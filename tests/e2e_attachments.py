"""Opt-in live attachment smoke test inside a Home Assistant Python environment.

Run with --config /config; add --send-telegram to send one real PDF and JPEG
into the bot's sole configured allowed chat. Credentials never leave memory.
Production configuration and message acknowledgement state are not modified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main(config: Path, send_telegram: bool) -> None:
    from homeassistant.core import HomeAssistant
    from homeassistant.components.telegram_bot.bot import (
        TelegramNotificationService,
        initialize_bot,
    )
    from custom_components.wibi import _async_register_services
    from custom_components.wibi.api import WibiClient
    from custom_components.wibi.coordinator import _message_scopes
    from custom_components.wibi.models import WibiMessage

    entries = json.loads((config / ".storage/core.config_entries").read_text())["data"][
        "entries"
    ]
    wibi = next(e for e in entries if e["domain"] == "wibi")
    telegram = next((e for e in entries if e["domain"] == "telegram_bot"), None)
    with TemporaryDirectory(prefix="wibi-attachment-e2e-") as temporary:
        hass = HomeAssistant(temporary)
        hass.config.allowlist_external_dirs.add(
            str(Path(temporary) / "wibi_attachments")
        )
        async with ClientSession() as session:
            client = WibiClient(session, wibi["data"]["auth"])
            scopes = _message_scopes(await client.async_get_inventory(), client.user)
            messages = []
            selected = {}
            empty = None
            for scope in scopes:
                for payload in await client.async_get_messages(
                    scope.school_class_id, scope.pupil_id
                ):
                    message = WibiMessage.from_payload(payload, scope)
                    if message is None:
                        continue
                    messages.append(message)
                    files = await client.async_get_attachments(message.id)
                    if not files:
                        empty = message.id
                    for file in files:
                        extension = Path(file["name"]).suffix.lower()
                        if extension in (".pdf", ".jpg") and extension not in selected:
                            selected[extension] = (message.id, file["name"])
            assert set(selected) == {
                ".pdf",
                ".jpg",
            }, "Need a PDF and JPEG in current WiBi messages"
            coordinator = SimpleNamespace(
                client=client, data=messages, async_request_refresh=AsyncMock()
            )
            entry = SimpleNamespace(entry_id=wibi["entry_id"])
            _async_register_services(hass, entry, coordinator)
            if empty:
                result = await hass.services.async_call(
                    "wibi",
                    "download_attachments",
                    {"message_id": empty},
                    blocking=True,
                    return_response=True,
                )
                assert result["count"] == 0 and result["attachments"] == []
                print("PASS empty attachment response", flush=True)
            try:
                await hass.services.async_call(
                    "wibi",
                    "download_attachments",
                    {"message_id": "unknown-id"},
                    blocking=True,
                    return_response=True,
                )
            except Exception as error:
                from homeassistant.exceptions import ServiceValidationError

                assert isinstance(error, ServiceValidationError)
                coordinator.async_request_refresh.assert_awaited_once()
            else:
                raise AssertionError("Unknown message ID was accepted")
            print("PASS unknown message validation", flush=True)
            downloaded = []
            for extension, (message_id, name) in selected.items():
                before = await client.async_get_message(message_id)
                result = await hass.services.async_call(
                    "wibi",
                    "download_attachments",
                    {"message_id": message_id, "file_name": name},
                    blocking=True,
                    return_response=True,
                )
                assert result["count"] == 1
                item = result["attachments"][0]
                data = Path(item["file"]).read_bytes()
                assert data.startswith(
                    b"%PDF-" if extension == ".pdf" else b"\xff\xd8\xff"
                )
                after = await client.async_get_message(message_id)
                assert all(
                    before.get(k) == after.get(k)
                    for k in ("isRead", "isSigned", "signingDate")
                )
                downloaded.append(item)
                print(
                    f"PASS action download {extension}: {len(data)} bytes; read/signature state unchanged",
                    flush=True,
                )
            if send_telegram:
                assert telegram is not None, "No Telegram bot configured"
                chats = [
                    s["data"]["chat_id"]
                    for s in telegram.get("subentries", [])
                    if s["subentry_type"] == "allowed_chat_ids"
                ]
                assert len(chats) == 1, "Expected exactly one allowed test destination"
                bot = await hass.async_add_executor_job(
                    initialize_bot, hass, telegram["data"]
                )
                async with bot:
                    notifier = TelegramNotificationService(
                        hass,
                        None,
                        bot,
                        SimpleNamespace(entry_id=telegram["entry_id"]),
                        "html",
                    )
                    for item in downloaded:
                        service = "send_photo" if item["is_image"] else "send_document"
                        result = await notifier.send_file(
                            service,
                            chat_id=chats[0],
                            file=item["file"],
                            caption="<b>WiBi attachment test</b> — "
                            + ("image" if item["is_image"] else "PDF"),
                            parse_mode="html",
                            disable_notification=True,
                        )
                        assert result and all(result.values())
                        print(f"PASS Telegram {service}: upload accepted", flush=True)
        await hass.async_stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--send-telegram", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.config, args.send_telegram))
