"""Asynchronous client for the WiBi API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
import logging
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from uuid import UUID

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    API_BASE_URL,
    API_VERSION,
    API_VERSION_HEADER,
    APPLICATION_TYPE,
    AUTH_HEADER,
    SSO_CALLBACK_HOST,
    SSO_CALLBACK_PATH,
)

REQUEST_TIMEOUT = 30
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024

_LOGGER = logging.getLogger(__name__)


class WibiError(Exception):
    """Base exception for WiBi failures."""


class WibiAuthenticationError(WibiError):
    """Raised when WiBi rejects authentication."""


class WibiConnectionError(WibiError):
    """Raised when the WiBi API cannot be reached."""


class WibiInvalidCallbackError(WibiError):
    """Raised when an SSO callback URL is not valid."""


def extract_sync_token(callback_url: str) -> str:
    """Extract and validate the one-time token from a WiBi callback URL."""
    parsed = urlparse(callback_url.strip())
    if parsed.scheme != "https" or parsed.hostname != SSO_CALLBACK_HOST:
        raise WibiInvalidCallbackError("Unexpected callback origin")

    fragment_path, separator, fragment_query = parsed.fragment.partition("?")
    if not separator or fragment_path != SSO_CALLBACK_PATH:
        raise WibiInvalidCallbackError("Unexpected callback path")

    values = parse_qs(fragment_query).get("syncToken", [])
    if len(values) != 1:
        raise WibiInvalidCallbackError("Missing synchronization token")

    try:
        return str(UUID(values[0]))
    except (ValueError, AttributeError) as error:
        raise WibiInvalidCallbackError("Invalid synchronization token") from error


class WibiClient:
    """Client for WiBi authentication and messages."""

    def __init__(
        self,
        session: ClientSession,
        auth: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the client with Home Assistant's shared HTTP session."""
        self._session = session
        self.auth: dict[str, Any] = dict(auth or {})

    @property
    def user(self) -> dict[str, Any]:
        """Return the authenticated WiBi user payload."""
        user = self.auth.get("user")
        return user if isinstance(user, dict) else {}

    async def async_exchange_sso_token(self, sync_token: str) -> dict[str, Any]:
        """Exchange a short-lived SSO synchronization token for API auth."""
        encoded_token = quote(sync_token, safe="")
        response = await self._async_request(
            "GET",
            f"/api/Users/login/syncToken/{encoded_token}",
            params={"applicationType": APPLICATION_TYPE},
            authentication_statuses={400, 401, 403},
        )
        if not isinstance(response, dict):
            raise WibiError("WiBi returned an unexpected authentication response")
        return self._set_auth(response)

    async def async_refresh_auth(self) -> dict[str, Any]:
        """Validate and refresh the persisted API token."""
        token = self.auth.get("token")
        if not isinstance(token, str) or not token:
            raise WibiAuthenticationError("No API token is available")

        response = await self._async_request(
            "GET",
            "/api/Users/login/token",
            headers={AUTH_HEADER: token},
            authentication_statuses={400, 401, 403},
        )
        if not isinstance(response, dict):
            raise WibiError("WiBi returned an unexpected authentication response")
        return self._set_auth(response)

    async def async_get_inventory(
        self, school_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the classes and pupils available to the authenticated user."""
        params = {"schoolId": school_id} if school_id else None
        response = await self._async_request(
            "GET", "/api/Common/Inventory", params=params, headers=self._auth_headers()
        )
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise WibiError("WiBi returned an unexpected inventory response")
        return response

    async def async_get_messages(
        self, school_class_id: str, pupil_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return every current message in one class/pupil scope."""
        filters = [
            "Deleted eq false",
            f"SchoolClassId eq '{_odata_string(school_class_id)}'",
        ]
        if pupil_id:
            filters.append(f"PupilId eq '{_odata_string(pupil_id)}'")

        messages: list[dict[str, Any]] = []
        previous_page_ids: tuple[str, ...] | None = None
        offset = 0
        page_size = 50
        while True:
            response = await self._async_request(
                "GET",
                "/tables/Messages",
                params={
                    "$filter": " and ".join(filters),
                    "$orderby": "UpdatedAt desc",
                    "$top": str(page_size),
                    "$skip": str(offset),
                    "$inlinecount": "allpages",
                },
                headers=self._auth_headers(),
            )
            if not isinstance(response, dict) or not isinstance(
                response.get("results"), list
            ):
                raise WibiError("WiBi returned an unexpected messages response")

            page = [item for item in response["results"] if isinstance(item, dict)]
            page_ids = tuple(str(item.get("id")) for item in page)
            if page and page_ids == previous_page_ids:
                raise WibiError("WiBi message pagination did not advance")
            previous_page_ids = page_ids
            messages.extend(page)
            offset += len(page)
            total = response.get("count")
            if (
                not page
                or len(page) < page_size
                or (isinstance(total, int) and offset >= total)
            ):
                break
        await self._async_attach_message_replies(messages, pupil_id)
        return messages

    async def async_get_message_replies(
        self, message_id: str, pupil_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return direct-answer replies associated with one message."""
        group_filters = [f"MessageId eq '{_odata_string(message_id)}'"]
        if pupil_id:
            group_filters.append(f"PupilId eq '{_odata_string(pupil_id)}'")

        groups: list[dict[str, Any]] = []
        previous_page_ids: tuple[str, ...] | None = None
        offset = 0
        page_size = 50
        while True:
            response = await self._async_request(
                "GET",
                "/tables/InstantMessageGroups",
                params={
                    "$filter": " and ".join(group_filters),
                    "$orderby": "UpdatedAt desc",
                    "$top": str(page_size),
                    "$skip": str(offset),
                    "$inlinecount": "allpages",
                },
                headers=self._auth_headers(),
            )
            page, total = _collection_page(
                response, "WiBi returned unexpected instant message groups"
            )
            page_ids = tuple(str(item.get("id")) for item in page)
            if page and page_ids == previous_page_ids:
                raise WibiError("WiBi instant message group pagination did not advance")
            previous_page_ids = page_ids
            groups.extend(page)
            offset += len(page)
            if (
                not page
                or len(page) < page_size
                or (isinstance(total, int) and offset >= total)
            ):
                break

        reply_pages = await asyncio.gather(
            *(
                self.async_get_instant_messages(str(group["id"]), pupil_id)
                for group in groups
                if group.get("id")
            )
        )
        return [reply for page in reply_pages for reply in page]

    async def async_get_instant_messages(
        self, group_id: str, pupil_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the direct-answer replies in one message group."""
        filters = [f"InstantMessageGroupId eq '{_odata_string(group_id)}'"]
        if pupil_id:
            filters.append(f"PupilId eq '{_odata_string(pupil_id)}'")
        response = await self._async_request(
            "GET",
            "/tables/InstantMessages",
            params={
                "$filter": " and ".join(filters),
                "$orderby": "CreatedAt desc",
            },
            headers=self._auth_headers(),
        )
        page, _total = _collection_page(
            response, "WiBi returned unexpected instant messages"
        )
        return page

    async def _async_attach_message_replies(
        self, messages: list[dict[str, Any]], pupil_id: str | None
    ) -> None:
        """Add direct-answer payloads to messages that have replies."""
        candidates = [
            message
            for message in messages
            if message.get("id") and _has_message_replies(message)
        ]
        if not candidates:
            return

        reply_pages = await asyncio.gather(
            *(
                self.async_get_message_replies(str(message["id"]), pupil_id)
                for message in candidates
            )
        )
        for message, replies in zip(candidates, reply_pages, strict=True):
            message["replies"] = replies

    async def async_get_message(self, message_id: str) -> dict[str, Any]:
        """Return a single current message."""
        encoded_id = quote(message_id, safe="")
        response = await self._async_request(
            "GET",
            f"/tables/Messages/{encoded_id}",
            headers=self._auth_headers(),
        )
        if not isinstance(response, dict):
            raise WibiError("WiBi returned an unexpected message response")
        return response

    async def async_get_attachments(self, message_id: str) -> list[dict[str, Any]]:
        """List attachment metadata without marking the message as read."""
        response = await self._async_request(
            "GET",
            f"/api/Files/Messages/{quote(message_id, safe='')}",
            headers=self._auth_headers(),
        )
        if not isinstance(response, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and item["name"]
            for item in response
        ):
            raise WibiError("WiBi returned unexpected attachment metadata")
        return response

    async def async_download_attachment(
        self, message_id: str, file_name: str
    ) -> tuple[bytes, str]:
        """Download a file without forwarding WiBi credentials to its file host."""
        url = f"{API_BASE_URL}/api/Files/Messages/{quote(message_id, safe='')}"
        params: dict[str, str] | None = {"fileName": file_name}
        headers = {API_VERSION_HEADER: API_VERSION, **self._auth_headers()}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                for attempt in range(2):
                    async with self._session.get(
                        url, params=params, headers=headers, allow_redirects=False
                    ) as response:
                        if response.status in {401, 403}:
                            raise WibiAuthenticationError(
                                "WiBi rejected the credentials"
                            )
                        if (
                            response.status in {301, 302, 303, 307, 308}
                            and attempt == 0
                        ):
                            url = response.headers.get("Location", "")
                            if not _is_attachment_redirect(url):
                                raise WibiError(
                                    "WiBi returned an unsupported attachment redirect"
                                )
                            # The file service uses the returned URL's authorization.
                            # X-ZUMO-AUTH must only be sent to the WiBi API origin.
                            params = None
                            headers = {}
                            continue
                        return await self._async_read_attachment(response)
        except (TimeoutError, ClientError) as error:
            raise WibiConnectionError(
                "Unable to download the WiBi attachment"
            ) from error
        raise WibiError("WiBi attachment download did not complete")

    @staticmethod
    async def _async_read_attachment(response: ClientResponse) -> tuple[bytes, str]:
        """Read a bounded binary response, including chunked transfer bodies."""
        if response.status != 200:
            raise WibiError(f"WiBi attachment download returned HTTP {response.status}")
        if (
            response.content_length is not None
            and response.content_length > MAX_ATTACHMENT_BYTES
        ):
            raise WibiError("WiBi attachment exceeds the 50 MiB limit")
        data = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_ATTACHMENT_BYTES:
                raise WibiError("WiBi attachment exceeds the 50 MiB limit")
        return bytes(data), response.content_type

    async def async_get_message_recipients(
        self, message_id: str, pupil_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return acknowledgement records associated with one message."""
        filters = [f"MessageId eq '{_odata_string(message_id)}'"]
        if pupil_id:
            filters.append(f"PupilId eq '{_odata_string(pupil_id)}'")
        response = await self._async_request(
            "GET",
            "/tables/MessageRelatedPupils",
            params={"$filter": " and ".join(filters), "$top": "50"},
            headers=self._auth_headers(),
        )
        if isinstance(response, dict):
            response = response.get("results")
        if not isinstance(response, list) or not all(
            isinstance(item, dict) for item in response
        ):
            raise WibiError("WiBi returned unexpected message recipient information")
        return response

    async def async_confirm_message(self, message_id: str) -> dict[str, Any]:
        """Acknowledge a message as the authenticated user."""
        message = await self.async_get_message(message_id)
        if message.get("isOwned") is True:
            raise WibiError("Owned messages cannot be acknowledged")
        if message.get("isSigned") is True:
            _LOGGER.debug("WiBi message %s is already acknowledged", message_id)
            return message

        user_id = self.user.get("id")
        if not user_id:
            raise WibiError("WiBi did not provide the authenticated user ID")

        pupil_id = message.get("pupilId")
        recipients = await self.async_get_message_recipients(
            message_id,
            str(pupil_id) if pupil_id else None,
        )
        _LOGGER.debug(
            "WiBi message %s has %d acknowledgement record(s)",
            message_id,
            len(recipients),
        )
        recipient = _recipient_record(recipients, str(user_id))
        if recipient.get("signedByUserId"):
            return recipient

        recipient_id = recipient.get("id")
        if not recipient_id:
            _LOGGER.warning(
                "WiBi acknowledgement record is missing an ID; fields=%s",
                sorted(recipient),
            )
            raise WibiError("WiBi did not provide an acknowledgement record ID")

        update = {
            **recipient,
            "signedByUserId": user_id,
            "signingDate": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        encoded_recipient_id = quote(str(recipient_id), safe="")
        response = await self._async_request(
            "PATCH",
            f"/tables/MessageRelatedPupils/{encoded_recipient_id}",
            headers=self._auth_headers(),
            json_body=update,
        )
        if response is None:
            return update
        if not isinstance(response, dict):
            raise WibiError("WiBi returned an unexpected acknowledgement response")
        return response

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
        authentication_statuses: set[int] | None = None,
    ) -> Any:
        request_headers = {API_VERSION_HEADER: API_VERSION}
        if headers:
            request_headers.update(headers)

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method,
                    f"{API_BASE_URL}{path}",
                    params=params,
                    headers=request_headers,
                    json=json_body,
                ) as response:
                    _LOGGER.debug(
                        "WiBi API %s %s returned HTTP %d",
                        method,
                        path,
                        response.status,
                    )
                    return await self._async_decode_response(
                        response, authentication_statuses or {401, 403}
                    )
        except (TimeoutError, ClientError) as error:
            raise WibiConnectionError("Unable to reach the WiBi API") from error

    @staticmethod
    async def _async_decode_response(
        response: ClientResponse, authentication_statuses: set[int]
    ) -> Any:
        if response.status in authentication_statuses:
            raise WibiAuthenticationError("WiBi rejected the credentials")
        if response.status >= 400:
            raise WibiError(f"WiBi API returned HTTP {response.status}")
        if response.status == 204:
            return None

        try:
            payload = await response.json()
        except (ClientError, ValueError) as error:
            raise WibiError("WiBi returned an invalid response") from error
        if not isinstance(payload, (dict, list)):
            raise WibiError("WiBi returned an unexpected response")
        return payload

    def _auth_headers(self) -> dict[str, str]:
        """Build API headers for the currently authenticated user."""
        token = self.auth.get("token")
        if not isinstance(token, str) or not token:
            raise WibiAuthenticationError("No API token is available")
        return {AUTH_HEADER: token}

    def _set_auth(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = payload.get("token")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(payload.get("user"), dict)
        ):
            raise WibiError("WiBi returned incomplete authentication data")
        self.auth = payload
        return payload


def _odata_string(value: str) -> str:
    """Escape a value for a quoted OData string literal."""
    return str(value).replace("'", "''")


def _collection_page(
    response: Any, error_message: str
) -> tuple[list[dict[str, Any]], int | None]:
    """Normalize an OData page or direct list response."""
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)], None
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise WibiError(error_message)
    total = response.get("count")
    return (
        [item for item in response["results"] if isinstance(item, dict)],
        total if isinstance(total, int) else None,
    )


def _has_message_replies(payload: Mapping[str, Any]) -> bool:
    """Return whether a message advertises one or more direct answers."""
    count = payload.get("instantMessagesCount")
    if isinstance(count, bool):
        return count
    if isinstance(count, int):
        return count > 0
    if isinstance(count, str):
        try:
            return int(count) > 0
        except ValueError:
            pass
    return payload.get("hasUnreadInstantMessages") is True


def _recipient_record(recipients: list[dict[str, Any]], user_id: str) -> dict[str, Any]:
    """Select the acknowledgement record belonging to the current user."""
    matching = [
        recipient
        for recipient in recipients
        if user_id in _string_list(recipient.get("userRecipientsIds"))
    ]
    if len(matching) == 1:
        return dict(matching[0])
    if not matching and len(recipients) == 1:
        _LOGGER.debug(
            "Using the only WiBi acknowledgement record because its recipient "
            "list did not contain the current user"
        )
        return dict(recipients[0])

    _LOGGER.warning(
        "Unable to select WiBi acknowledgement record: records=%d matches=%d "
        "field_sets=%s",
        len(recipients),
        len(matching),
        [sorted(recipient) for recipient in recipients],
    )
    raise WibiError("WiBi did not provide a unique acknowledgement record")


def _string_list(value: Any) -> list[str]:
    """Normalize a possible API list to strings without accepting scalars."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _is_attachment_redirect(url: str) -> bool:
    """Accept only the HTTPS file service observed in WiBi's download flow."""
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "getfile.foxeducation.com"
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False
