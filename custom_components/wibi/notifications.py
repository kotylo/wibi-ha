"""New-message notifications for WiBi."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EVENT_NEW_MESSAGE
from .coordinator import WibiDataUpdateCoordinator


class WibiMessageNotifier:
    """Announce incoming messages discovered after the initial sync."""

    def __init__(
        self, hass: HomeAssistant, coordinator: WibiDataUpdateCoordinator
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._known_ids = {message.id for message in coordinator.data}

    @callback
    def async_handle_update(self) -> None:
        """Fire an event and create a persistent notification for new messages."""
        new_messages = [
            message
            for message in self._coordinator.data
            if message.id not in self._known_ids and not message.is_owned
        ]
        self._known_ids.update(message.id for message in self._coordinator.data)

        for message in reversed(new_messages):
            event_data = message.as_dict()
            self._hass.bus.async_fire(EVENT_NEW_MESSAGE, event_data)
            persistent_notification.async_create(
                self._hass,
                _notification_body(message.sender, message.content),
                title=f"WiBi: {message.topic or message.message_type}",
                notification_id=f"{DOMAIN}_{message.id}",
            )


def _notification_body(sender: str, content: str) -> str:
    """Build a readable notification body without empty placeholders."""
    if sender and content:
        return f"From {sender}\n\n{content}"
    return content or (f"From {sender}" if sender else "New WiBi message")
