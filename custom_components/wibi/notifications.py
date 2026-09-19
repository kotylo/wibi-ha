"""New-message notifications for WiBi."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EVENT_NEW_MESSAGE
from .coordinator import WibiDataUpdateCoordinator
from .models import WibiMessage, WibiReply


class WibiMessageNotifier:
    """Announce incoming messages discovered after the initial sync."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: WibiDataUpdateCoordinator,
        *,
        show_persistent_notifications: bool = True,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._show_persistent_notifications = show_persistent_notifications
        self._known_ids = {message.id for message in coordinator.data}
        self._known_reply_ids = {
            reply.id for message in coordinator.data for reply in message.replies
        }

    @callback
    def async_handle_update(self) -> None:
        """Fire an event and optionally create a persistent notification."""
        messages = self._coordinator.data
        new_messages = [
            message
            for message in messages
            if message.id not in self._known_ids and not message.is_owned
        ]
        new_replies = [
            (message, reply)
            for message in messages
            for reply in message.replies
            if reply.id not in self._known_reply_ids and reply.is_incoming
        ]
        self._known_ids.update(message.id for message in messages)
        self._known_reply_ids.update(
            reply.id for message in messages for reply in message.replies
        )

        for message in reversed(new_messages):
            event_data = message.as_dict()
            self._hass.bus.async_fire(EVENT_NEW_MESSAGE, event_data)
            if self._show_persistent_notifications:
                persistent_notification.async_create(
                    self._hass,
                    _notification_body(message.sender, message.content),
                    title=f"WiBi: {message.topic or message.message_type}",
                    notification_id=f"{DOMAIN}_{message.id}",
                )
        for message, reply in reversed(new_replies):
            event_data = _reply_event_data(message, reply)
            self._hass.bus.async_fire(EVENT_NEW_MESSAGE, event_data)
            if self._show_persistent_notifications:
                persistent_notification.async_create(
                    self._hass,
                    _notification_body(reply.sender, reply.content),
                    title=f"WiBi reply: {message.topic or message.message_type}",
                    notification_id=f"{DOMAIN}_reply_{reply.id}",
                )


def _reply_event_data(message: WibiMessage, reply: WibiReply) -> dict[str, object]:
    """Represent a reply as a new-message event with its parent context."""
    event_data = message.as_dict()
    event_data.update(
        {
            "id": reply.id,
            "content": reply.content,
            "contentHtml": reply.content_html,
            "sender": reply.sender,
            "updated_at": reply.created_at,
            "is_owned": False,
            "is_read": False,
            "is_confirmed": False,
            "can_confirm": False,
            "signature_required": False,
            "is_reply": True,
            "parent_message_id": message.id,
            "reply": reply.as_dict(),
        }
    )
    return event_data


def _notification_body(sender: str, content: str) -> str:
    """Build a readable notification body without empty placeholders."""
    if sender and content:
        return f"From {sender}\n\n{content}"
    return content or (f"From {sender}" if sender else "New WiBi message")
