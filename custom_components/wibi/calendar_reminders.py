"""Calendar snapshots and description-only reminder edits using Google's API."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class CalendarReminderError(ValueError):
    """A calendar proposal no longer identifies an editable occurrence."""


async def async_calendar_snapshot(api: Any, request: Any) -> dict[str, object]:
    """List expanded occurrences, retaining the IDs needed to edit one instance."""
    response = await api.async_list_events(request)
    events: list[dict[str, object]] = []
    async for page in response:
        for event in page.items:
            if getattr(event.status, "value", event.status) == "cancelled":
                continue
            events.append(
                {
                    "event_id": event.id,
                    "title": event.summary or "",
                    "start": event.start.value.isoformat(),
                    "end": event.end.value.isoformat(),
                    "description": event.description or "",
                    "all_day": not isinstance(event.start.value, datetime),
                    "recurring": bool(event.recurring_event_id),
                }
            )
    events.sort(key=lambda event: str(event["start"]))
    for index, event in enumerate(events):
        event["event_index"] = index
    return {"events": events}


async def async_append_calendar_reminder(
    api: Any,
    calendar_id: str,
    *,
    event_id: str,
    expected_start: datetime,
    expected_description: str,
    reminder: str,
    now: datetime,
) -> dict[str, object]:
    """Recheck a confirmed occurrence and patch only its description."""
    if not reminder.strip():
        raise CalendarReminderError("The reminder must not be empty")
    if expected_start.tzinfo is None or now.tzinfo is None:
        raise CalendarReminderError("Reminder timestamps must include a UTC offset")
    event = await api.async_get_event(calendar_id, event_id)
    # A recurring master carries recurrence rules; expanded instances do not.
    # Never patch a master, which would apply the reminder to every school day.
    if event.id != event_id or event.recurrence:
        raise CalendarReminderError(
            "Select a single occurrence, not a recurring series"
        )
    if getattr(event.status, "value", event.status) == "cancelled":
        raise CalendarReminderError("The calendar event was cancelled")
    if not isinstance(event.start.value, datetime):
        raise CalendarReminderError(
            "Select a timed reminder event, not an all-day event"
        )
    if event.start.value != expected_start or expected_start <= now:
        raise CalendarReminderError(
            "The event moved or has already started; propose it again"
        )
    description = event.description or ""
    addition = "WiBi reminder:\n" + reminder.strip()
    updated_description = (
        expected_description + "\n\n" + addition if expected_description else addition
    )
    # A retry after a successful patch must not append the same reminder twice.
    if description == updated_description:
        return {"event_id": event_id, "updated": False, "description": description}
    if description != expected_description:
        raise CalendarReminderError("The description changed; review a fresh proposal")
    await api.async_patch_event(
        calendar_id, event_id, {"description": updated_description}
    )
    return {"event_id": event_id, "updated": True, "description": updated_description}
