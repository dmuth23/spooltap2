"""SpoolTap services — the tag/slot lifecycle over the Bambuddy REST API.

Registered once (domain-level). Writes refresh the inventory coordinator so HA reflects BB
immediately. BB stays the single source of truth; resolve is CLIENT-SIDE (no /spoolbuddy
broadcast); assign RELOCATES (clears the spool's prior slot) and reports BB's push status.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps

import voluptuous as vol
from aiohttp import ClientError
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .bambuddy.rest_client import BambuddyApiError
from .brain.inventory import SpoolModel, canonical_tag
from .const import (
    DOMAIN,
    SERVICE_ARCHIVE_SPOOL,
    SERVICE_ASSIGN_SLOT,
    SERVICE_BIND_SLOT_TAG,
    SERVICE_BIND_TAG,
    SERVICE_CLEAR_SLOT_TAG,
    SERVICE_CREATE_SPOOL,
    SERVICE_INSTALL_DASHBOARD,
    SERVICE_MODIFY_SPOOL,
    SERVICE_RECYCLE_TAG,
    SERVICE_REFRESH,
    SERVICE_RESOLVE_TAG,
    SERVICE_UNASSIGN_SLOT,
    SERVICE_WEIGH_SPOOL,
)
from .coordinator import SpoolTapCoordinator

_LOGGER = logging.getLogger(__name__)

RESOLVE_SCHEMA = vol.Schema({vol.Required("tag_uid"): cv.string})
BIND_SCHEMA = vol.Schema(
    {vol.Required("spool_id"): vol.Coerce(int), vol.Required("tag_uid"): cv.string}
)
RECYCLE_SCHEMA = vol.Schema(
    {
        vol.Required("tag_uid"): cv.string,
        vol.Required("target_spool_id"): vol.Coerce(int),
        vol.Optional("source_spool_id"): vol.Coerce(int),
    }
)
CREATE_SCHEMA = vol.Schema(
    {
        vol.Required("material"): cv.string,
        vol.Optional("color_name"): cv.string,
        vol.Optional("brand"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("tag_uid"): cv.string,
        vol.Optional("label_weight"): vol.Coerce(float),
        vol.Optional("note"): cv.string,
    }
)
ASSIGN_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): vol.Coerce(int),
        vol.Optional("printer_id"): vol.Coerce(int),  # default: BB's first printer
        vol.Required("ams_id"): vol.Coerce(int),
        vol.Required("tray_id"): vol.Coerce(int),
    }
)
UNASSIGN_SCHEMA = vol.Schema(
    {
        vol.Optional("printer_id"): vol.Coerce(int),  # default: BB's first printer
        vol.Required("ams_id"): vol.Coerce(int),
        vol.Required("tray_id"): vol.Coerce(int),
    }
)
MODIFY_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): vol.Coerce(int),
        vol.Optional("color_name"): cv.string,
        vol.Optional("material"): cv.string,
        vol.Optional("weight_used"): vol.Coerce(float),
        vol.Optional("weight_locked"): cv.boolean,
    }
)
WEIGH_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): vol.Coerce(int),
        vol.Required("gross_grams"): vol.Coerce(float),
    }
)
ARCHIVE_SCHEMA = vol.Schema({vol.Required("spool_id"): vol.Coerce(int)})


def _bb_errors(
    func: Callable[[ServiceCall], Awaitable[ServiceResponse | None]],
) -> Callable[[ServiceCall], Awaitable[ServiceResponse | None]]:
    """Surface Bambuddy failures as service errors, not raw tracebacks.

    Covers transport errors too (connection drop, timeout) — scripts rely on
    continue_on_error, which only suppresses HomeAssistantError."""

    @wraps(func)
    async def wrapper(call: ServiceCall) -> ServiceResponse | None:
        try:
            return await func(call)
        except BambuddyApiError as err:
            raise HomeAssistantError(f"Bambuddy request failed: {err}") from err
        except (ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Bambuddy unreachable: {err}") from err

    return wrapper


def _coordinator(hass: HomeAssistant) -> SpoolTapCoordinator:
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "coordinator", None)
        if coordinator is not None:
            return coordinator
    raise HomeAssistantError("SpoolTap: no loaded config entry")


def _model_dict(model: SpoolModel | None) -> dict | None:
    if model is None:
        return None
    return {
        "spool_id": model.spool_id,
        "material": model.material,
        "color_name": model.color_name,
        "brand": model.brand,
        "display_name": model.display_name,
        "remaining_grams": model.remaining_grams,
        "tag_uid": model.tag_uid,
        "assigned_slot": model.assigned_slot,
    }


def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_BIND_TAG):
        return

    async def resolve_tag(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        spool_id = await coordinator.resolve_tag_fresh(call.data["tag_uid"])
        spool = (coordinator.data or {}).get(spool_id) if spool_id else None
        return {
            "matched": spool_id is not None,
            "spool_id": spool_id,
            "spool": _model_dict(spool),
        }

    async def bind_tag(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        status, _ = await coordinator.rest.link_tag(
            call.data["spool_id"], call.data["tag_uid"]
        )
        if status == 409:
            raise HomeAssistantError(
                "Tag is already on another active spool (409). Use spooltap.recycle_tag."
            )
        await coordinator.async_refresh()

    async def recycle_tag(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        source = call.data.get("source_spool_id")
        if source is None:  # resolve client-side (never the broadcasting tag-scanned),
            # FRESH — a stale local resolve could point the clear at the wrong spool
            source = await coordinator.resolve_tag_fresh(call.data["tag_uid"])
        status = await coordinator.rest.recycle_tag(
            call.data["tag_uid"], call.data["target_spool_id"], source
        )
        await coordinator.async_refresh()
        if status == 409:
            raise HomeAssistantError(
                "Recycle failed: the tag is on another active spool that could not "
                "be identified and cleared safely. Pass source_spool_id, or unbind "
                "the tag in Bambuddy first."
            )

    async def create_spool(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        fields = dict(call.data)
        material = fields.pop("material")
        spool = await coordinator.rest.create_spool(material, **fields)
        await coordinator.async_refresh()
        return {"spool_id": (spool or {}).get("id"), "spool": spool}

    async def assign_slot(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass)
        printer_id = call.data.get("printer_id")
        if printer_id is None:  # auto-derived from BB (portable: no hardcoded id)
            printer_id = coordinator.printer_id
        result = await coordinator.relocate_assign(
            call.data["spool_id"],
            printer_id,
            call.data["ams_id"],
            call.data["tray_id"],
        )
        # BB's assign IS the physical push: configured=pushed now, pending_config=will
        # apply on next insert / when the printer reconnects.
        return {
            "configured": bool(result.get("configured")),
            "pending_config": bool(result.get("pending_config")),
        }

    async def unassign_slot(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        printer_id = call.data.get("printer_id")
        if printer_id is None:
            printer_id = coordinator.printer_id
        await coordinator.rest.unassign(
            printer_id, call.data["ams_id"], call.data["tray_id"]
        )
        await coordinator.async_refresh()

    async def modify_spool(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        fields = {
            k: call.data[k]
            for k in ("color_name", "material", "weight_used", "weight_locked")
            if k in call.data
        }
        if not fields:
            raise HomeAssistantError("modify_spool: nothing to change")
        # NOTE: BB auto-sets weight_locked=true whenever weight_used is sent (unless
        # weight_locked is sent explicitly). Locked only disables BB's coarse AMS
        # remain% reconciliation; the precise per-print tracker keeps deducting, so a
        # corrected weight is not frozen — it just can't be clobbered by the AMS.
        await coordinator.rest.modify_spool(call.data["spool_id"], **fields)
        await coordinator.async_refresh()

    async def weigh_spool(call: ServiceCall) -> None:
        """Recertify a spool from a gross scale reading (BB-native weigh-in).

        BB's scale endpoint does the tare math itself (net = gross - core_weight;
        weight_used = label_weight - net), corrects in BOTH directions, and stamps
        last_weighed_at. We then lock the weight so the AMS remain% sync (strictly
        increase-only, 10 g resolution) can't clobber a downward correction; the
        per-print usage tracker ignores the lock and keeps tracking from the
        certified value.
        """
        coordinator = _coordinator(hass)
        await coordinator.rest.weigh_spool(
            call.data["spool_id"], call.data["gross_grams"]
        )
        await coordinator.rest.modify_spool(call.data["spool_id"], weight_locked=True)
        await coordinator.async_refresh()

    async def archive_spool(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        await coordinator.rest.archive_spool(call.data["spool_id"])
        await coordinator.async_refresh()

    async def refresh(call: ServiceCall) -> None:
        await _coordinator(hass).async_refresh()

    async def bind_slot_tag(call: ServiceCall) -> None:
        co = _coordinator(hass)
        slot_key = call.data["slot_key"]
        # store CANONICAL (same form as spool tags in BB) so a pasted tag in any
        # case/length form still matches a live scan at the dispatch layer
        tag = canonical_tag((call.data.get("tag_uid") or "").strip())
        # one tag ↔ one slot: drop this tag from any other slot first
        co.slot_tags = {
            k: v for k, v in co.slot_tags.items()
            if canonical_tag(v) != tag or not tag
        }
        if tag:
            co.slot_tags[slot_key] = tag
        else:
            co.slot_tags.pop(slot_key, None)
        await co.async_save_slot_tags()

    async def clear_slot_tag(call: ServiceCall) -> None:
        co = _coordinator(hass)
        co.slot_tags.pop(call.data["slot_key"], None)
        await co.async_save_slot_tags()

    async def install_dashboard(call: ServiceCall) -> None:
        """(Re)install the auto-created SpoolTap dashboard; force overwrites the layout."""
        from .dashboard import async_ensure_dashboard

        if not await async_ensure_dashboard(hass, force=call.data["force"]):
            raise HomeAssistantError(
                "SpoolTap dashboard install failed — see the log for details."
            )

    reg = hass.services.async_register
    reg(DOMAIN, SERVICE_RESOLVE_TAG, _bb_errors(resolve_tag), schema=RESOLVE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL)
    reg(DOMAIN, SERVICE_BIND_TAG, _bb_errors(bind_tag), schema=BIND_SCHEMA)
    reg(DOMAIN, SERVICE_RECYCLE_TAG, _bb_errors(recycle_tag), schema=RECYCLE_SCHEMA)
    reg(DOMAIN, SERVICE_CREATE_SPOOL, _bb_errors(create_spool), schema=CREATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL)
    reg(DOMAIN, SERVICE_ASSIGN_SLOT, _bb_errors(assign_slot), schema=ASSIGN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL)
    reg(DOMAIN, SERVICE_UNASSIGN_SLOT, _bb_errors(unassign_slot), schema=UNASSIGN_SCHEMA)
    reg(DOMAIN, SERVICE_MODIFY_SPOOL, _bb_errors(modify_spool), schema=MODIFY_SCHEMA)
    reg(DOMAIN, SERVICE_WEIGH_SPOOL, _bb_errors(weigh_spool), schema=WEIGH_SCHEMA)
    reg(DOMAIN, SERVICE_ARCHIVE_SPOOL, _bb_errors(archive_spool), schema=ARCHIVE_SCHEMA)
    reg(DOMAIN, SERVICE_REFRESH, refresh, schema=vol.Schema({}))
    reg(DOMAIN, SERVICE_BIND_SLOT_TAG, bind_slot_tag, schema=vol.Schema(
        {vol.Required("slot_key"): cv.string, vol.Optional("tag_uid", default=""): cv.string}))
    reg(DOMAIN, SERVICE_CLEAR_SLOT_TAG, clear_slot_tag, schema=vol.Schema(
        {vol.Required("slot_key"): cv.string}))
    reg(DOMAIN, SERVICE_INSTALL_DASHBOARD, install_dashboard, schema=vol.Schema(
        {vol.Optional("force", default=False): cv.boolean}))


def async_unload_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_RESOLVE_TAG,
        SERVICE_BIND_TAG,
        SERVICE_RECYCLE_TAG,
        SERVICE_CREATE_SPOOL,
        SERVICE_ASSIGN_SLOT,
        SERVICE_UNASSIGN_SLOT,
        SERVICE_MODIFY_SPOOL,
        SERVICE_WEIGH_SPOOL,
        SERVICE_ARCHIVE_SPOOL,
        SERVICE_REFRESH,
        SERVICE_BIND_SLOT_TAG,
        SERVICE_CLEAR_SLOT_TAG,
        SERVICE_INSTALL_DASHBOARD,
    ):
        hass.services.async_remove(DOMAIN, service)
