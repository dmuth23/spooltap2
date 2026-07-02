"""Button views over the flows controller — each ports the package script of the same intent."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import SpoolTapFlowEntity
from .flows import SpoolTapFlows

# key, name, icon, controller coroutine (ported from the stv2_* scripts)
BUTTONS: list[tuple[str, str, str, str]] = [
    ("assign", "Assign", "mdi:tray-arrow-down", "async_assign_manual"),
    ("cancel", "Cancel", "mdi:cancel", "async_cancel_assign"),
    ("bind_spool", "Bind to spool", "mdi:link-variant-plus", "async_bind_spool"),
    ("bind_slot", "Bind to slot", "mdi:tray-plus", "async_bind_slot"),
    ("save", "Save", "mdi:content-save", "async_mod_save"),
    ("archive", "Archive", "mdi:archive-arrow-down", "async_mod_archive"),
    ("close", "Close", "mdi:close-circle-outline", "async_mod_close"),
    ("refresh", "Refresh", "mdi:refresh", "async_refresh_action"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    flows: SpoolTapFlows = entry.runtime_data.flows
    async_add_entities(
        SpoolTapButton(flows, key, name, icon, method)
        for key, name, icon, method in BUTTONS
    )


class SpoolTapButton(SpoolTapFlowEntity, ButtonEntity):
    """Press -> the controller action; the outcome lands in the status/result sensors."""

    def __init__(
        self, flows: SpoolTapFlows, key: str, name: str, icon: str, method: str
    ) -> None:
        super().__init__(
            flows, key=key, name=name, platform_domain=Platform.BUTTON, icon=icon
        )
        self._method = method

    async def async_press(self) -> None:
        await getattr(self._flows, self._method)()
