"""Configuration flow for WiBi."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WibiAuthenticationError,
    WibiClient,
    WibiConnectionError,
    WibiError,
    WibiInvalidCallbackError,
    extract_sync_token,
)
from .const import (
    CONF_AUTH,
    CONF_CALLBACK_URL,
    CONF_PERSISTENT_NOTIFICATIONS,
    DOMAIN,
    SSO_LOGIN_URL,
)
from .notifications import (
    DEFAULT_CUSTOM_TEST_EVENT,
    async_fire_custom_test_event,
    async_fire_test_event,
)

CALLBACK_SCHEMA = vol.Schema({vol.Required(CONF_CALLBACK_URL): str})
CONF_CUSTOM_TEST_EVENT = "event_yaml"


class WibiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle WiBi configuration."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> WibiOptionsFlowHandler:
        """Create the WiBi options flow."""
        return WibiOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up WiBi from the integrations UI."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return await self._async_process_callback("user", user_input)

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
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


class WibiOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Manage WiBi notification options."""

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show WiBi configuration and test actions."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["notifications", "test_event", "custom_test_event"],
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set whether persistent notifications are created."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="notifications",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PERSISTENT_NOTIFICATIONS,
                        default=self.config_entry.options.get(
                            CONF_PERSISTENT_NOTIFICATIONS, True
                        ),
                    ): bool,
                }
            ),
        )

    async def async_step_test_event(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fire a test event containing the newest incoming message."""
        coordinator = self.config_entry.runtime_data
        if not async_fire_test_event(self.hass, coordinator.data):
            return self.async_abort(reason="no_messages")
        return self.async_abort(reason="test_event_fired")

    async def async_step_custom_test_event(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show an editable event payload and fire it after validation."""
        errors: dict[str, str] = {}
        event_yaml = DEFAULT_CUSTOM_TEST_EVENT

        if user_input is not None:
            event_yaml = user_input[CONF_CUSTOM_TEST_EVENT]
            try:
                async_fire_custom_test_event(self.hass, event_yaml)
            except ValueError:
                errors[CONF_CUSTOM_TEST_EVENT] = "invalid_event_yaml"
            else:
                return self.async_abort(reason="custom_test_event_fired")

        return self.async_show_form(
            step_id="custom_test_event",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CUSTOM_TEST_EVENT,
                        default=event_yaml,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
        )
