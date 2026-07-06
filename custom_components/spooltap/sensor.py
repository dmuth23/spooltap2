"""Inventory sensors (one per BB spool, dynamic-add) + the flows-state sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SpoolTapCoordinator
from .entity import SpoolTapFlowEntity, SpoolTapSpoolEntity
from .flows import STATUS_LEVELS, SpoolTapFlows


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SpoolTapCoordinator = entry.runtime_data.coordinator
    flows: SpoolTapFlows = entry.runtime_data.flows
    known: set[int] = set()

    @callback
    def _sync() -> None:
        new: list[InventorySpoolSensor] = []
        for spool_id in coordinator.data or {}:
            if spool_id not in known:
                known.add(spool_id)
                new.append(InventorySpoolSensor(coordinator, spool_id))
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
    async_add_entities(
        [
            SlotsSensor(coordinator),  # the auto-derived AMS slot layout
            StatusSensor(flows),
            AssignResultSensor(flows),
            TagInHandSensor(flows),
            ModLoadedSensor(flows),
        ]
    )


class SlotsSensor(CoordinatorEntity[SpoolTapCoordinator], SensorEntity):
    """Exposes the AMS slot layout auto-derived from Bambuddy (state = slot count)."""

    _attr_icon = "mdi:tray"
    _attr_has_entity_name = False

    def __init__(self, coordinator: SpoolTapCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = "spooltap-slots"
        self.entity_id = "sensor.spooltap_slots"
        self._attr_name = "SpoolTap Slots"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "spooltap-inventory")},
            name="SpoolTap Inventory",
            manufacturer="Bambuddy",
            model="Filament Inventory",
        )

    @property
    def native_value(self) -> StateType:
        return len(self.coordinator.slots)

    @property
    def extra_state_attributes(self) -> dict:
        st = self.coordinator.slot_tags
        slots = [{**s, "tag_uid": st.get(s["key"])} for s in self.coordinator.slots]
        return {"slots": slots, "bound": sum(1 for s in slots if s["tag_uid"])}


class StatusSensor(SpoolTapFlowEntity, SensorEntity):
    """The workflow status.

    v0.3.0 restructure: the STATE is the level (an enum from STATUS_LEVELS) so
    dashboards/automations can color and route on it; the human message lives in the
    `message` attribute (no 255-char state limit), with `updated_at` for freshness.
    (Pre-0.3.0 the state was the message itself.)
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUS_LEVELS

    _LEVEL_ICONS = {
        "Idle": "mdi:message-outline",
        "Ready": "mdi:gesture-tap",
        "Armed": "mdi:crosshairs-gps",
        "Working": "mdi:progress-clock",
        "Success": "mdi:check-circle",
        "Warning": "mdi:alert",
        "Error": "mdi:alert-octagon",
        "Info": "mdi:message-text",
    }

    def __init__(self, flows: SpoolTapFlows) -> None:
        super().__init__(
            flows,
            key="status",
            name="Status",
            platform_domain=Platform.SENSOR,
            icon="mdi:message-text",
        )

    @property
    def native_value(self) -> StateType:
        return self._flows.status_level

    @property
    def icon(self) -> str:
        return self._LEVEL_ICONS.get(self._flows.status_level, "mdi:message-text")

    @property
    def extra_state_attributes(self) -> dict:
        updated = self._flows.status_updated
        return {
            "message": self._flows.status or None,
            "updated_at": updated.isoformat() if updated else None,
            "busy": self._flows.busy,
        }


class AssignResultSensor(SpoolTapFlowEntity, SensorEntity):
    """Assign outcome + the armed pending slot (was stv2_assign_result + _pending_slot)."""

    def __init__(self, flows: SpoolTapFlows) -> None:
        super().__init__(
            flows,
            key="assign_result",
            name="Assign result",
            platform_domain=Platform.SENSOR,
            icon="mdi:information",
        )

    @property
    def native_value(self) -> StateType:
        return self._flows.assign_result

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "pending_slot_key": self._flows.pending_slot_key,
            "pending_slot_label": self._flows.pending_slot_label,
        }


class TagInHandSensor(SpoolTapFlowEntity, SensorEntity):
    """Last scanned tag + its classification (was the STV2 Tag In Hand template)."""

    def __init__(self, flows: SpoolTapFlows) -> None:
        super().__init__(
            flows,
            key="tag_in_hand",
            name="Tag in hand",
            platform_domain=Platform.SENSOR,
            icon="mdi:nfc-tap",
        )

    @property
    def native_value(self) -> StateType:
        return self._flows.last_scanned[:255] or None

    @property
    def extra_state_attributes(self) -> dict:
        role, detail = self._flows.tag_role_detail()
        return {"role": role, "detail": detail}


class ModLoadedSensor(SpoolTapFlowEntity, SensorEntity):
    """The spool loaded in Modify, 0 = none (was input_number.stv2_mod_spool_id)."""

    def __init__(self, flows: SpoolTapFlows) -> None:
        super().__init__(
            flows,
            key="mod_loaded",
            name="Modify loaded spool",
            platform_domain=Platform.SENSOR,
            icon="mdi:pencil-box",
        )

    @property
    def native_value(self) -> StateType:
        return self._flows.loaded_spool_id

    @property
    def extra_state_attributes(self) -> dict:
        spool = self._flows.loaded_spool
        if spool is None:
            return {}
        return {
            "display_name": spool.display_name,
            "color_name": spool.color_name,
            "material": spool.material,
            "remaining_grams": spool.remaining_grams,
            "tag_uid": spool.tag_uid,
            "assigned_slot": spool.assigned_slot,
        }


class InventorySpoolSensor(SpoolTapSpoolEntity, SensorEntity):
    """State = material; everything else exposed as attributes."""

    _attr_icon = "mdi:printer-3d-nozzle"

    def __init__(self, coordinator: SpoolTapCoordinator, spool_id: int) -> None:
        super().__init__(
            coordinator, spool_id=spool_id, key="info", platform_domain=Platform.SENSOR
        )

    @property
    def name(self) -> str:
        return self.spool.display_name if self.spool else f"Spool {self._spool_id}"

    @property
    def native_value(self) -> StateType:
        return None if self.spool is None else self.spool.material

    @property
    def icon(self) -> str:
        if self.spool and self.spool.tag_uid:
            return "mdi:nfc-variant"
        return self._attr_icon

    @property
    def extra_state_attributes(self) -> dict[str, StateType]:
        spool = self.spool
        if spool is None:
            return {}
        return {
            "spool_id": spool.spool_id,
            "material": spool.material,
            "color_name": spool.color_name,
            "rgba": spool.rgba,
            "brand": spool.brand,
            "category": spool.category,
            "remaining_grams": spool.remaining_grams,
            "label_weight": spool.label_weight,
            "weight_used": spool.weight_used,
            "core_weight": spool.core_weight,
            "tag_uid": spool.tag_uid,
            "tag_type": spool.tag_type,
            "data_origin": spool.data_origin,
            "slicer_filament": spool.slicer_filament,
            "nozzle_temp_min": spool.nozzle_temp_min,
            "nozzle_temp_max": spool.nozzle_temp_max,
            "assigned_slot": spool.assigned_slot,
            # raw ids so dashboards can join against the slots sensor's `key`
            # ('<ams>_<tray>') — assigned_slot's display form doesn't match labels
            "assigned_ams_id": spool.assigned_ams_id,
            "assigned_tray_id": spool.assigned_tray_id,
        }
