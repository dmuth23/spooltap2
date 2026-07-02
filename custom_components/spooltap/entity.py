"""Base entities for SpoolTap: everything grouped under one HA device, pinned entity_ids."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .brain.inventory import SpoolModel
from .const import DOMAIN
from .coordinator import SpoolTapCoordinator

if TYPE_CHECKING:
    from .flows import SpoolTapFlows


class SpoolTapSpoolEntity(CoordinatorEntity[SpoolTapCoordinator]):
    """One entity per spool, keyed by BB spool id.

    `has_entity_name = False` + an explicit id-based `entity_id` gives a stable,
    device-name-independent object id. Lock the scheme before beta testers add the
    integration — `entity_id` is only honored at first registration.
    """

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: SpoolTapCoordinator,
        *,
        spool_id: int,
        key: str,
        platform_domain: str,
    ) -> None:
        super().__init__(coordinator)
        self._spool_id = spool_id
        self._key = key
        self._attr_unique_id = f"spool-{spool_id}_{key}"
        self.entity_id = f"{platform_domain}.spooltap_spool_{spool_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "spooltap-inventory")},
            name="SpoolTap Inventory",
            manufacturer="Bambuddy",
            model="Filament Inventory",
        )

    @property
    def spool(self) -> SpoolModel | None:
        return (self.coordinator.data or {}).get(self._spool_id)

    @property
    def available(self) -> bool:
        # Decoupled from last_update_success on purpose: a single failed BB poll must
        # NOT blank every spool sensor at once (that empties/refills the inventory grid
        # for every open session = the multi-session "flash"). The coordinator retains
        # last-good .data on a failed poll, so a spool we still know about stays available.
        return self.spool is not None


class SpoolTapFlowEntity(Entity):
    """Thin VIEW over SpoolTapFlows controller state (state lives in the controller).

    Subscribes to the controller's one dispatcher signal and re-renders. Pre-setting
    `entity_id` is the suggested_object_id path, so the shipped dashboard's
    `<platform>.spooltap_<key>` references hold regardless of the device name.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        flows: SpoolTapFlows,
        *,
        key: str,
        name: str,
        platform_domain: str,
        icon: str | None = None,
    ) -> None:
        self._flows = flows
        self._key = key
        self._attr_unique_id = f"spooltap_{key}"
        self._attr_name = name
        if icon:
            self._attr_icon = icon
        self.entity_id = f"{platform_domain}.spooltap_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "spooltap-inventory")},
            name="SpoolTap Inventory",
            manufacturer="Bambuddy",
            model="Filament Inventory",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._flows.signal, self._flows_updated)
        )

    @callback
    def _flows_updated(self) -> None:
        self.async_write_ha_state()
