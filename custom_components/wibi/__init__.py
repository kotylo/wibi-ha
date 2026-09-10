"""WiBi Home Assistant integration."""

from __future__ import annotations

from datetime import datetime
from functools import partial
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import (
    WibiAuthenticationError,
    WibiClient,
    WibiConnectionError,
    WibiError,
)
from .const import CONF_AUTH, TOKEN_REFRESH_INTERVAL

_LOGGER = logging.getLogger(__name__)

type WibiConfigEntry = ConfigEntry[WibiClient]


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

    entry.runtime_data = client
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
    return True


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
