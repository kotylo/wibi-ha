"""Data models for WiBi messages."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
import re
from typing import Any

_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


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
    content_html: str
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

        content, content_html = _render_content(_text(payload.get("content")))
        return cls(
            id=str(message_id),
            topic=_text(payload.get("topic")),
            content=content,
            content_html=content_html,
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
            "contentHtml": self.content_html,
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


class _ContentRenderer(HTMLParser):
    """Render WiBi HTML as readable text and Telegram-compatible HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.html_parts: list[str] = []
        self._closing_tags: list[tuple[str, str]] = []
        self._ignored_tags: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Translate structural and supported inline formatting tags."""
        tag = tag.lower()
        attributes = dict(attrs)
        if self._ignored_tags:
            if tag not in _VOID_TAGS:
                self._ignored_tags.append(tag)
            return
        if tag in {"head", "script", "style"}:
            self._ignored_tags.append(tag)
            return
        if tag in {"p", "div"}:
            self._block_break()
        elif tag == "br":
            self._append("\n")
            return
        elif tag == "hr":
            self._block_break()
            return
        elif tag == "img":
            alt = attributes.get("alt")
            if alt:
                self.handle_data(alt)
            return
        elif tag in _VOID_TAGS:
            return
        elif tag in {"ul", "ol"}:
            self._block_break()
        elif tag == "li":
            self._line_break()
            self._append("- ")

        opening, closing = _format_tags(tag, attributes)
        self.html_parts.extend(opening)
        self._closing_tags.append((tag, "".join(reversed(closing))))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Handle self-closing tags."""
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        """Close inline formatting and terminate structural blocks."""
        tag = tag.lower()
        if self._ignored_tags:
            if tag in self._ignored_tags:
                while self._ignored_tags:
                    ignored_tag = self._ignored_tags.pop()
                    if ignored_tag == tag:
                        break
            return
        for index in range(len(self._closing_tags) - 1, -1, -1):
            if self._closing_tags[index][0] == tag:
                while len(self._closing_tags) > index:
                    _, closing = self._closing_tags.pop()
                    self.html_parts.append(closing)
                break

        if tag in {"p", "div", "ul", "ol"}:
            self._block_break()
        elif tag == "li":
            self._line_break()

    def handle_data(self, data: str) -> None:
        """Collect visible text, escaping it in the HTML representation."""
        if self._ignored_tags:
            return
        value = data.replace("\xa0", " ")
        self.text_parts.append(value)
        self.html_parts.append(escape(value, quote=False))

    def _append(self, value: str) -> None:
        self.text_parts.append(value)
        self.html_parts.append(value)

    def _line_break(self) -> None:
        if not _ends_with_newline(self.text_parts):
            self._append("\n")

    def _block_break(self) -> None:
        current = "".join(self.text_parts)
        if current and not current.endswith("\n\n"):
            self._append("\n" if current.endswith("\n") else "\n\n")


def _format_tags(
    tag: str, attributes: dict[str, str | None]
) -> tuple[list[str], list[str]]:
    """Return Telegram-supported opening and closing formatting tags."""
    formats: list[str] = []
    if tag in {"b", "strong"}:
        formats.append("b")
    elif tag in {"i", "em"}:
        formats.append("i")
    elif tag in {"u", "ins"}:
        formats.append("u")
    elif tag in {"s", "strike", "del"}:
        formats.append("s")
    elif tag in {"code", "pre"}:
        formats.append(tag)
    elif tag == "a" and attributes.get("href"):
        href = escape(str(attributes["href"]), quote=True)
        return [f'<a href="{href}">'], ["</a>"]

    style = (attributes.get("style") or "").lower()
    if re.search(r"font-weight\s*:\s*(?:bold(?:er)?|[6-9]00)", style):
        formats.append("b")
    if re.search(r"font-style\s*:\s*italic", style):
        formats.append("i")
    decoration_match = re.search(r"text-decoration(?:-line)?\s*:\s*([^;]+)", style)
    if decoration_match:
        decoration = decoration_match.group(1)
        if "underline" in decoration:
            formats.append("u")
        if "line-through" in decoration:
            formats.append("s")

    formats = list(dict.fromkeys(formats))
    return ([f"<{item}>" for item in formats], [f"</{item}>" for item in formats])


def _ends_with_newline(parts: list[str]) -> bool:
    return bool(parts) and "".join(parts).endswith("\n")


def _render_content(value: str) -> tuple[str, str]:
    parser = _ContentRenderer()
    parser.feed(value)
    parser.close()
    while parser._closing_tags:
        _, closing = parser._closing_tags.pop()
        parser.html_parts.append(closing)
    return _normalize_rendered("".join(parser.text_parts)), _normalize_rendered(
        "".join(parser.html_parts)
    )


def _normalize_rendered(value: str) -> str:
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.splitlines()]
    rendered = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return re.sub(r"<(b|i|u|s|code|pre)></\1>", "", rendered)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None
