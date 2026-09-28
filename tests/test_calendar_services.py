"""Verify the calendar adapter's registration, connection checks, and dispatch."""

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class HomeAssistantError(Exception):
    pass


class ServiceValidationError(HomeAssistantError):
    pass


class Unauthorized(HomeAssistantError):
    def __init__(self, **kwargs):
        super().__init__(str(kwargs))


class ApiException(Exception):
    pass


def load_services():
    root = Path(__file__).parents[1] / "custom_components/wibi"
    names = (
        "homeassistant",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.config_validation",
        "homeassistant.helpers.entity_registry",
        "homeassistant.util",
        "homeassistant.util.dt",
        "homeassistant.auth",
        "homeassistant.auth.permissions",
        "homeassistant.auth.permissions.const",
        "wibi_test_calendar_services",
        "wibi_test_calendar_services.const",
        "gcal_sync",
        "gcal_sync.api",
        "gcal_sync.exceptions",
    )
    modules = {name: ModuleType(name) for name in names}
    for module in modules.values():
        module.__path__ = []
    core = modules["homeassistant.core"]
    core.HomeAssistant = core.ServiceCall = object
    core.SupportsResponse = SimpleNamespace(ONLY="only", OPTIONAL="optional")
    exceptions = modules["homeassistant.exceptions"]
    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ServiceValidationError = ServiceValidationError
    exceptions.Unauthorized = Unauthorized
    cv = modules["homeassistant.helpers.config_validation"]
    cv.entity_domain = lambda domain: lambda value: value
    cv.datetime = (
        lambda value: datetime.fromisoformat(value) if isinstance(value, str) else value
    )
    cv.string = str
    modules["homeassistant.auth.permissions.const"].POLICY_READ = "read"
    modules["homeassistant.auth.permissions.const"].POLICY_CONTROL = "control"
    modules["wibi_test_calendar_services.const"].DOMAIN = "wibi"
    modules["homeassistant.util.dt"].now = lambda: datetime(
        2026, 9, 28, tzinfo=timezone.utc
    )
    modules["gcal_sync.api"].ListEventsRequest = lambda **kwargs: SimpleNamespace(
        **kwargs
    )
    modules["gcal_sync.exceptions"].ApiException = ApiException
    with patch.dict(sys.modules, modules):
        for filename in ("calendar_reminders", "calendar_services"):
            spec = spec_from_file_location(
                f"wibi_test_calendar_services.{filename}", root / f"{filename}.py"
            )
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
    return module, modules


services, modules = load_services()


class CalendarServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = SimpleNamespace(async_patch_event=AsyncMock())
        self.entity = SimpleNamespace(
            calendar_id="family@group.calendar.google.com",
            entity_description=SimpleNamespace(read_only=False),
            coordinator=SimpleNamespace(async_refresh=AsyncMock()),
        )
        self.registered = SimpleNamespace(
            platform="google", config_entry_id="google-entry"
        )
        self.registry = SimpleNamespace(async_get=Mock(return_value=self.registered))
        services.er.async_get = Mock(return_value=self.registry)
        self.hass = SimpleNamespace(
            data={
                "calendar": SimpleNamespace(get_entity=Mock(return_value=self.entity))
            },
            config_entries=SimpleNamespace(
                async_get_entry=Mock(
                    return_value=SimpleNamespace(
                        runtime_data=SimpleNamespace(service=self.api)
                    )
                )
            ),
            services=SimpleNamespace(async_register=Mock(), async_remove=Mock()),
            auth=SimpleNamespace(async_get_user=AsyncMock()),
        )
        services.async_register_calendar_services(self.hass)
        self.handlers = {
            call.args[1]: call.args[2]
            for call in self.hass.services.async_register.call_args_list
        }
        self.schemas = {
            call.args[1]: call.kwargs["schema"]
            for call in self.hass.services.async_register.call_args_list
        }
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def call(self, name, **data):
        validated = self.schemas[name]({"entity_id": "calendar.family", **data})
        return SimpleNamespace(data=validated, context=SimpleNamespace(user_id=None))

    async def test_snapshot_uses_existing_google_client_and_bounded_range(self):
        call = self.call(
            "get_calendar_events",
            start_date_time="2026-09-28T00:00:00+02:00",
            end_date_time="2026-10-05T00:00:00+02:00",
        )
        with patch.object(
            services, "async_calendar_snapshot", AsyncMock(return_value={"events": []})
        ) as helper:
            self.assertEqual(
                await self.handlers["get_calendar_events"](call), {"events": []}
            )
        api, request = helper.await_args.args
        self.assertIs(api, self.api)
        self.assertEqual(request.calendar_id, self.entity.calendar_id)
        self.assertEqual(request.start_time, call.data["start_date_time"])
        self.assertEqual(request.end_time, call.data["end_date_time"])

    async def test_rejects_read_only_and_other_provider(self):
        self.entity.entity_description.read_only = True
        with self.assertRaisesRegex(ServiceValidationError, "allow writes"):
            services._google_calendar(self.hass, "calendar.family")
        self.entity.entity_description.read_only = False
        self.registered.platform = "local_calendar"
        with self.assertRaisesRegex(ServiceValidationError, "Google Calendar"):
            services._google_calendar(self.hass, "calendar.family")

    async def test_rejects_unloaded_or_incompatible_connection(self):
        self.hass.data = {}
        with self.assertRaises(ServiceValidationError):
            services._google_calendar(self.hass, "calendar.family")
        self.hass.data = {"calendar": SimpleNamespace(get_entity=lambda _: self.entity)}
        self.hass.config_entries.async_get_entry.return_value.runtime_data = None
        with self.assertRaisesRegex(ServiceValidationError, "version"):
            services._google_calendar(self.hass, "calendar.family")

    async def test_rejects_invalid_time_range(self):
        for start, end in (
            ("2026-09-29T00:00:00", "2026-09-30T00:00:00"),
            ("2026-09-30T00:00:00+02:00", "2026-09-29T00:00:00+02:00"),
        ):
            call = self.call(
                "get_calendar_events", start_date_time=start, end_date_time=end
            )
            with self.assertRaises(ServiceValidationError):
                await self.handlers["get_calendar_events"](call)

    def reminder_call(self):
        return self.call(
            "append_calendar_reminder",
            event_id="school_occurrence",
            expected_start="2026-09-29T07:30:00+02:00",
            expected_description="School bag",
            reminder="Running shoes",
        )

    async def test_edit_passes_snapshot_checks_and_refreshes(self):
        call = self.reminder_call()
        with patch.object(
            services,
            "async_append_calendar_reminder",
            AsyncMock(return_value={"updated": True}),
        ) as helper:
            await self.handlers["append_calendar_reminder"](call)
        self.assertEqual(helper.await_args.kwargs["event_id"], "school_occurrence")
        self.assertEqual(helper.await_args.kwargs["expected_description"], "School bag")
        self.assertEqual(helper.await_args.kwargs["reminder"], "Running shoes")
        self.entity.coordinator.async_refresh.assert_awaited_once()

    async def test_stale_edit_is_validation_error_without_refresh(self):
        with patch.object(
            services,
            "async_append_calendar_reminder",
            AsyncMock(
                side_effect=services.CalendarReminderError("description changed")
            ),
        ):
            with self.assertRaisesRegex(ServiceValidationError, "description changed"):
                await self.handlers["append_calendar_reminder"](self.reminder_call())
        self.entity.coordinator.async_refresh.assert_not_awaited()

    async def test_refresh_failure_does_not_report_successful_patch_as_failed(self):
        self.entity.coordinator.async_refresh.side_effect = HomeAssistantError(
            "offline"
        )
        with patch.object(
            services,
            "async_append_calendar_reminder",
            AsyncMock(return_value={"updated": True}),
        ):
            self.assertTrue(
                (await self.handlers["append_calendar_reminder"](self.reminder_call()))[
                    "updated"
                ]
            )

    async def test_entity_permissions_checked_before_edit(self):
        call = self.reminder_call()
        call.context.user_id = "restricted-user"
        permissions = SimpleNamespace(check_entity=Mock(return_value=False))
        self.hass.auth.async_get_user.return_value = SimpleNamespace(
            permissions=permissions
        )
        with self.assertRaises(Unauthorized):
            await self.handlers["append_calendar_reminder"](call)
        permissions.check_entity.assert_called_once_with("calendar.family", "control")
        self.api.async_patch_event.assert_not_awaited()

    async def test_unregister_removes_both_actions(self):
        services.async_unregister_calendar_services(self.hass)
        self.assertEqual(self.hass.services.async_remove.call_count, 2)


if __name__ == "__main__":
    unittest.main()
