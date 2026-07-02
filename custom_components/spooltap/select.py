"""Select views over the flows controller: the mode + every workflow picker.

Options are computed by the controller (facet narrowing + placeholder fallback) on
every coordinator update and facet change — the entities only render. Only the mode
select restores across restarts (RestoreEntity); everything else re-seeds from the
placeholder, exactly like the package's helpers did on reload.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SpoolTapFlowEntity
from .flows import PLACEHOLDERS, SpoolTapFlows

# key, name, icon (ported from the stv2_* input_selects)
SELECTS: list[tuple[str, str, str]] = [
    ("assign_slot", "Assign slot", "mdi:tray-arrow-down"),
    ("assign_brand", "Assign brand", "mdi:factory"),
    ("assign_type", "Assign type", "mdi:texture"),
    ("assign_spool", "Assign spool", "mdi:format-list-bulleted"),
    ("bind_pool", "Bind pool", "mdi:filter-variant"),
    ("bind_brand", "Bind brand", "mdi:factory"),
    ("bind_type", "Bind type", "mdi:texture"),
    ("bind_spool", "Bind spool", "mdi:format-list-bulleted"),
    ("bind_tag", "Bind tag", "mdi:nfc"),
    ("bind_slot", "Bind register slot", "mdi:tray-plus"),
    ("mod_open_tag", "Modify open by tag", "mdi:tag-search"),
    ("mod_open_spool", "Modify open by spool", "mdi:format-list-bulleted"),
    ("mod_material", "Modify material", "mdi:printer-3d-nozzle"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    flows: SpoolTapFlows = entry.runtime_data.flows
    entities: list[SelectEntity] = [SpoolTapModeSelect(flows)]
    entities += [SpoolTapSelect(flows, key, name, icon) for key, name, icon in SELECTS]
    async_add_entities(entities)


class SpoolTapSelect(SpoolTapFlowEntity, SelectEntity):
    """One workflow picker; selection + options both live in the controller."""

    def __init__(self, flows: SpoolTapFlows, key: str, name: str, icon: str) -> None:
        super().__init__(
            flows, key=key, name=name, platform_domain=Platform.SELECT, icon=icon
        )

    @property
    def options(self) -> list[str]:
        return self._flows.options.get(self._key, [PLACEHOLDERS[self._key]])

    @property
    def current_option(self) -> str | None:
        return self._flows.selections.get(self._key)

    async def async_select_option(self, option: str) -> None:
        await self._flows.async_select(self._key, option)


class SpoolTapModeSelect(SpoolTapSelect, RestoreEntity):
    """The area switcher. Restores the last mode across restarts (no `initial:` reset);
    changing away from Bind turns Bind mode off (in the controller)."""

    def __init__(self, flows: SpoolTapFlows) -> None:
        super().__init__(flows, "mode", "Mode", "mdi:view-dashboard")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._flows.async_set_restored_mode(last.state)
            self.async_write_ha_state()
