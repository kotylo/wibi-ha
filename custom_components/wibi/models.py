"""Data models for WiBi messages."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any


@dataclass(frozen=True, slots=True)
class MessageScope:
    """A class, optionally narrowed to one pupil."""

    school_class_id: str
    pupil_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class WibiMessage:
    """A stable, Home Assistant-friendly representation of a WiBi message."""

    id: str
    topic: str
    content: str
    sender: str
    message_type: str
    updated_at: str | None
    school_class_id: str
    pupil_id: str | None
    scope_name: str | None
    is_owned: bool
    is_read: bool
    is_confirmed: bool
    signature_required: bool
    is_done: bool

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], scope: MessageScope
    ) -> WibiMessage | None:
        """Create a message from the SchoolFox table payload."""
        message_id = payload.get("id")
        if not message_id:
            return None
        info = payload.get("info")
        recipient = info[0] if isinstance(info, list) and info else {}
        if not isinstance(recipient, dict):
            recipient = {}

        return cls(
            id=str(message_id),
            topic=_text(payload.get("topic")),
            content=_html_to_text(_text(payload.get("content"))),
            sender=_text(payload.get("senderName")),
            message_type=_text(payload.get("messageType")) or "Message",
            updated_at=_optional_text(
                payload.get("updatedAt")
                or payload.get("UpdatedAt")
                or payload.get("createdAt")
            ),
            school_class_id=scope.school_class_id,
            pupil_id=scope.pupil_id,
            scope_name=scope.name,
            is_owned=bool(payload.get("isOwned")),
            is_read=bool(payload.get("isRead")),
            is_confirmed=bool(
                payload.get("isSigned") or recipient.get("signedByUserId")
            ),
            signature_required=bool(payload.get("signatureRequired")),
            is_done=bool(payload.get("isDone")),
        )

    @property
    def can_confirm(self) -> bool:
        """Return whether this received message still needs acknowledgement."""
        return not self.is_owned and not self.is_confirmed

    def as_dict(self, *, content_limit: int | None = None) -> dict[str, Any]:
        """Return serializable message data for states and service responses."""
        content = self.content
        if content_limit is not None and len(content) > content_limit:
            content = f"{content[:content_limit].rstrip()}…"
        return {
            "id": self.id,
            "topic": self.topic,
            "content": content,
            "sender": self.sender,
            "message_type": self.message_type,
            "updated_at": self.updated_at,
            "school_class_id": self.school_class_id,
            "pupil_id": self.pupil_id,
            "scope_name": self.scope_name,
            "is_owned": self.is_owned,
            "is_read": self.is_read,
            "is_confirmed": self.is_confirmed,
            "can_confirm": self.can_confirm,
            "signature_required": self.signature_required,
            "is_done": self.is_done,
        }


class _TextExtractor(HTMLParser):
    """Convert the simple HTML used by message bodies to plain text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Collect visible text."""
        if data.strip():
            self.parts.append(data.strip())


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return " ".join(parser.parts)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None
