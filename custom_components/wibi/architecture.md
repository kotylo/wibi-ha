# WiBi Integration Architecture

The integration authenticates Home Assistant with WiBi's SchoolFox-backed API and exposes current messages without acknowledging them during polling.

## Files

- `__init__.py` — config-entry lifecycle, token refresh scheduling, platform forwarding, and Home Assistant action registration.
- `api.py` — asynchronous authentication, inventory, message retrieval, and acknowledgement API client.
- `config_flow.py` — Home Assistant UI setup and reauthentication flows.
- `const.py` — integration and endpoint constants.
- `coordinator.py` — five-minute polling and conversion of inventory items into message scopes.
- `models.py` — message/scope models and HTML-to-plain-text conversion for TTS.
- `notifications.py` — new-message event emission and persistent Home Assistant notifications.
- `sensor.py` — message-count sensor with recent messages in state attributes.
- `services.yaml` — UI metadata for reading and confirming message actions.
- `manifest.json` — Home Assistant integration metadata.
- `strings.json` — source UI translations.
- `translations/` — localized Home Assistant UI strings.

The config flow links directly to WiBi sign-in and accepts the final WiBi SSO callback URL. It extracts the one-time synchronization token, exchanges it immediately, and persists the returned authentication payload in the config entry. The integration refreshes and persists authentication at startup and every 23 hours.

The coordinator discovers parent pupil scopes or staff class scopes from WiBi inventory, retrieves all message pages, deduplicates them, and updates the sensor every five minutes. The notifier seeds itself from the initial result, then creates a persistent Home Assistant notification and fires `wibi_new_message` for each subsequently discovered incoming message. Polling is read-only. `wibi.confirm_message` acknowledges an explicit ID; `wibi.confirm_last_message` refreshes first and acknowledges the newest incoming message. Both query `MessageRelatedPupils` by message and pupil, select the logged-in user's recipient record, and run only when explicitly called. API debug logs include endpoints, statuses, record counts, and field names but omit authentication and message content. API behavior belongs in `api.py`; polling belongs in `coordinator.py`; notification behavior belongs in `notifications.py`; Home Assistant lifecycle and actions belong in `__init__.py`.
