"""Config flow for SpoolTap: collect the Bambuddy URL (+ optional API key)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bambuddy.rest_client import BambuddyRestClient
from .const import CONF_API_TOKEN, CONF_HOST, DEFAULT_HOST, DOMAIN


class SpoolTapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SpoolTap."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).rstrip("/")
            token = user_input.get(CONF_API_TOKEN) or None
            session = async_get_clientsession(self.hass)
            rest = BambuddyRestClient(session, host, api_key=token)
            try:
                await rest.health()
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SpoolTap ({host})",
                    data={CONF_HOST: host, CONF_API_TOKEN: token},
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=(user_input or {}).get(CONF_HOST, DEFAULT_HOST),
                ): str,
                vol.Optional(CONF_API_TOKEN): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """YAML import fallback (trusts the configured host, skips the probe)."""
        host = str(user_input.get(CONF_HOST, DEFAULT_HOST)).rstrip("/")
        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"SpoolTap ({host})",
            data={
                CONF_HOST: host,
                CONF_API_TOKEN: user_input.get(CONF_API_TOKEN),
            },
        )
