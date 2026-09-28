"""Tests for WiBi message and reply notifications."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock


def _load_notifications() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Load notifications and models with lightweight Home Assistant stubs."""
    root = Path(__file__).parents[1] / "custom_components" / "wibi"
    package_name = "wibi_test_notifications"
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package

    models_spec = spec_from_file_location(f"{package_name}.models", root / "models.py")
    if models_spec is None or models_spec.loader is None:
        raise RuntimeError("Unable to load the WiBi models module")
    models = module_from_spec(models_spec)
    sys.modules[models_spec.name] = models
    models_spec.loader.exec_module(models)

    const = ModuleType(f"{package_name}.const")
    const.DOMAIN = "wibi"
    const.EVENT_NEW_MESSAGE = "wibi_new_message"
    sys.modules[const.__name__] = const

    coordinator = ModuleType(f"{package_name}.coordinator")
    coordinator.WibiDataUpdateCoordinator = object
    sys.modules[coordinator.__name__] = coordinator

    persistent_notification = ModuleType(
        "homeassistant.components.persistent_notification"
    )
    persistent_notification.async_create = Mock()
    components = ModuleType("homeassistant.components")
    components.persistent_notification = persistent_notification
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda function: function
    homeassistant = ModuleType("homeassistant")
    homeassistant.components = components
    homeassistant.core = core
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.components"] = components
    sys.modules[persistent_notification.__name__] = persistent_notification
    sys.modules[core.__name__] = core

    notifications_spec = spec_from_file_location(
        f"{package_name}.notifications", root / "notifications.py"
    )
    if notifications_spec is None or notifications_spec.loader is None:
        raise RuntimeError("Unable to load the WiBi notifications module")
    notifications = module_from_spec(notifications_spec)
    sys.modules[notifications_spec.name] = notifications
    notifications_spec.loader.exec_module(notifications)
    return notifications, models, persistent_notification


notifications, models, persistent_notification = _load_notifications()


