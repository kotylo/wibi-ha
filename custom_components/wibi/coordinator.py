"""Message polling coordinator for WiBi."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WibiAuthenticationError, WibiClient, WibiError
from .const import APPLICATION_TYPE, DOMAIN, MESSAGE_UPDATE_INTERVAL
from .models import MessageScope, WibiMessage

_LOGGER = logging.getLogger(__name__)


class WibiDataUpdateCoordinator(DataUpdateCoordinator[list[WibiMessage]]):
    """Fetch WiBi messages for every class visible to the account."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: WibiClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=MESSAGE_UPDATE_INTERVAL,
        )
        self.client = client
        self.scopes: list[MessageScope] = []

    async def _async_update_data(self) -> list[WibiMessage]:
        try:
            inventory = await self.client.async_get_inventory()
            self.scopes = _message_scopes(inventory, self.client.user)
            payloads = await asyncio.gather(
                *(
                    self.client.async_get_messages(
                        scope.school_class_id, scope.pupil_id
                    )
                    for scope in self.scopes
                )
            )
        except WibiAuthenticationError as error:
            raise ConfigEntryAuthFailed("WiBi authentication expired") from error
        except WibiError as error:
            raise UpdateFailed(str(error)) from error

        messages_by_id: dict[str, WibiMessage] = {}
        for scope, items in zip(self.scopes, payloads, strict=True):
            for item in items:
                message = WibiMessage.from_payload(item, scope)
                if message is not None:
                    messages_by_id[message.id] = message
        return sorted(
            messages_by_id.values(),
            key=lambda item: item.updated_at or "",
            reverse=True,
        )

    async def async_confirm(self, message_id: str) -> dict[str, Any]:
        """Confirm one message, then immediately refresh all message state."""
        result = await self.client.async_confirm_message(message_id)
        await self.async_request_refresh()
        return result


def _message_scopes(
    inventory: list[dict[str, Any]], user: dict[str, Any]
) -> list[MessageScope]:
    """Derive the same class/pupil scopes used by the WiBi web client."""
    actor_type = user.get("actorType")
    scopes: dict[tuple[str, str | None], MessageScope] = {}
    for item in inventory:
        if item.get("applicationType") not in (None, APPLICATION_TYPE):
            continue
        item_type = item.get("itemType")
        if actor_type == "Parent" and item_type != "Pupil":
            continue
        if actor_type != "Parent" and item_type != "Class":
            continue

        pupil_id = str(item["id"]) if item_type == "Pupil" and item.get("id") else None
        class_id = item.get("schoolClassId")
        if not class_id and item_type == "Class":
            class_id = item.get("id")
        if not class_id:
            continue
        name = item.get("schoolClassName") or item.get("name")
        scope = MessageScope(
            school_class_id=str(class_id),
            pupil_id=pupil_id,
            name=str(name) if name else None,
        )
        scopes[(scope.school_class_id, scope.pupil_id)] = scope
    return list(scopes.values())
