"""Switch view over the flows controller: Bind Mode (registration owns the scanner)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SpoolTapFlowEntity
from .flows import SpoolTapFlows


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SpoolTapBindModeSwitch(entry.runtime_data.flows)])


class SpoolTapBindModeSwitch(SpoolTapFlowEntity, SwitchEntity):
    """While ON, scans only feed last_scanned for the Bind actions (no dispatch);
    turned off automatically when the mode select leaves Bind."""

    def __init__(self, flows: SpoolTapFlows) -> None:
        super().__init__(
            flows,
            key="bind_mode",
            name="Bind Mode",
            platform_domain=Platform.SWITCH,
            icon="mdi:nfc-tap",
        )

    @property
    def is_on(self) -> bool:
        return self._flows.bind_mode

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._flows.async_set_bind_mode(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._flows.async_set_bind_mode(False)
