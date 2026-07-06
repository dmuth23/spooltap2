"""SpoolTapFlows — the Assign/Bind/Modify workflow controller.

A 1:1 Python port of the Hybrid YAML package (`ha/packages/spooltap_v2.yaml`): the
two-tap NFC gesture (capture + dispatch state machine), the staging fields the flows
read (facet pickers, tag input, modify form), and every button action. State lives
HERE — the select/text/number/switch/button/sensor entities are thin views that
subscribe to one dispatcher signal and re-render.

Event-driven, no helper round-trip: `tag_scanned` goes straight into the dispatch
(the package's clear-then-set input_text hack is obsolete). BB writes go through the
same coordinator/REST paths the `spooltap.*` services use; failures land in the
status line (never a dead listener, never a result stuck at Working).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .bambuddy.rest_client import BambuddyApiError
from .brain.inventory import canonical_tag
from .coordinator import SpoolTapCoordinator

if TYPE_CHECKING:
    from .brain.inventory import SpoolModel

_LOGGER = logging.getLogger(__name__)

PENDING_TIMEOUT = 300  # seconds — the package's 5-min pending-slot timeout

# The status sensor's state vocabulary (v0.3.0): the sensor state IS the level;
# the human message lives in the sensor's `message` attribute (no 255-char limit).
STATUS_LEVELS = [
    "Idle",      # nothing has happened yet
    "Ready",     # a preview / prompt — the next tap commits
    "Armed",     # a slot is armed, waiting for a spool tag
    "Working",   # a Bambuddy write is in flight
    "Success",   # the last action committed
    "Warning",   # blocked / needs input / timed out — nothing was changed
    "Error",     # the last action failed
    "Info",      # neutral state change (opened/closed/refreshed)
]

MODE_OPTIONS = ["Assign", "Bind", "Modify", "Spools"]
BIND_POOL_OPTIONS = ["Available", "All", "Assigned"]
# same base list the package's rebuild script used (superset of the input_select seed)
BASE_MATERIALS = [
    "PLA", "PLA+", "PETG", "PETG-CF", "PLA-CF", "PA", "PA-CF", "PAHT-CF",
    "PET-GF", "PEBA", "ABS", "ASA", "TPU", "PC", "PVA", "HIPS",
]

# select key -> placeholder (the fallback when a selection disappears from options)
PLACEHOLDERS: dict[str, str] = {
    "mode": "Assign",
    "assign_slot": "Select…",
    "assign_brand": "Any",
    "assign_type": "Any",
    "assign_spool": "Select…",
    "bind_pool": "Available",
    "bind_brand": "Any",
    "bind_type": "Any",
    "bind_spool": "Select…",
    "bind_tag": "Select…",
    "bind_slot": "Select…",
    "mod_open_tag": "Any",
    "mod_open_spool": "Select…",
    "mod_material": "Unknown",
}
# facet keys whose change re-narrows the dependent pickers
_FACET_KEYS = ("bind_pool", "bind_brand", "bind_type", "assign_brand", "assign_type")

_SPOOL_ID_RE = re.compile(r"^#(\d+)")
# BB failure shapes the flows report instead of raising (transport + API + wrapped)
_BB_ERRORS = (HomeAssistantError, BambuddyApiError, ClientError, asyncio.TimeoutError)


def _parse_spool_id(option: str) -> int:
    """'#12 — Brand Color (PLA)' -> 12; anything else -> 0 (the package's regex)."""
    match = _SPOOL_ID_RE.match(option or "")
    return int(match.group(1)) if match else 0


def _bb_error_text(err: Exception) -> str:
    """Human text for a failed Bambuddy call — never the raw exception repr.

    BambuddyApiError carries BB's own `detail` (already human, no host in it);
    transport errors (DNS, refused, timeout) get one generic line — their raw text
    can embed the Bambuddy host/port and belongs in the log, not on the status card.
    """
    if isinstance(err, BambuddyApiError):
        return f"Bambuddy rejected it ({err.status}): {str(err.detail)[:140]}"
    if isinstance(err, HomeAssistantError):
        return str(err)
    if isinstance(err, asyncio.TimeoutError):
        return "Bambuddy timed out — check the add-on is running, then retry."
    return "Bambuddy is unreachable — check the add-on is running, then retry."


class SpoolTapFlows:
    """Controller owned by the config entry; entities render its state."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: SpoolTapCoordinator
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.signal = f"spooltap_flows_{entry.entry_id}"
        # gesture / result state (was: stv2_last_scanned / _pending_slot / _status /
        # _assign_result / _bind_mode / _mod_spool_id)
        self.last_scanned: str = ""
        self.pending_slot_key: str | None = None
        self._pending_timer: CALLBACK_TYPE | None = None
        self.loaded_spool_id: int = 0
        self.status: str = ""
        self.status_level: str = "Idle"
        self.status_updated = None  # datetime | None — set on every _set_status
        self.busy: bool = False  # a BB write is in flight (double-press guard)
        self.assign_result: str = "Idle"
        self.bind_mode: bool = False
        # staging fields the flows read (was: the stv2_* input helpers)
        self.selections: dict[str, str] = dict(PLACEHOLDERS)
        self.options: dict[str, list[str]] = {k: [v] for k, v in PLACEHOLDERS.items()}
        self.texts: dict[str, str] = {"mod_name": "", "tag_input": ""}
        self.numbers: dict[str, float] = {"mod_core": 250.0, "mod_gross": 0.0, "mod_net": 0.0}
        # {canonical tag -> HA registry friendly name}; rebuilt each recompute so the pickers
        # can show the physical label the user reads off the spool. Empty on a virgin system.
        self._tag_names: dict[str, str] = {}
        # serialize scan handling — the YAML dispatch was mode:queued; without this,
        # two rapid scans race (double-assign to one pending, arm/clear interleave)
        self._dispatch_lock = asyncio.Lock()

    # ------------------------------------------------------------ lifecycle
    @callback
    def async_setup(self) -> None:
        """Wire the tag_scanned listener + coordinator recompute; entry unload tears down."""
        self._recompute_options()
        self.entry.async_on_unload(
            self.hass.bus.async_listen("tag_scanned", self._async_tag_scanned)
        )
        self.entry.async_on_unload(
            self.coordinator.async_add_listener(self._coordinator_updated)
        )
        self.entry.async_on_unload(self._cancel_pending_timer)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def _coordinator_updated(self) -> None:
        self._recompute_options()
        self._notify()

    @callback
    def _set_status(self, level: str, message: str) -> None:
        """The one writer for the status line: level + message + freshness stamp."""
        self.status_level = level if level in STATUS_LEVELS else "Info"
        self.status = message
        self.status_updated = dt_util.utcnow()

    def _tag_display(self, tag: str) -> str:
        """The name the user reads off the physical tag (HA registry friendly name),
        falling back to the UID tail — every status message names tags this way."""
        name = self._tag_names.get(canonical_tag(tag))
        return name or f"tag …{tag[-8:].upper()}"

    # ------------------------------------------------------------ derived views
    @property
    def pending_slot_label(self) -> str | None:
        if not self.pending_slot_key:
            return None
        slot = self._slot_by_key(self.pending_slot_key)
        return slot["label"] if slot else self.pending_slot_key

    @property
    def loaded_spool(self) -> SpoolModel | None:
        return (self.coordinator.data or {}).get(self.loaded_spool_id)

    def _spool_desc(self, spool_id: int) -> str:
        """'spool #12 (Polymaker Orange PLA, 612g left)' — name + weight so the
        physical spool in hand can be checked against what the scan resolved to."""
        spool = (self.coordinator.data or {}).get(spool_id)
        if spool is None:
            return f"spool #{spool_id}"
        return (
            f"spool #{spool_id} ({spool.display_name},"
            f" {round(spool.remaining_grams)}g left)"
        )

    def _role_for_tag(self, tag: str) -> tuple[str, str]:
        """Classify an ARBITRARY tag: role none/slot/spool/unbound + a human detail
        (spool wins over slot). The tag-in-hand sensor and the bind preview share this so
        the preview classifies the tag Bind will actually commit, not just the last scan."""
        want = canonical_tag(tag)
        if not want:
            return ("none", "")
        role, detail = "unbound", ""
        if slot := self._slot_for_tag(tag):
            role, detail = "slot", slot["label"]
        for spool_id, model in (self.coordinator.data or {}).items():
            if model.tag_uid and canonical_tag(model.tag_uid) == want:
                role, detail = "spool", self._spool_desc(spool_id)
        return role, detail

    def tag_role_detail(self) -> tuple[str, str]:
        """The tag-in-hand classification (port of the STV2 Tag In Hand template)."""
        return self._role_for_tag(self.last_scanned)

    # ------------------------------------------------------------ slot lookups
    def _slot_by_key(self, key: str | None) -> dict | None:
        return next((s for s in self.coordinator.slots if s["key"] == key), None)

    def _slot_by_label(self, label: str) -> dict | None:
        return next((s for s in self.coordinator.slots if s["label"] == label), None)

    def _slot_for_tag(self, tag: str) -> dict | None:
        """Match a scan against the slot->tag registry (both sides canonical)."""
        want = canonical_tag(tag)
        if not want:
            return None
        registry = self.coordinator.slot_tags
        return next(
            (
                s
                for s in self.coordinator.slots
                if canonical_tag(registry.get(s["key"]) or "") == want
            ),
            None,
        )

    # ------------------------------------------------------------ entity inputs
    async def async_select(self, key: str, option: str) -> None:
        """A select view changed. Side effects mirror the package automations:
        mode away from Bind -> bind mode off; facet change -> re-narrow;
        Modify open pickers -> load the spool."""
        self.selections[key] = option
        if key == "mode" and option != "Bind":
            self.bind_mode = False  # port of stv2_bind_mode_autooff
        if key in _FACET_KEYS:
            self._recompute_options()
        if key in ("mod_open_spool", "mod_open_tag") and option not in (
            "Select…", "Any", "",
        ):
            spool_id = _parse_spool_id(option)  # port of stv2_mod_pick_load
            if spool_id > 0:
                self._mod_load(spool_id)
        # keep the status line == what Bind will commit whenever a bind input changes
        if self.bind_mode and key in ("bind_spool", "bind_tag", "bind_slot"):
            self._bind_preview()
        self._notify()

    @callback
    def async_set_restored_mode(self, option: str) -> None:
        """RestoreEntity path for select.spooltap_mode — no side effects."""
        if option in MODE_OPTIONS:
            self.selections["mode"] = option

    @callback
    def async_set_bind_mode(self, on: bool) -> None:
        self.bind_mode = on
        if on:
            # entering Bind: drop any stale registry pick / paste so a 42-min-old selection
            # can't silently outrank the tag about to be scanned (the #51 misfire), then preview
            self.selections["bind_tag"] = PLACEHOLDERS["bind_tag"]
            self.texts["tag_input"] = ""
            self._bind_preview()
        self._notify()

    @callback
    def async_set_text(self, key: str, value: str) -> None:
        self.texts[key] = value
        if key == "tag_input" and self.bind_mode:
            self._bind_preview()  # the pasted-UID path must preview too (display == action)
        self._notify()

    @callback
    def async_set_number(self, key: str, value: float) -> None:
        self.numbers[key] = float(value)
        # port of stv2_mod_gross_to_net: gross entered -> auto-fill net (= gross - tare),
        # clamped at 0 (a gross below the tare must not stage a negative remaining)
        if key == "mod_gross" and value > 0:
            self.numbers["mod_net"] = max(
                0.0, float(round(value - self.numbers.get("mod_core", 250.0)))
            )
        self._notify()

    # ------------------------------------------------------------ scan dispatch
    async def _async_tag_scanned(self, event: Event) -> None:
        """tag_scanned listener. Exception-proof: a bug or BB hiccup may cost one
        scan's outcome but can never kill the listener."""
        try:
            async with self._dispatch_lock:
                await self._dispatch_scan(str(event.data.get("tag_id") or ""))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("SpoolTap: tag_scanned dispatch failed")

    async def _dispatch_scan(self, tag: str) -> None:
        """Port of stv2_capture_scan + stv2_dispatch, same branch order:
        bind mode -> slot tag -> pending armed -> resolve (modify / unrecognized)."""
        if not tag:
            return
        self.last_scanned = tag  # identical re-scan just re-runs (no clear-then-set)
        if self.bind_mode:
            # a fresh scan is the authoritative tag: drop any stale registry pick / paste so it
            # can't silently outrank the scan (the #51 misfire), then preview what Bind will do
            self.selections["bind_tag"] = PLACEHOLDERS["bind_tag"]
            self.texts["tag_input"] = ""
            self._bind_preview()
            self._notify()
            return
        if slot := self._slot_for_tag(tag):
            self.pending_slot_key = slot["key"]
            self.assign_result = "Awaiting Spool Tag"
            self._set_status(
                "Armed",
                f"{slot['label']} armed — now tap a spool's tag to assign it there.",
            )
            self._start_pending_timer()
            self._notify()
            return
        if self.pending_slot_key:
            await self.async_assign_from_pending(tag)
            return
        # no pending -> load into Modify (best-effort), else point at Bind mode
        spool_id = await self.coordinator.resolve_tag_fresh(tag)
        if spool_id is not None:
            self._mod_load(spool_id)
            self._set_status(
                "Info",
                f"Opened {self._spool_desc(spool_id)} in Modify — edit below, then Save.",
            )
        else:
            self._set_status(
                "Warning",
                f"{self._tag_display(tag)} isn't bound to anything — "
                "switch to Bind mode to register it.",
            )
        self._notify()

    # ------------------------------------------------------------ pending timer
    def _start_pending_timer(self) -> None:
        self._cancel_pending_timer()
        self._pending_timer = async_call_later(
            self.hass, PENDING_TIMEOUT, self._pending_timeout
        )

    @callback
    def _cancel_pending_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer()
            self._pending_timer = None

    @callback
    def _pending_timeout(self, _now) -> None:
        """Port of stv2_pending_timeout."""
        self._pending_timer = None
        self.pending_slot_key = None
        self.assign_result = "Idle"
        self._set_status(
            "Warning",
            "Armed slot timed out (5 min) — re-scan the slot tag to start over.",
        )
        self._notify()

    def _clear_pending(self) -> None:
        self._cancel_pending_timer()
        self.pending_slot_key = None

    # ------------------------------------------------------------ assign actions
    async def async_assign_manual(self) -> None:
        """Port of stv2_assign_manual (the dropdown path)."""
        disp = self.selections["assign_slot"]
        slot = self._slot_by_label(disp)
        spool_id = _parse_spool_id(self.selections["assign_spool"])
        if slot is None or spool_id < 1:
            self._set_status(
                "Warning", "Nothing to assign yet — pick both a slot and a spool first."
            )
            self._notify()
            return
        if self.busy:
            return
        self.busy = True
        self.assign_result = "Working"
        desc = self._spool_desc(spool_id)
        self._set_status("Working", f"Assigning {desc} → {disp}…")
        self._notify()
        try:
            await self.coordinator.relocate_assign(
                spool_id, self.coordinator.printer_id, slot["ams_id"], slot["tray_id"]
            )
        except _BB_ERRORS as err:
            _LOGGER.warning("manual assign failed: %s", err)
            self.assign_result = "Failed"
            self._set_status("Error", f"Assign failed — {_bb_error_text(err)}")
            self._notify()
            return
        finally:
            self.busy = False
        self.assign_result = "Success"
        self._set_status(
            "Success",
            f"Assigned {desc} → {disp}. "
            "The tray auto-configures when the printer is online.",
        )
        self._clear_pending()
        self._notify()

    async def async_assign_from_pending(self, tag: str) -> None:
        """Port of stv2_assign_from_pending (the two-tap path). The tag arrives as an
        ARGUMENT from the dispatch — never re-read from last_scanned (a queued scan
        could overwrite it in between). On failure the slot stays armed."""
        slot = self._slot_by_key(self.pending_slot_key)
        spool_id = await self.coordinator.resolve_tag_fresh(tag)
        if spool_id is None or slot is None:
            self._set_status(
                "Warning",
                "Unknown spool tag — bind it first (Bind mode). "
                "The slot is still armed: re-scan a spool, or Cancel.",
            )
            self._notify()
            return
        desc = self._spool_desc(spool_id)
        if self.busy:
            return
        self.busy = True
        self.assign_result = "Working"
        self._set_status("Working", f"Assigning {desc} → {slot['label']}…")
        self._notify()
        try:
            await self.coordinator.relocate_assign(
                spool_id, self.coordinator.printer_id, slot["ams_id"], slot["tray_id"]
            )
        except _BB_ERRORS as err:
            _LOGGER.warning("tap assign failed: %s", err)
            self.assign_result = "Failed"
            self._set_status(
                "Error",
                f"Assign failed — {_bb_error_text(err)} "
                "The slot is still armed: fix and re-scan, or Cancel.",
            )
            self._notify()
            return
        finally:
            self.busy = False
        self.assign_result = "Success"
        self._set_status(
            "Success",
            f"Assigned {desc} → {slot['label']}. "
            "The tray auto-configures when the printer is online.",
        )
        self._clear_pending()
        self._notify()

    async def async_cancel_assign(self) -> None:
        """Port of stv2_cancel_assign."""
        self._clear_pending()
        self.last_scanned = ""
        self.assign_result = "Cancelled"
        self._set_status(
            "Info",
            "Assignment cancelled — scan a slot tag to start again, or use the dropdowns.",
        )
        self._notify()

    # ------------------------------------------------------------ bind actions
    def _tag_for_bind(self) -> str:
        """Tag precedence, port of the bind scripts: registry pick -> typed -> last scan.
        A bind-mode scan now clears the pick/typed fields first, so a stale pick can no
        longer outrank a fresh scan; an EXPLICIT post-scan pick still wins, by design."""
        picked = self.selections["bind_tag"]
        if picked != "Select…" and "[" in picked:
            return picked.split("[")[1].rstrip("]")
        if typed := self.texts["tag_input"]:
            return typed
        return self.last_scanned

    def _bind_preview(self) -> None:
        """Set the status to EXACTLY what pressing Bind will commit — same tag source the
        button uses (`_tag_for_bind`) and that tag's role — so the display can never disagree
        with the action (the root of the #51 misfire). Target-aware: a slot/tray tag headed
        for a spool is flagged as blocked; a spool tag headed for a slot is flagged as a move."""
        tag = self._tag_for_bind()
        if not tag:
            self._set_status(
                "Ready",
                "Bind mode: scan a tag, pick one from the registry, or paste a UID.",
            )
            return
        name = self._tag_display(tag)
        role, detail = self._role_for_tag(tag)
        spool_id = _parse_spool_id(self.selections["bind_spool"])
        slot = self._slot_by_label(self.selections["bind_slot"])
        warn = ""
        if spool_id > 0:
            tgt = f"spool #{spool_id}"
            if role == "slot":
                warn = f"  that tag is registered to {detail} — binding it to a spool is blocked"
            elif role == "spool":
                warn = f"  already on {detail}; Bind will move it here"
        elif slot is not None:
            tgt = slot["label"]
            if role == "spool":
                warn = f"  that tag is on {detail}; recycle it off the spool first"
        else:
            tgt = "— pick a spool or a slot below"
        self._set_status(
            "Warning" if warn else "Ready",
            f"Ready: bind {name} → {tgt}. Press Bind to confirm.{warn}",
        )

    async def async_bind_spool(self) -> None:
        """Port of stv2_bind_spool — same code path as spooltap.recycle_tag (fresh
        client-side source resolve, then the rollback-safe recycle)."""
        tag = self._tag_for_bind()
        spool_id = _parse_spool_id(self.selections["bind_spool"])
        if not tag or spool_id < 1:
            self._set_status(
                "Warning", "Bind needs both: a tag (scan, pick, or paste) and a spool."
            )
            self._notify()
            return
        # guard: a tag registered to an AMS slot/tray must not also become a spool tag
        # (the #51 misfire — a stale slot-tag pick bound onto a spool)
        want = canonical_tag(tag)
        slot = next(
            (
                s
                for s in self.coordinator.slots
                if want and canonical_tag(self.coordinator.slot_tags.get(s["key"]) or "") == want
            ),
            None,
        )
        if slot is not None:
            self._set_status(
                "Warning",
                f"That tag is registered to {slot['label']} (a slot/tray tag). "
                "Unbind the slot first, or use a different tag.",
            )
            self._notify()
            return
        if self.busy:
            return
        self.busy = True
        name = self._tag_display(tag)
        self._set_status("Working", f"Binding {name} → spool #{spool_id}…")
        self._notify()
        try:
            source = await self.coordinator.resolve_tag_fresh(tag)
            status = await self.coordinator.rest.recycle_tag(tag, spool_id, source)
            await self.coordinator.async_refresh()
            if status == 409:
                raise HomeAssistantError(
                    "the tag is on another active spool that could not be "
                    "identified and cleared safely"
                )
        except _BB_ERRORS as err:
            _LOGGER.warning("bind spool failed: %s", err)
            self._set_status("Error", f"Bind failed — {_bb_error_text(err)}")
            self._notify()
            return
        finally:
            self.busy = False
        self._set_status("Success", f"Bound {name} → spool #{spool_id}.")
        self.texts["tag_input"] = ""
        self._notify()

    async def async_bind_slot(self) -> None:
        """Port of stv2_bind_slot — same registry write as spooltap.bind_slot_tag
        (canonical storage, one-tag-one-slot eviction, persisted)."""
        tag = self._tag_for_bind()
        slot = self._slot_by_label(self.selections["bind_slot"])
        if not tag or slot is None:
            self._set_status("Warning", "Bind needs both: a tag and a slot.")
            self._notify()
            return
        # symmetric guard: a tag that's a live spool tag must not also become a slot tag
        spool_id = self.coordinator.resolve_tag_local(tag)
        if spool_id is not None:
            self._set_status(
                "Warning",
                f"That tag is bound to spool #{spool_id}. "
                "Recycle it off the spool first, or use a different tag for the slot.",
            )
            self._notify()
            return
        co = self.coordinator
        want = canonical_tag(tag.strip())
        co.slot_tags = {
            k: v for k, v in co.slot_tags.items() if canonical_tag(v) != want or not want
        }
        if want:
            co.slot_tags[slot["key"]] = want
        else:
            co.slot_tags.pop(slot["key"], None)
        await co.async_save_slot_tags()
        self._set_status("Success", f"Bound {self._tag_display(tag)} → {slot['label']}.")
        self.texts["tag_input"] = ""
        self._notify()

    # ------------------------------------------------------------ modify actions
    @callback
    def _mod_load(self, spool_id: int) -> None:
        """Port of stv2_mod_load: populate the Modify form from the loaded spool.
        None-safe defaults (core 250, net 0) — the model's dict.get pitfall applies."""
        if spool_id < 1:
            return
        spool = (self.coordinator.data or {}).get(spool_id)
        material = (spool.material if spool else None) or "Unknown"
        self.loaded_spool_id = spool_id
        self.texts["mod_name"] = (spool.color_name if spool else None) or ""
        self.selections["mod_material"] = (
            material if material in self.options.get("mod_material", []) else "Unknown"
        )
        self.numbers["mod_core"] = float(spool.core_weight) if spool else 250.0
        remaining = float(spool.remaining_grams) if spool else 0.0
        self.numbers["mod_net"] = float(round(remaining))
        self.numbers["mod_gross"] = 0.0
        display = ((spool.color_name or spool.material) if spool else None) or "?"
        self._set_status(
            "Info",
            f"Editing #{spool_id} — {display} · {material} · {int(remaining)}g left.",
        )

    async def async_mod_save(self) -> None:
        """Port of stv2_mod_save. One Save commits weight + name + material:
        gross > 0 -> BB's weigh-in (tare math + last_weighed_at) then weight lock;
        gross = 0 -> commit Net as weight_used (BB auto-locks server-side)."""
        sid = self.loaded_spool_id
        if sid < 1:
            return
        if self.busy:
            return
        mat = self.selections["mod_material"]
        net = float(self.numbers["mod_net"])
        gross = float(self.numbers["mod_gross"])
        spool = (self.coordinator.data or {}).get(sid)
        label = float(spool.label_weight) if spool else 1000.0
        weight_used = round(label - net, 2)
        rest = self.coordinator.rest
        self.busy = True
        self._set_status("Working", f"Saving #{sid}…")
        self._notify()
        try:
            if gross > 0:
                await rest.weigh_spool(sid, gross)
                await rest.modify_spool(sid, weight_locked=True)
            else:
                await rest.modify_spool(sid, weight_used=weight_used)
            if mat != "Unknown":
                await rest.modify_spool(
                    sid, color_name=self.texts["mod_name"], material=mat
                )
            else:
                await rest.modify_spool(sid, color_name=self.texts["mod_name"])
        except _BB_ERRORS as err:
            _LOGGER.warning("modify save failed: %s", err)
            await self.coordinator.async_refresh()  # reflect any partial commit
            self._set_status("Error", f"Save failed — {_bb_error_text(err)}")
            self._notify()
            return
        finally:
            self.busy = False
        await self.coordinator.async_refresh()
        # reset gross so a later direct Net edit doesn't silently re-take the weigh-in path
        self.numbers["mod_gross"] = 0.0
        detail = (
            f" · recertified from {int(round(gross))}g gross"
            if gross > 0
            else f" · {int(round(net))}g remaining"
        )
        suffix = f" · {mat}" if mat != "Unknown" else ""
        self._set_status(
            "Success", f"Saved #{sid} — {self.texts['mod_name']}{detail}{suffix}"
        )
        self._notify()

    async def async_mod_archive(self) -> None:
        """Port of stv2_mod_archive (the dashboard keeps its confirmation dialog)."""
        sid = self.loaded_spool_id
        if sid < 1:
            return
        if self.busy:
            return
        self.busy = True
        self._set_status("Working", f"Archiving #{sid}…")
        self._notify()
        try:
            await self.coordinator.rest.archive_spool(sid)
        except _BB_ERRORS as err:
            _LOGGER.warning("archive failed: %s", err)
            self._set_status("Error", f"Archive failed — {_bb_error_text(err)}")
            self._notify()
            return
        finally:
            self.busy = False
        await self.coordinator.async_refresh()
        self.loaded_spool_id = 0
        self._set_status("Success", f"Archived #{sid} — its tag is free to recycle.")
        self._notify()

    async def async_mod_close(self) -> None:
        """Port of stv2_mod_close."""
        self.loaded_spool_id = 0
        self.selections["mod_open_spool"] = "Select…"
        self.selections["mod_open_tag"] = "Any"
        self._set_status("Info", "Closed the modify panel.")
        self._notify()

    async def async_refresh_action(self) -> None:
        """Port of stv2_refresh: re-poll BB, reset the facets, rebuild every picker."""
        await self.coordinator.async_refresh()
        for key in ("bind_brand", "bind_type", "assign_brand", "assign_type"):
            self.selections[key] = "Any"
        self._recompute_options()
        self._set_status("Info", "Refreshed from Bambuddy.")
        self._notify()

    # ------------------------------------------------------------ picker options
    def _spools_snapshot(self) -> list[dict]:
        """The rebuild script's `spools` variable, from the coordinator's inventory."""
        return [
            {
                "id": m.spool_id,
                "brand": m.brand,
                "material": m.material,
                "color": m.color_name,
                "taguid": m.tag_uid,
                "tag": bool(m.tag_uid),
                "assigned": bool(m.assigned_slot),
            }
            for m in (self.coordinator.data or {}).values()
        ]

    def _spool_label(self, s: dict) -> str:
        # short hyphen (was an em-dash) + the HA tag's friendly name when the spool has a tag,
        # so the option matches the physical label the user reads off the spool. Prefix
        # "#<id>" is preserved verbatim so _parse_spool_id still extracts the id.
        label = f"#{s['id']} - {s['brand'] or ''} {s['color'] or s['material']} ({s['material']})"
        if s["tag"]:
            name = self._tag_names.get(canonical_tag(s["taguid"] or ""))
            label += f"  ·  {name}" if name else "  ·  (tag not in HA)"
        return label

    def _fallback(self, key: str) -> None:
        """If the current selection vanished from options, fall back to the placeholder."""
        if self.selections[key] not in self.options[key]:
            self.selections[key] = PLACEHOLDERS[key]

    @callback
    def _recompute_options(self) -> None:
        """Port of stv2_rebuild_pickers (+ its automation): recompute every picker's
        options from the coordinator. Narrowing order matters — a brand falling back
        to Any must widen the type/spool computed after it."""
        self._tag_names = self._tag_names_by_canonical()  # for the enriched spool/tag labels
        spools = self._spools_snapshot()
        opts = self.options
        opts["mode"] = MODE_OPTIONS
        opts["bind_pool"] = BIND_POOL_OPTIONS
        pool = self.selections["bind_pool"]
        bindset = [
            s
            for s in spools
            if pool == "All"
            or (pool == "Available" and not s["tag"])
            or (pool == "Assigned" and s["tag"])
        ]
        asgset = [s for s in spools if not s["assigned"]]
        materials = sorted(
            {m for m in BASE_MATERIALS + [s["material"] for s in spools] if m}
        )
        # Bind facets: brand = all in pool; type narrowed by brand; spool = brand+type
        opts["bind_brand"] = ["Any"] + sorted({s["brand"] for s in bindset if s["brand"]})
        self._fallback("bind_brand")
        brand = self.selections["bind_brand"]
        opts["bind_type"] = ["Any"] + sorted(
            {
                s["material"]
                for s in bindset
                if (brand == "Any" or s["brand"] == brand) and s["material"]
            }
        )
        self._fallback("bind_type")
        mat = self.selections["bind_type"]
        opts["bind_spool"] = ["Select…"] + [
            self._spool_label(s)
            for s in bindset
            if (brand == "Any" or s["brand"] == brand)
            and (mat == "Any" or s["material"] == mat)
        ]
        self._fallback("bind_spool")
        # Assign facets: brand = all unassigned; type narrowed by brand; spool = brand+type
        opts["assign_brand"] = ["Any"] + sorted({s["brand"] for s in asgset if s["brand"]})
        self._fallback("assign_brand")
        brand = self.selections["assign_brand"]
        opts["assign_type"] = ["Any"] + sorted(
            {
                s["material"]
                for s in asgset
                if (brand == "Any" or s["brand"] == brand) and s["material"]
            }
        )
        self._fallback("assign_type")
        mat = self.selections["assign_type"]
        opts["assign_spool"] = ["Select…"] + [
            self._spool_label(s)
            for s in asgset
            if (brand == "Any" or s["brand"] == brand)
            and (mat == "Any" or s["material"] == mat)
        ]
        self._fallback("assign_spool")
        # Modify: spool (all) / tag (tagged) / material (full dynamic list)
        opts["mod_open_spool"] = ["Select…"] + [self._spool_label(s) for s in spools]
        self._fallback("mod_open_spool")
        opts["mod_open_tag"] = ["Any"] + [
            f"#{s['id']} - {s['color'] or s['material']}  ·  "
            f"{self._tag_names.get(canonical_tag(s['taguid'] or '')) or '(tag not in HA)'}"
            for s in spools
            if s["tag"]
        ]
        self._fallback("mod_open_tag")
        opts["mod_material"] = ["Unknown"] + materials
        self._fallback("mod_material")
        # Tag registry picker (no-scan bind) + the two slot pickers
        opts["bind_tag"] = ["Select…"] + self._tag_registry_options()
        self._fallback("bind_tag")
        slot_labels = [s["label"] for s in self.coordinator.slots]
        opts["assign_slot"] = ["Select…"] + slot_labels
        self._fallback("assign_slot")
        opts["bind_slot"] = ["Select…"] + slot_labels
        self._fallback("bind_slot")

    def _tag_names_by_canonical(self) -> dict[str, str]:
        """{canonical_tag: friendly_name} for every HA registry tag, so a spool's tag_uid can
        show the physical label the user reads off the spool. Same source as
        `_tag_registry_options`; returns {} on a virgin system (no `tag` data) — never raises."""
        out: dict[str, str] = {}
        try:
            items = self.hass.data["tag"].async_items()
            registry = er.async_get(self.hass)
            for item in items:
                tag_id = str(item.get("id") or "")
                if not tag_id:
                    continue
                name = None
                if entity_id := registry.async_get_entity_id("tag", "tag", tag_id):
                    if entry := registry.async_get(entity_id):
                        name = entry.name or entry.original_name
                out[canonical_tag(tag_id)] = name or f"Tag {tag_id[-8:]}"
        except Exception:  # noqa: BLE001 - storage shape changed -> states fallback
            for state in self.hass.states.async_all("tag"):
                tag_id = str(state.attributes.get("tag_id") or "")
                if tag_id:
                    out[canonical_tag(tag_id)] = state.name or f"Tag {tag_id[-8:]}"
        return out

    def _tag_registry_options(self) -> list[str]:
        """HA tag registry entries as `name [tag_id]`.

        Primary: the tag component's storage collection (hass.data['tag'] is a
        DictStorageCollection whose items carry only `id`; names live in the entity
        registry — same join the tag WS list does). Fallback: iterate the `tag.`
        entity states, exactly what the YAML package did."""
        try:
            items = self.hass.data["tag"].async_items()
            registry = er.async_get(self.hass)
            out: list[str] = []
            for item in items:
                tag_id = str(item.get("id") or "")
                if not tag_id:
                    continue
                name = None
                if entity_id := registry.async_get_entity_id("tag", "tag", tag_id):
                    if entry := registry.async_get(entity_id):
                        name = entry.name or entry.original_name
                out.append(f"{name or f'Tag {tag_id}'} [{tag_id}]")
            return out
        except Exception:  # noqa: BLE001 - storage shape changed -> states fallback
            return [
                f"{state.name} [{state.attributes.get('tag_id', '')}]"
                for state in self.hass.states.async_all("tag")
            ]


@dataclass
class SpoolTapRuntime:
    """entry.runtime_data: the coordinator (data layer) + the flows controller."""

    coordinator: SpoolTapCoordinator
    flows: SpoolTapFlows
