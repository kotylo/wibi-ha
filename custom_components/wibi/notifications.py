"""New-message notifications for WiBi."""

from __future__ import annotations

from typing import Any

import yaml

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, EVENT_NEW_MESSAGE
from .coordinator import WibiDataUpdateCoordinator
from .models import WibiMessage, WibiReply


DEFAULT_CUSTOM_TEST_EVENT = """event_type: wibi_new_message
data:
  id: 5286b509-24bc-4fe9-b528-5ce66c07d264
  topic: Projekt-Nachmittag morgen
  content: >-
    Liebe Eltern. Morgen um 9:30 gibt es HOFER Lauf. Bitte die Sportschuhe mitnehmen und 8 EUR für den Startgeld
  contentHtml: >-
    Liebe Eltern. Morgen um 9:30 gibt es HOFER Lauf. Bitte die Sportschuhe mitnehmen und 8 EUR für den Startgeld
  sender: Someone Unknown
  message_type: Undefined
  updated_at: '2026-09-27T17:22:50.611Z'
  school_class_id: 126a20a5-b671-46ec-acb3-9a37f62f216b
  pupil_id: 1f1db2b3-028b-4c84-95b2-2ab5dd05de49
  scope_name: 2A
  is_owned: false
  is_read: false
  is_confirmed: false
  can_confirm: false
  signature_required: false
  is_done: false
  replies: []
"""


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


@callback
def async_fire_test_event(
    hass: HomeAssistant, messages: list[WibiMessage]
) -> bool:
    """Fire a new-message event using the newest incoming cached message."""
    message = next((item for item in messages if not item.is_owned), None)
    if message is None:
        return False

    hass.bus.async_fire(EVENT_NEW_MESSAGE, message.as_dict())
    return True


@callback
def async_fire_custom_test_event(hass: HomeAssistant, event_yaml: str) -> None:
    """Validate editable YAML and fire the described test event."""
    try:
        event: Any = yaml.safe_load(event_yaml)
    except yaml.YAMLError as error:
        raise ValueError("Invalid event YAML") from error

    if not isinstance(event, dict):
        raise ValueError("Event YAML must contain a mapping")

    event_type = event.get("event_type")
    event_data = event.get("data")
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type must be a non-empty string")
    if not isinstance(event_data, dict):
        raise ValueError("data must be a mapping")

    hass.bus.async_fire(event_type.strip(), event_data)


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
