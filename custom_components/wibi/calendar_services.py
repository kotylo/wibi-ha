"""Expose optional Google Calendar reminder actions to Home Assistant scripts."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
)
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util

from .calendar_reminders import (
    CalendarReminderError,
    async_append_calendar_reminder,
    async_calendar_snapshot,
)
from .const import DOMAIN

SERVICES = ("get_calendar_events", "append_calendar_reminder")


async def _check_access(hass: HomeAssistant, call: ServiceCall, policy: str) -> None:
    """Honor the same entity permissions as Home Assistant's calendar API."""
    if user_id := call.context.user_id:
        user = await hass.auth.async_get_user(user_id)
        if user is None or not user.permissions.check_entity(
            call.data["entity_id"], policy
        ):
            raise Unauthorized(entity_id=call.data["entity_id"])


def _google_calendar(hass: HomeAssistant, entity_id: str) -> tuple[Any, Any]:
    """Resolve the configured Google entity and its existing authenticated API."""
    registered = er.async_get(hass).async_get(entity_id)
    component = hass.data.get("calendar")
    entity = component.get_entity(entity_id) if component is not None else None
    if registered is None or registered.platform != "google" or entity is None:
        raise ServiceValidationError("Select a loaded Google Calendar entity")
    if entity.entity_description.read_only:
        raise ServiceValidationError("The Google Calendar connection must allow writes")
    entry = hass.config_entries.async_get_entry(registered.config_entry_id)
    api = getattr(getattr(entry, "runtime_data", None), "service", None)
    if api is None or not hasattr(api, "async_patch_event"):
        raise ServiceValidationError(
            "This Google Calendar version does not support reminder edits"
        )
    return entity, api


def async_register_calendar_services(hass: HomeAssistant) -> None:
    """Register calendar helpers without requiring Google for normal WiBi use."""
    locks: dict[str, asyncio.Lock] = {}

    async def get_events(call: ServiceCall) -> dict[str, object]:
        await _check_access(hass, call, POLICY_READ)
        # Google loads this library; WiBi does not need it unless this is called.
        entity, api = _google_calendar(hass, call.data["entity_id"])
        from gcal_sync.api import ListEventsRequest
        from gcal_sync.exceptions import ApiException

        start = call.data["start_date_time"]
        end = call.data["end_date_time"]
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ServiceValidationError(
                "Use an increasing time range with UTC offsets"
            )
        try:
            return await async_calendar_snapshot(
                api,
                ListEventsRequest(
                    calendar_id=entity.calendar_id, start_time=start, end_time=end
                ),
            )
        except ApiException as error:
            raise HomeAssistantError("Unable to read Google Calendar events") from error

    async def append_reminder(call: ServiceCall) -> dict[str, object]:
        await _check_access(hass, call, POLICY_CONTROL)
        entity_id = call.data["entity_id"]
        entity, api = _google_calendar(hass, entity_id)
        from gcal_sync.exceptions import ApiException

        # Serialize WiBi edits to this calendar, including different occurrences.
        async with locks.setdefault(entity.calendar_id, asyncio.Lock()):
            try:
                result = await async_append_calendar_reminder(
                    api,
                    entity.calendar_id,
                    event_id=call.data["event_id"],
                    expected_start=call.data["expected_start"],
                    expected_description=call.data["expected_description"],
                    reminder=call.data["reminder"],
                    now=dt_util.now(),
                )
            except CalendarReminderError as error:
                raise ServiceValidationError(str(error)) from error
            except ApiException as error:
                raise HomeAssistantError(
                    "Unable to update Google Calendar reminder"
                ) from error
            if result["updated"]:
                # The write succeeded even if refreshing HA's cached view fails.
                try:
                    await entity.coordinator.async_refresh()
                except HomeAssistantError:
                    pass
            return result

    hass.services.async_register(
        DOMAIN,
        SERVICES[0],
        get_events,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_domain("calendar"),
                vol.Required("start_date_time"): cv.datetime,
                vol.Required("end_date_time"): cv.datetime,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICES[1],
        append_reminder,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_domain("calendar"),
                vol.Required("event_id"): cv.string,
                vol.Required("expected_start"): cv.datetime,
                vol.Required("expected_description"): cv.string,
                vol.Required("reminder"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


def async_unregister_calendar_services(hass: HomeAssistant) -> None:
    """Remove reminder helpers when WiBi is unloaded."""
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
