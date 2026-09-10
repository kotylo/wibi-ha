# WiBi Integration Architecture

The integration authenticates Home Assistant with WiBi's SchoolFox-backed API.

## Files

- `__init__.py` — config-entry setup, token validation, daily refresh scheduling, and runtime client storage.
- `api.py` — asynchronous SSO token exchange and API token refresh client.
- `config_flow.py` — Home Assistant UI setup and reauthentication flows.
- `const.py` — integration and endpoint constants.
- `manifest.json` — Home Assistant integration metadata.
- `strings.json` — source UI translations.
- `translations/` — localized Home Assistant UI strings.

The config flow accepts only the final WiBi SSO callback URL. It extracts the one-time synchronization token, exchanges it immediately, and persists the returned authentication payload in the config entry. The integration refreshes and persists authentication at startup and every 23 hours. API behavior belongs in `api.py`; Home Assistant lifecycle behavior belongs in `__init__.py`.