class WibiNotificationTests(unittest.TestCase):
    """Verify incoming replies are detected independently of their parent."""

    def test_new_reply_on_owned_message_is_announced_once(self) -> None:
        scope = models.MessageScope("class-id", "pupil-id")
        original = models.WibiMessage.from_payload(
            {"id": "message-id", "isOwned": True}, scope
        )
        updated = models.WibiMessage.from_payload(
            {
                "id": "message-id",
                "isOwned": True,
                "replies": [
                    {
                        "id": "reply-id",
                        "content": "<p>Teacher reply</p>",
                        "creatorFullName": "Teacher Name",
                        "createdAt": "2026-09-15T10:00:00Z",
                        "isIncoming": True,
                    }
                ],
            },
            scope,
        )
        coordinator = SimpleNamespace(data=[original])
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
        notifier = notifications.WibiMessageNotifier(hass, coordinator)
        persistent_notification.async_create.reset_mock()

        coordinator.data = [updated]
        notifier.async_handle_update()
        notifier.async_handle_update()

        hass.bus.async_fire.assert_called_once()
        event_type, event_data = hass.bus.async_fire.call_args.args
        self.assertEqual(event_type, "wibi_new_message")
        self.assertTrue(event_data["is_reply"])
        self.assertEqual(event_data["parent_message_id"], "message-id")
        self.assertEqual(event_data["content"], "Teacher reply")
        persistent_notification.async_create.assert_called_once_with(
            hass,
            "From Teacher Name\n\nTeacher reply",
            title="WiBi reply: Message",
            notification_id="wibi_reply_reply-id",
        )

    def test_disabling_persistent_notifications_keeps_message_and_reply_events(
        self,
    ) -> None:
        scope = models.MessageScope("class-id", "pupil-id")
        existing = models.WibiMessage.from_payload(
            {"id": "existing-message", "isOwned": True}, scope
        )
        existing_with_reply = models.WibiMessage.from_payload(
            {
                "id": "existing-message",
                "isOwned": True,
                "replies": [
                    {
                        "id": "reply-id",
                        "content": "A reply",
                        "isIncoming": True,
                    }
                ],
            },
            scope,
        )
        new_message = models.WibiMessage.from_payload(
            {"id": "new-message", "content": "New message"}, scope
        )
        assert existing is not None
        assert existing_with_reply is not None
        assert new_message is not None
        coordinator = SimpleNamespace(data=[existing])
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
        notifier = notifications.WibiMessageNotifier(
            hass,
            coordinator,
            show_persistent_notifications=False,
        )
        persistent_notification.async_create.reset_mock()

        coordinator.data = [existing_with_reply, new_message]
        notifier.async_handle_update()

        self.assertEqual(hass.bus.async_fire.call_count, 2)
        self.assertEqual(
            {call.args[1]["id"] for call in hass.bus.async_fire.call_args_list},
            {"new-message", "reply-id"},
        )
        persistent_notification.async_create.assert_not_called()

    def test_manual_test_event_uses_newest_incoming_message(self) -> None:
        scope = models.MessageScope("class-id", "pupil-id")
        outgoing = models.WibiMessage.from_payload(
            {"id": "outgoing", "content": "Sent by me", "isOwned": True}, scope
        )
        newest_incoming = models.WibiMessage.from_payload(
            {"id": "newest", "content": "Latest school message"}, scope
        )
        older_incoming = models.WibiMessage.from_payload(
            {"id": "older", "content": "Earlier school message"}, scope
        )
        assert outgoing is not None
        assert newest_incoming is not None
        assert older_incoming is not None
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

        fired = notifications.async_fire_test_event(
            hass, [outgoing, newest_incoming, older_incoming]
        )

        self.assertTrue(fired)
        hass.bus.async_fire.assert_called_once_with(
            "wibi_new_message", newest_incoming.as_dict()
        )

    def test_manual_test_event_requires_an_incoming_message(self) -> None:
        scope = models.MessageScope("class-id", "pupil-id")
        outgoing = models.WibiMessage.from_payload(
            {"id": "outgoing", "content": "Sent by me", "isOwned": True}, scope
        )
        assert outgoing is not None
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

        fired = notifications.async_fire_test_event(hass, [outgoing])

        self.assertFalse(fired)
        hass.bus.async_fire.assert_not_called()

    def test_custom_test_event_fires_edited_yaml(self) -> None:
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
        event_yaml = """event_type: wibi_new_message
data:
  id: custom-id
  topic: Edited topic
  is_read: false
  replies: []
"""

        notifications.async_fire_custom_test_event(hass, event_yaml)

        hass.bus.async_fire.assert_called_once_with(
            "wibi_new_message",
            {
                "id": "custom-id",
                "topic": "Edited topic",
                "is_read": False,
                "replies": [],
            },
        )

    def test_default_custom_test_event_contains_predefined_message(self) -> None:
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

        notifications.async_fire_custom_test_event(
            hass, notifications.DEFAULT_CUSTOM_TEST_EVENT
        )

        event_type, event_data = hass.bus.async_fire.call_args.args
        self.assertEqual(event_type, "wibi_new_message")
        self.assertEqual(event_data["id"], "5286b509-24bc-4fe9-b528-5ce66c07d264")
        self.assertEqual(event_data["topic"], "Projekt-Nachmittag morgen")
        self.assertEqual(event_data["sender"], "Someone Unknown")
        self.assertEqual(event_data["scope_name"], "2A")
        self.assertFalse(event_data["is_owned"])
        self.assertEqual(event_data["replies"], [])

    def test_custom_test_event_rejects_invalid_structure(self) -> None:
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))

        invalid_events = (
            "not: [valid",
            "- event_type\n- wibi_new_message",
            "event_type: ''\ndata: {}",
            "event_type: wibi_new_message\ndata: message",
        )
        for event_yaml in invalid_events:
            with self.subTest(event_yaml=event_yaml):
                with self.assertRaises(ValueError):
                    notifications.async_fire_custom_test_event(hass, event_yaml)

        hass.bus.async_fire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
