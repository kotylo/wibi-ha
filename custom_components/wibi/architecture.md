# WiBi Integration Architecture

The integration authenticates Home Assistant with WiBi's SchoolFox-backed API and exposes current messages without acknowledging them during polling.

## Files

- `__init__.py` — config-entry lifecycle, token refresh scheduling, platform forwarding, and Home Assistant action registration.
- `api.py` — asynchronous authentication, inventory, message/direct-answer retrieval, attachment listing/bounded binary download, and acknowledgement API client.
- `attachments.py` — on-demand attachment downloads and atomic private local storage; returns file paths and metadata for automation responses.
- `config_flow.py` — Home Assistant UI setup, notification options, and reauthentication flows.
- `const.py` — integration and endpoint constants.
- `coordinator.py` — five-minute polling and conversion of inventory items into message scopes.
- `models.py` — message/scope/reply models plus structured plain-text and Telegram-compatible HTML rendering.
- `notifications.py` — new-message and incoming-reply event emission plus persistent Home Assistant notifications.
- `sensor.py` — message-count sensor with recent messages in state attributes.
- `services.yaml` — UI metadata for reading, downloading attachments, and confirming message actions.
- `manifest.json` — Home Assistant integration metadata.
- `brand/` — local integration icon served by Home Assistant's Brands API.
- `strings.json` — source UI translations.
- `translations/` — localized Home Assistant UI strings.

The config flow links directly to WiBi sign-in and accepts the final WiBi SSO callback URL. It extracts the one-time synchronization token, exchanges it immediately, and persists the returned authentication payload in the config entry. The options flow controls persistent notifications, which are enabled by default. The integration refreshes and persists authentication at startup and every 23 hours.

The coordinator discovers parent pupil scopes or staff class scopes from WiBi inventory, retrieves all message pages and direct-answer replies, deduplicates them, and updates the sensor every five minutes. The notifier seeds itself from the initial result, then always fires `wibi_new_message` for each subsequently discovered incoming message or reply. It creates a persistent Home Assistant notification for each item only when the `persistent_notifications` option is enabled. Polling is read-only. `wibi.confirm_message` acknowledges an explicit ID; `wibi.confirm_last_message` refreshes first and acknowledges the newest incoming message. Both query `MessageRelatedPupils` by message and pupil, select the logged-in user's recipient record, and run only when explicitly called. API debug logs include endpoints, statuses, record counts, and field names but omit authentication and message content. API behavior belongs in `api.py`; polling belongs in `coordinator.py`; notification behavior belongs in `notifications.py`; Home Assistant lifecycle and actions belong in `__init__.py`.

`wibi.download_attachments` explicitly lists and downloads files for a known original message, optionally selecting an exact filename. It follows the verified HTTPS file-service redirect without forwarding the API token, limits each file to 50 MiB, and returns paths under the private configuration `wibi_attachments/<entry_id>` directory. Polling does not download files. Telegram forwarding uses local file actions and requires the directory in `allowlist_external_dirs`. Direct-answer IDs are not accepted by this message-file API.
