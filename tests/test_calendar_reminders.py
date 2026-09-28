"""Verify reminder edits preserve existing events and target one occurrence."""

from datetime import date, datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

spec = spec_from_file_location(
    "wibi_calendar_reminders",
    Path(__file__).parents[1] / "custom_components/wibi/calendar_reminders.py",
)
reminders = module_from_spec(spec)
spec.loader.exec_module(reminders)

START = datetime(2026, 9, 29, 7, 30, tzinfo=timezone(timedelta(hours=2)))
NOW = START - timedelta(days=1)
REMINDER = "If there is no rain, pack running shoes and weather-appropriate sportswear."


def event(**changes):
    values = {
        "id": "school_20260929T053000Z",
        "summary": "Before school",
        "start": SimpleNamespace(value=START),
        "end": SimpleNamespace(value=START + timedelta(minutes=15)),
        "description": "Take the school bag.",
        "recurrence": [],
        "recurring_event_id": "school",
        "status": SimpleNamespace(value="confirmed"),
    }
    return SimpleNamespace(**{**values, **changes})


class CalendarReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event = event()
        self.api = SimpleNamespace(
            async_get_event=AsyncMock(return_value=self.event),
            async_patch_event=AsyncMock(),
        )

    async def append(self, **changes):
        arguments = {
            "event_id": self.event.id,
            "expected_start": START,
            "expected_description": "Take the school bag.",
            "reminder": REMINDER,
            "now": NOW,
        }
        return await reminders.async_append_calendar_reminder(
            self.api, "family", **{**arguments, **changes}
        )

    async def test_updates_only_selected_occurrence_description(self):
        result = await self.append()
        expected = "Take the school bag.\n\nWiBi reminder:\n" + REMINDER
        self.api.async_patch_event.assert_awaited_once_with(
            "family", "school_20260929T053000Z", {"description": expected}
        )
        self.assertTrue(result["updated"])
        self.assertEqual(self.event.summary, "Before school")
        self.assertEqual(self.event.start.value, START)

    async def test_empty_original_description(self):
        self.event.description = None
        result = await self.append(expected_description="")
        self.assertEqual(result["description"], "WiBi reminder:\n" + REMINDER)

    async def test_retry_does_not_duplicate_reminder(self):
        self.event.description += "\n\nWiBi reminder:\n" + REMINDER
        result = await self.append()
        self.assertFalse(result["updated"])
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_series_master(self):
        self.event.recurrence = ["RRULE:FREQ=DAILY"]
        with self.assertRaisesRegex(
            reminders.CalendarReminderError, "single occurrence"
        ):
            await self.append()
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_changed_description(self):
        self.event.description = "A parent added another reminder."
        with self.assertRaisesRegex(
            reminders.CalendarReminderError, "description changed"
        ):
            await self.append()
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_moved_occurrence(self):
        self.event.start.value += timedelta(hours=1)
        with self.assertRaisesRegex(reminders.CalendarReminderError, "moved"):
            await self.append()
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_cancelled_occurrence(self):
        self.event.status = SimpleNamespace(value="cancelled")
        with self.assertRaisesRegex(reminders.CalendarReminderError, "cancelled"):
            await self.append()
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_past_occurrence(self):
        with self.assertRaisesRegex(reminders.CalendarReminderError, "already started"):
            await self.append(now=START)
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_all_day_occurrence(self):
        self.event.start.value = START.date()
        with self.assertRaisesRegex(reminders.CalendarReminderError, "all-day"):
            await self.append()
        self.api.async_patch_event.assert_not_awaited()

    async def test_rejects_naive_timestamp_and_empty_reminder(self):
        for change in (
            {"expected_start": START.replace(tzinfo=None)},
            {"reminder": " "},
        ):
            with self.subTest(change=change), self.assertRaises(
                reminders.CalendarReminderError
            ):
                await self.append(**change)
        self.api.async_get_event.assert_not_awaited()

    async def test_rejects_mismatched_id(self):
        with self.assertRaisesRegex(
            reminders.CalendarReminderError, "single occurrence"
        ):
            await self.append(event_id="another_occurrence")
        self.api.async_patch_event.assert_not_awaited()

    async def test_snapshot_retains_occurrence_ids_across_pages(self):
        async def pages():
            yield SimpleNamespace(items=[event(), event(status="cancelled")])
            yield SimpleNamespace(
                items=[
                    event(
                        id="evening",
                        recurring_event_id=None,
                        start=SimpleNamespace(value=NOW + timedelta(hours=12)),
                    )
                ]
            )
            yield SimpleNamespace(
                items=[
                    event(
                        id="all_day",
                        recurring_event_id=None,
                        start=SimpleNamespace(value=date(2026, 9, 30)),
                        end=SimpleNamespace(value=date(2026, 10, 1)),
                    )
                ]
            )

        self.api.async_list_events = AsyncMock(return_value=pages())
        snapshot = await reminders.async_calendar_snapshot(self.api, object())
        self.assertEqual(len(snapshot["events"]), 3)
        self.assertEqual(snapshot["events"][0]["event_id"], "evening")
        occurrence = snapshot["events"][1]
        self.assertEqual(occurrence["event_id"], "school_20260929T053000Z")
        self.assertEqual(occurrence["event_index"], 1)
        self.assertTrue(occurrence["recurring"])
        self.assertEqual(occurrence["description"], "Take the school bag.")
        self.assertTrue(snapshot["events"][2]["all_day"])


if __name__ == "__main__":
    unittest.main()
