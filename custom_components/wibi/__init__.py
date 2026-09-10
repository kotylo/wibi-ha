"""WiBi Home Assistant integration."""

from __future__ import annotations

from datetime import datetime
from functools import partial
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import (
    WibiAuthenticationError,
    WibiClient,
    WibiConnectionError,
    WibiError,
)
from .const import (
    ATTR_MESSAGE_ID,
    CONF_AUTH,
    DOMAIN,
    PLATFORMS,
    SERVICE_CONFIRM_LAST_MESSAGE,
    SERVICE_CONFIRM_MESSAGE,
    SERVICE_GET_MESSAGES,
    TOKEN_REFRESH_INTERVAL,
)
from .coordinator import WibiDataUpdateCoordinator
from .notifications import WibiMessageNotifier

_LOGGER = logging.getLogger(__name__)

type WibiConfigEntry = ConfigEntry[WibiDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: WibiConfigEntry) -> bool:
    """Set up WiBi and validate its persisted authentication token."""
    client = WibiClient(async_get_clientsession(hass), entry.data[CONF_AUTH])

    try:
        await _async_refresh_and_persist(hass, entry, client)
    except WibiAuthenticationError as error:
        raise ConfigEntryAuthFailed("WiBi authentication expired") from error
    except WibiConnectionError as error:
        raise ConfigEntryNotReady("Unable to connect to WiBi") from error
    except WibiError as error:
        raise ConfigEntryError("WiBi returned an invalid response") from error

    coordinator = WibiDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass, entry, coordinator)
    notifier = WibiMessageNotifier(hass, coordinator)
    entry.async_on_unload(coordinator.async_add_listener(notifier.async_handle_update))
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            partial(_async_periodic_refresh, hass, entry, client),
            TOKEN_REFRESH_INTERVAL,
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WibiConfigEntry) -> bool:
    """Unload a WiBi config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.services.async_remove(DOMAIN, SERVICE_GET_MESSAGES)
        hass.services.async_remove(DOMAIN, SERVICE_CONFIRM_MESSAGE)
        hass.services.async_remove(DOMAIN, SERVICE_CONFIRM_LAST_MESSAGE)
    return unloaded


def _async_register_services(
    hass: HomeAssistant,
    entry: WibiConfigEntry,
    coordinator: WibiDataUpdateCoordinator,
) -> None:
    """Register read and acknowledgement actions for the single WiBi entry."""

    async def async_get_messages(_call: ServiceCall) -> dict[str, object]:
        await coordinator.async_request_refresh()
        messages = coordinator.data
        return {
            "count": len(messages),
            "messages": [message.as_dict() for message in messages],
        }

    async def async_confirm_message(call: ServiceCall) -> dict[str, object]:
        message_id = call.data[ATTR_MESSAGE_ID]
        message = next(
            (message for message in coordinator.data if message.id == message_id), None
        )
        if message is None:
            raise ServiceValidationError(f"Unknown WiBi message ID: {message_id}")
        if message.is_owned:
            raise ServiceValidationError("Sent WiBi messages cannot be confirmed")
        return await _async_confirm(message.id, message.as_dict())

    async def async_confirm_last_message(_call: ServiceCall) -> dict[str, object]:
        await coordinator.async_request_refresh()
        message = next(
            (message for message in coordinator.data if not message.is_owned), None
        )
        if message is None:
            raise ServiceValidationError("No received WiBi message is available")
        return await _async_confirm(message.id, message.as_dict())

    async def _async_confirm(
        message_id: str, message_data: dict[str, object]
    ) -> dict[str, object]:
        try:
            await coordinator.async_confirm(message_id)
        except WibiAuthenticationError as error:
            entry.async_start_reauth(hass)
            raise HomeAssistantError("WiBi authentication expired") from error
        except WibiError as error:
            raise HomeAssistantError(str(error)) from error
        return {
            "message_id": message_id,
            "confirmed": True,
            "message": {
                **message_data,
                "is_confirmed": True,
                "can_confirm": False,
            },
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MESSAGES,
        async_get_messages,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIRM_MESSAGE,
        async_confirm_message,
        schema=vol.Schema({vol.Required(ATTR_MESSAGE_ID): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIRM_LAST_MESSAGE,
        async_confirm_last_message,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _async_refresh_and_persist(
    hass: HomeAssistant,
    entry: WibiConfigEntry,
    client: WibiClient,
) -> None:
    """Refresh authentication and persist a rotated token."""
    refreshed_auth = await client.async_refresh_auth()
    if refreshed_auth != entry.data[CONF_AUTH]:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_AUTH: refreshed_auth},
        )


async def _async_periodic_refresh(
    hass: HomeAssistant,
    entry: WibiConfigEntry,
    client: WibiClient,
    _now: datetime,
) -> None:
    """Keep the WiBi session active and request reauthentication if needed."""
    try:
        await _async_refresh_and_persist(hass, entry, client)
    except WibiAuthenticationError:
        _LOGGER.warning("WiBi authentication expired; reauthentication is required")
        entry.async_start_reauth(hass)
    except WibiError as error:
        _LOGGER.warning("Unable to refresh WiBi authentication: %s", error)
