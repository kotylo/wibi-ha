# WiBi Home Assistant Integration

WiBi Home Assistant Integration is a custom integration for connecting Home Assistant with the WiBi app. The long-term goal is to expose WiBi messages in Home Assistant so they can be displayed, automated, and used for text-to-speech.

## Current status

The first iteration implements authentication through the **Stadt Wien-Konto** single sign-on flow:

1. In Home Assistant, go to **Settings → Devices & services → Add integration** and select **WiBi**.
2. Copy the displayed sign-in URL into a new browser tab.
3. Sign in and approve the request with the Stadt Wien authentication app.
4. When the browser reaches the WiBi success page, copy its complete address immediately.
5. Paste that address into the Home Assistant setup form.

Home Assistant exchanges the short-lived callback code for a WiBi API token. The token is stored in the Home Assistant config entry, refreshed when the integration loads, and refreshed again every 23 hours to keep the session active. Rotated tokens are persisted automatically. Passwords and Stadt Wien cookies are never handled or stored by the integration.

Message retrieval, Home Assistant entities, TTS support, and interactions with WiBi will be added in later iterations.

## Installation for development

Copy `custom_components/wibi` into the `custom_components` directory of a Home Assistant configuration, restart Home Assistant, and add the integration through the UI.

This project uses an undocumented API observed in the public WiBi web client. Upstream authentication behavior may change without notice.
