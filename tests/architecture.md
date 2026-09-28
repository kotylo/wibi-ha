# Tests Architecture

This folder contains focused unit tests for WiBi integration behavior that can run without Home Assistant.

## Files

- `test_models.py` — verifies structured plain-text conversion and Telegram-compatible HTML rendering.
- `test_calendar_reminders.py` — verifies occurrence snapshots, description-only appends, retry behavior, and rejection of series masters, stale descriptions, moved/cancelled/past/all-day events.
- `test_calendar_services.py` — verifies calendar helper registration, existing Google connection resolution, permissions, time-range validation, edit dispatch, and refresh behavior.
- `test_api.py` — verifies `Messages` polling also fetches direct-answer replies from their instant-message groups.
- `test_attachments.py` — verifies attachment metadata, authenticated downloads, safe redirects without token forwarding, size limits, response selection, and atomic safe file storage.
- `e2e_attachments.py` — opt-in live test in a Home Assistant Python environment; uses existing credentials, calls the registered WiBi action in an isolated instance, validates real PDF/JPEG data and unchanged acknowledgement state, and optionally sends samples through Telegram's installed file sender.
- `test_notifications.py` — verifies incoming messages and replies fire events, including when persistent notifications are disabled, and that cached and editable-YAML manual tests fire valid events.
