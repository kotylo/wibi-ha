# WiBi Home Assistant Integration

WiBi Home Assistant Integration is a custom integration for connecting Home Assistant with the WiBi app. The long-term goal is to expose WiBi messages in Home Assistant so they can be displayed, automated, and used for text-to-speech.

## Features

- Stadt Wien-Konto SSO with manual phone approval.
- Persisted and automatically refreshed WiBi API authentication.
- All current messages polled every five minutes across the account's pupil or class scopes.
- Plain-text message bodies suitable for Home Assistant text-to-speech templates.
- New-message Home Assistant notifications and automation events, normally within five minutes.
- Explicit message acknowledgement through a Home Assistant action.

## Setup

The first iteration implements authentication through the **Stadt Wien-Konto** single sign-on flow:

1. In Home Assistant, go to **Settings → Devices & services → Add integration** and select **WiBi**.
2. Select the displayed **Open WiBi sign-in** link.
3. Sign in and approve the request with the Stadt Wien authentication app.
4. When the browser reaches the WiBi success page, copy its complete address immediately.
5. Paste that address into the Home Assistant setup form.

Home Assistant exchanges the short-lived callback code for a WiBi API token. The token is stored in the Home Assistant config entry, refreshed when the integration loads, and refreshed again every 23 hours to keep the session active. Rotated tokens are persisted automatically. Passwords and Stadt Wien cookies are never handled or stored by the integration.

After setup, `sensor.wibi_messages` contains the total current message count. Its attributes include unread and unconfirmed counts and the 20 newest messages. Message bodies are converted from HTML to plain text. The complete, untruncated list is available through the `wibi.get_messages` action.

The first synchronization establishes a baseline and does not generate old-message alerts. After that, each newly discovered incoming message creates a persistent notification in Home Assistant and fires a `wibi_new_message` event containing the message fields. WiBi's web client does not expose a reusable live notification connection, so the integration polls every five minutes. The normal maximum detection delay is therefore five minutes, comfortably below 15 minutes.

To forward the event as a push notification through the Home Assistant Companion app, create an automation using your phone's notify action:

```yaml
triggers:
  - trigger: event
    event_type: wibi_new_message
actions:
  - action: notify.mobile_app_your_phone
    data:
      title: "WiBi: {{ trigger.event.data.topic }}"
      message: "{{ trigger.event.data.content }}"
```

Replace `notify.mobile_app_your_phone` with the notify action provided by your phone. Polling, persistent notifications, and automation events do not acknowledge messages.

Polling and retrieving messages never acknowledges them. To acknowledge one message in WiBi, explicitly call `wibi.confirm_message` with the message's `id`:

```yaml
action: wibi.confirm_message
data:
  message_id: "12345678-1234-1234-1234-123456789abc"
```

This has the same external effect as confirming/signing the message in the WiBi web app. The integration refreshes its sensor immediately afterward.

To acknowledge the newest incoming message without looking up its ID, use:

```yaml
action: wibi.confirm_last_message
```

The action refreshes the message list before selecting the newest incoming message. If that message was already confirmed, the operation is harmless and remains idempotent; it will not skip backwards and unexpectedly confirm an older message.

For TTS, request the complete message list into a response variable and select the content you want to announce:

```yaml
- action: wibi.get_messages
  response_variable: wibi_result
- action: tts.speak
  target:
    entity_id: tts.example
  data:
    media_player_entity_id: media_player.example
    message: >-
      {{ wibi_result.messages
         | selectattr('can_confirm')
         | map(attribute='content')
         | join('. ') }}
```

Replace the example TTS and media-player entity IDs with entities from your Home Assistant instance.

## Installation for development

Copy `custom_components/wibi` into the `custom_components` directory of a Home Assistant configuration, restart Home Assistant, and add the integration through the UI.

For repeat deployments over SSH, copy `.env.example` to `.env`, fill in the server values, and run:

```powershell
.\copy-to-server.ps1
```

The script validates that the configured remote path ends in `/custom_components`, excludes generated Python bytecode, validates the uploaded manifest, and installs the component through a staging directory. If WiBi is already installed, it is moved to a timestamped `.wibi-backup-*` directory first. The script does not restart Home Assistant.

For detailed production diagnostics without logging authentication tokens or message bodies, enable the integration's debug logger in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.wibi: debug
```

This project uses an undocumented API observed in the public WiBi web client. Upstream authentication behavior may change without notice.
