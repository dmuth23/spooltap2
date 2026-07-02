"""Text views over the flows controller: the Modify name field + the no-scan tag paste."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SpoolTapFlowEntity
from .flows import SpoolTapFlows

# key, name, icon, max length (ported from the stv2_* input_texts)
TEXTS: list[tuple[str, str, str, int]] = [
    ("mod_name", "Modify name", "mdi:rename-box", 255),
    ("tag_input", "Tag UID (no-scan)", "mdi:keyboard", 64),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    flows: SpoolTapFlows = entry.runtime_data.flows
    async_add_entities(
        SpoolTapText(flows, key, name, icon, max_len)
        for key, name, icon, max_len in TEXTS
    )


class SpoolTapText(SpoolTapFlowEntity, TextEntity):
    """A staging text field; the value lives in the controller."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0

    def __init__(
        self, flows: SpoolTapFlows, key: str, name: str, icon: str, max_len: int
    ) -> None:
        super().__init__(
            flows, key=key, name=name, platform_domain=Platform.TEXT, icon=icon
        )
        self._attr_native_max = max_len

    @property
    def native_value(self) -> str:
        return self._flows.texts.get(self._key, "")

    async def async_set_value(self, value: str) -> None:
        self._flows.async_set_text(self._key, value)
