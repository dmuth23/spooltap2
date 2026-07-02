"""Number views over the flows controller: the Modify weight boxes (core/gross/net).

Setting gross > 0 auto-fills net (= gross − core) in the controller — the port of the
stv2_mod_gross_to_net automation. Save then decides the weigh-in vs weight_used path.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SpoolTapFlowEntity
from .flows import SpoolTapFlows

# key, name, icon, min, max (ported from the stv2_* input_numbers; step 1, box mode)
NUMBERS: list[tuple[str, str, str, float, float]] = [
    ("mod_core", "Modify core weight (g)", "mdi:circle-outline", 0, 2000),
    ("mod_gross", "Modify gross (g)", "mdi:scale", 0, 10000),
    ("mod_net", "Modify remaining net (g)", "mdi:weight-gram", 0, 10000),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    flows: SpoolTapFlows = entry.runtime_data.flows
    async_add_entities(
        SpoolTapNumber(flows, key, name, icon, minimum, maximum)
        for key, name, icon, minimum, maximum in NUMBERS
    )


class SpoolTapNumber(SpoolTapFlowEntity, NumberEntity):
    """A staging weight box; the value lives in the controller."""

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1.0

    def __init__(
        self,
        flows: SpoolTapFlows,
        key: str,
        name: str,
        icon: str,
        minimum: float,
        maximum: float,
    ) -> None:
        super().__init__(
            flows, key=key, name=name, platform_domain=Platform.NUMBER, icon=icon
        )
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum

    @property
    def native_value(self) -> float:
        return self._flows.numbers.get(self._key, 0.0)

    async def async_set_native_value(self, value: float) -> None:
        self._flows.async_set_number(self._key, value)
