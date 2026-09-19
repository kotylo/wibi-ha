# Tests Architecture

This folder contains focused unit tests for WiBi integration behavior that can run without Home Assistant.

## Files

- `test_models.py` — verifies structured plain-text conversion and Telegram-compatible HTML rendering.
- `test_api.py` — verifies `Messages` polling also fetches direct-answer replies from their instant-message groups.
- `test_notifications.py` — verifies incoming messages and replies fire events, including when persistent notifications are disabled.
