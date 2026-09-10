"""Configuration flow for WiBi."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WibiAuthenticationError,
    WibiClient,
    WibiConnectionError,
    WibiError,
    WibiInvalidCallbackError,
    extract_sync_token,
)
from .const import CONF_AUTH, CONF_CALLBACK_URL, DOMAIN, SSO_LOGIN_URL

CALLBACK_SCHEMA = vol.Schema({vol.Required(CONF_CALLBACK_URL): str})


class WibiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle WiBi configuration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up WiBi from the integrations UI."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return await self._async_process_callback("user", user_input)

    async def async_step_reauth(
        self, _entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for an expired token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Finish reauthentication with a fresh SSO callback."""
        return await self._async_process_callback("reauth_confirm", user_input)

    async def _async_process_callback(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                sync_token = extract_sync_token(user_input[CONF_CALLBACK_URL])
                client = WibiClient(async_get_clientsession(self.hass))
                auth = await client.async_exchange_sso_token(sync_token)
                user_id = self._user_id(auth)
                title = self._entry_title(auth)
            except WibiInvalidCallbackError:
                errors[CONF_CALLBACK_URL] = "invalid_callback"
            except WibiAuthenticationError:
                errors["base"] = "invalid_auth"
            except WibiConnectionError:
                errors["base"] = "cannot_connect"
            except WibiError:
                errors["base"] = "unknown"
            else:
                if step_id == "reauth_confirm":
                    await self.async_set_unique_id(user_id)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(),
                        data_updates={CONF_AUTH: auth},
                    )

                await self.async_set_unique_id(user_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=title,
                    data={CONF_AUTH: auth},
                )

        return self.async_show_form(
            step_id=step_id,
            data_schema=CALLBACK_SCHEMA,
            errors=errors,
            description_placeholders={"login_url": SSO_LOGIN_URL},
        )

    @staticmethod
    def _user_id(auth: dict[str, Any]) -> str:
        user_id = auth.get("user", {}).get("id")
        if not user_id:
            raise WibiError("WiBi did not return a user ID")
        return str(user_id)

    @staticmethod
    def _entry_title(auth: dict[str, Any]) -> str:
        user = auth.get("user", {})
        name = " ".join(
            part.strip()
            for part in (user.get("firstName"), user.get("lastName"))
            if isinstance(part, str) and part.strip()
        )
        return name or "WiBi"
