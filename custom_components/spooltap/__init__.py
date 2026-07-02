"""SpoolTap V2 — Bambuddy-native filament tracking (no Spoolman).

Since v0.2.0 the integration IS the whole product: the data engine (coordinator +
services + spool/slot sensors), the workflow (native select/text/number/switch/button
entities over the SpoolTapFlows controller — the former YAML package), and the
dashboard (auto-installed storage dashboard). One-click via HACS.
"""

from __future__ import annotations

import logging

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bambuddy.rest_client import BambuddyRestClient
from .const import (
    CONF_API_TOKEN,
    CONF_HOST,
    DEFAULT_HOST,
    DEFAULT_INVENTORY_INTERVAL,
    DOMAIN,
)
from .coordinator import SpoolTapCoordinator
from .dashboard import async_ensure_dashboard
from .flows import SpoolTapFlows, SpoolTapRuntime
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

type SpoolTapConfigEntry = ConfigEntry[SpoolTapRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: SpoolTapConfigEntry) -> bool:
    host = str(entry.data.get(CONF_HOST, DEFAULT_HOST)).rstrip("/")
    token = entry.data.get(CONF_API_TOKEN)
    session = async_get_clientsession(hass)
    rest = BambuddyRestClient(session, host, api_key=token)
    coordinator = SpoolTapCoordinator(
        hass, entry, rest, update_interval_seconds=DEFAULT_INVENTORY_INTERVAL
    )
    await coordinator.async_load_slot_tags()  # slot->tag registry (persisted)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(f"SpoolTap: initial BB poll failed: {err}") from err

    flows = SpoolTapFlows(hass, entry, coordinator)
    flows.async_setup()  # tag_scanned listener + option recompute (torn down on unload)
    entry.runtime_data = SpoolTapRuntime(coordinator=coordinator, flows=flows)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_setup_services(hass)
    # one-click install: create the SpoolTap dashboard if it doesn't exist yet
    # (guarded — never fails setup; spooltap.install_dashboard is the retry lever)
    await async_ensure_dashboard(hass)

    # Hard precondition: Bambuddy<->Spoolman must be OFF. With it ON, the tag-scanned
    # resolver returns Spoolman IDs while our inventory reads the local table -> two ID
    # universes, bind/resolve/assign break. Warn loudly (self-clears when turned off).
    nid = f"{DOMAIN}_spoolman_on"
    try:
        if await rest.spoolman_enabled():
            persistent_notification.async_create(
                hass,
                "Bambuddy↔Spoolman integration is **ON**. SpoolTap V2 requires it **OFF** — "
                "resolve would hit Spoolman while inventory reads the local table, so "
                "bind / resolve / assign will misbehave. Turn Spoolman off in Bambuddy → "
                "Settings → Filament.",
                title="SpoolTap: turn Spoolman OFF",
                notification_id=nid,
            )
        else:
            persistent_notification.async_dismiss(hass, nid)
    except Exception as err:  # noqa: BLE001 - never block setup on the precondition probe
        _LOGGER.debug("spoolman precondition probe failed: %s", err)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpoolTapConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # tear down the shared services only when the last entry goes away
        remaining = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining:
            async_unload_services(hass)
    return unload_ok
