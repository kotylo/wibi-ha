"""On-demand attachment downloads into private Home Assistant local storage."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any

from .api import WibiClient, WibiError

ATTACHMENT_DIRECTORY = "wibi_attachments"


async def async_download_attachments(
    client: WibiClient,
    directory: Path,
    message_id: str,
    file_name: str | None = None,
) -> dict[str, Any]:
    """Download all files (or an exact filename) and return automation data."""
    attachments = await client.async_get_attachments(message_id)
    if file_name is not None:
        attachments = [item for item in attachments if item["name"] == file_name]
        if not attachments:
            raise WibiError("The requested attachment was not found on this message")

    results = []
    for item in attachments:
        name = item["name"]
        data, content_type = await client.async_download_attachment(message_id, name)
        try:
            path = await asyncio.to_thread(
                _save_attachment, directory, message_id, name, data
            )
        except OSError as error:
            raise WibiError("Unable to save the WiBi attachment") from error
        results.append(
            {
                "name": name,
                "file": str(path),
                "content_type": content_type,
                "size": len(data),
                "is_image": content_type.startswith("image/"),
            }
        )
    return {"message_id": message_id, "count": len(results), "attachments": results}


def _save_attachment(directory: Path, message_id: str, name: str, data: bytes) -> Path:
    """Atomically replace a stable private path; never trust remote filenames."""
    # Hash the full identity to avoid collisions, traversal, and reserved filenames.
    identity = sha256(f"{message_id}\0{name}".encode()).hexdigest()
    safe_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)[-100:].strip(" .")
    if re.fullmatch(
        r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])", safe_name.split(".")[0]
    ):
        safe_name = f"_{safe_name}"
    directory = directory / identity
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / (safe_name or "attachment")
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
