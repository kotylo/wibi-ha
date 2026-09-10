"""Asynchronous client for the WiBi API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
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
        return messages

    async def async_get_message(self, message_id: str) -> dict[str, Any]:
        """Return a single current message with its recipient information."""
        encoded_id = quote(message_id, safe="")
        response = await self._async_request(
            "GET",
            f"/tables/Messages/{encoded_id}",
            headers=self._auth_headers(),
        )
        if not isinstance(response, dict):
            raise WibiError("WiBi returned an unexpected message response")
        return response

    async def async_confirm_message(self, message_id: str) -> dict[str, Any]:
        """Acknowledge a message as the authenticated user."""
        message = await self.async_get_message(message_id)
        recipient = _recipient_record(message)
        if recipient.get("signedByUserId"):
            return recipient

        user_id = self.user.get("id")
        recipient_id = recipient.get("id")
        if not user_id or not recipient_id:
            raise WibiError("WiBi did not provide acknowledgement identifiers")
        if message.get("isOwned") is True:
            raise WibiError("Owned messages cannot be acknowledged")

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


def _recipient_record(message: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the current user's acknowledgement record from a message."""
    info = message.get("info")
    if not isinstance(info, list) or not info or not isinstance(info[0], dict):
        raise WibiError("WiBi did not provide message recipient information")
    return dict(info[0])
