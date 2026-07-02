"""Polling coordinator: BB inventory + assignments + live AMS layout -> SpoolModel map.

Also owns the two orchestration primitives that are more than a raw REST call:
  - resolve_tag_fresh: client-side tag->spool (no /spoolbuddy broadcast), refresh-once
    fallback so a just-bound tag resolves.
  - relocate_assign: clear the spool's PRIOR slot(s) before assigning (BB's unique key is
    (printer,ams,tray) only, so BB won't do it) => a spool can't sit in two slots.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bambuddy.rest_client import BambuddyApiError, BambuddyRestClient
from .const import DOMAIN
from .brain.inventory import (
    SpoolModel,
    TrayState,
    canonical_tag,
    derive_slots,
    map_inventory,
    parse_ams,
    resolve_tag,
)

_LOGGER = logging.getLogger(__name__)


class SpoolTapCoordinator(DataUpdateCoordinator[dict[int, SpoolModel]]):
    """Polls the Bambuddy inventory read-path on an interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        rest: BambuddyRestClient,
        *,
        update_interval_seconds: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="spooltap inventory",
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.rest = rest
        self.printers: list[dict] = []
        self.printer_id: int = 1
        self.tray_states: dict[tuple[int, int], TrayState] = {}
        self.slots: list[dict] = []  # auto-derived AMS slot layout from BB (persists last-known)
        # slot->tag registry (SoA): the one thing BB can't store. Persisted here (ships
        # EMPTY; the user binds slot tags in the Bind area). Keyed by slot key '<ams>_<tray>'.
        self.slot_tags: dict[str, str] = {}
        self._store: Store = Store(hass, 1, f"{DOMAIN}_slot_tags_{entry.entry_id}")
        self._assign_lock = asyncio.Lock()  # serialize relocate_assign (list->clear->assign)

    async def async_load_slot_tags(self) -> None:
        loaded = (await self._store.async_load()) or {}
        # migrate pre-canonical registries: bindings were once stored as the raw HA
        # tag_id, but binds and the dispatch compare canonical now — canonicalize on
        # load so existing installs keep matching
        self.slot_tags = {k: canonical_tag(v) for k, v in loaded.items() if v}

    async def async_save_slot_tags(self) -> None:
        await self._store.async_save(self.slot_tags)
        self.async_update_listeners()  # re-render the slots sensor

    async def _async_update_data(self) -> dict[int, SpoolModel]:
        try:
            spools = await self.rest.list_spools()
            try:
                assignments = await self.rest.list_assignments()
            except Exception as err:  # noqa: BLE001 - slot join is best-effort
                _LOGGER.debug("assignments poll failed (slots omitted): %s", err)
                assignments = []
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"inventory poll failed: {err}") from err
        # best-effort live AMS layout (offline -> keep last-good, used for slot picker +
        # material-mismatch + mid-print guard). Never fails the inventory poll.
        try:
            printers = await self.rest.get_printers()
            if printers:
                self.printers = printers
                self.printer_id = int(printers[0].get("id") or self.printer_id)
                status = await self.rest.get_printer_status(self.printer_id)
                if status is not None:
                    self.tray_states = parse_ams(status)
                    labels = await self.rest.get_ams_labels(self.printer_id)
                    derived = derive_slots(status, labels)
                    if derived:  # only overwrite when BB actually returned a layout
                        self.slots = derived
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("AMS layout poll failed: %s", err)
        return map_inventory(spools, assignments)

    # ------------------------------------------------------------- orchestration
    def resolve_tag_local(self, tag_uid: str) -> int | None:
        return resolve_tag(self.data or {}, tag_uid)

    async def resolve_tag_fresh(self, tag_uid: str) -> int | None:
        """Client-side resolve; if unseen, poll once (a tag bound seconds ago)."""
        spool_id = self.resolve_tag_local(tag_uid)
        if spool_id is None:
            await self.async_refresh()
            spool_id = self.resolve_tag_local(tag_uid)
        return spool_id

    async def relocate_assign(
        self, spool_id: int, printer_id: int, ams_id: int, tray_id: int
    ) -> dict:
        """Clear the spool's prior slot(s), then assign. BB's assign auto-configures
        the physical slot (profile+K+color) or defers via pending_config.

        The clear must SUCCEED before we assign — proceeding after a failed clear
        would leave the spool occupying two slots, the exact thing this exists to
        prevent — so a clear failure aborts. A 404 on the delete is fine (BB
        auto-removes assignments on tray fingerprint mismatch)."""
        target = (printer_id, ams_id, tray_id)
        async with self._assign_lock:
            try:
                for a in await self.rest.list_assignments():
                    if a.get("spool_id") == spool_id and (
                        a.get("printer_id"),
                        a.get("ams_id"),
                        a.get("tray_id"),
                    ) != target:
                        try:
                            await self.rest.unassign(
                                a["printer_id"], a["ams_id"], a["tray_id"]
                            )
                        except BambuddyApiError as err:
                            if err.status != 404:  # already gone == cleared
                                raise
            except HomeAssistantError:
                raise
            except Exception as err:  # noqa: BLE001
                raise HomeAssistantError(
                    f"Assign aborted: could not clear spool {spool_id}'s prior "
                    f"slot ({err}); the spool would occupy two slots."
                ) from err
            result = await self.rest.assign_slot(spool_id, printer_id, ams_id, tray_id)
        await self.async_refresh()
        return result
