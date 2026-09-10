"""Sensor platform for WiBi messages."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import WibiDataUpdateCoordinator

ATTRIBUTE_MESSAGE_LIMIT = 20
ATTRIBUTE_CONTENT_LIMIT = 1000


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[WibiDataUpdateCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the WiBi message sensor."""
    async_add_entities([WibiMessagesSensor(entry.runtime_data, entry)])


class WibiMessagesSensor(CoordinatorEntity[WibiDataUpdateCoordinator], SensorEntity):
    """Summarize messages while keeping recent content available to templates."""

    _attr_icon = "mdi:message-text-outline"
    _attr_name = "WiBi messages"

    def __init__(
        self,
        coordinator: WibiDataUpdateCoordinator,
        entry: ConfigEntry[WibiDataUpdateCoordinator],
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_messages"

    @property
    def native_value(self) -> int:
        """Return the total number of current messages."""
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose counts and recent plain-text messages for automations and TTS."""
        messages = self.coordinator.data
        return {
            "unread_count": sum(
                not message.is_owned and not message.is_read for message in messages
            ),
            "unconfirmed_count": sum(message.can_confirm for message in messages),
            "messages": [
                message.as_dict(content_limit=ATTRIBUTE_CONTENT_LIMIT)
                for message in messages[:ATTRIBUTE_MESSAGE_LIMIT]
            ],
            "messages_in_attributes": min(len(messages), ATTRIBUTE_MESSAGE_LIMIT),
            "messages_truncated": len(messages) > ATTRIBUTE_MESSAGE_LIMIT,
        }
