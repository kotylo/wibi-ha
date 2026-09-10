"""Asynchronous client for WiBi authentication."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
    """Minimal client responsible for WiBi authentication state."""

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
        response = await self._async_get(
            f"/api/Users/login/syncToken/{encoded_token}",
            params={"applicationType": APPLICATION_TYPE},
        )
        return self._set_auth(response)

    async def async_refresh_auth(self) -> dict[str, Any]:
        """Validate and refresh the persisted API token."""
        token = self.auth.get("token")
        if not isinstance(token, str) or not token:
            raise WibiAuthenticationError("No API token is available")

        response = await self._async_get(
            "/api/Users/login/token",
            headers={AUTH_HEADER: token},
        )
        return self._set_auth(response)

    async def _async_get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {API_VERSION_HEADER: API_VERSION}
        if headers:
            request_headers.update(headers)

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(
                    f"{API_BASE_URL}{path}",
                    params=params,
                    headers=request_headers,
                ) as response:
                    return await self._async_decode_response(response)
        except (TimeoutError, ClientError) as error:
            raise WibiConnectionError("Unable to reach the WiBi API") from error

    @staticmethod
    async def _async_decode_response(response: ClientResponse) -> dict[str, Any]:
        if response.status in (400, 401, 403):
            raise WibiAuthenticationError("WiBi rejected the credentials")
        if response.status >= 400:
            raise WibiError(f"WiBi API returned HTTP {response.status}")

        try:
            payload = await response.json()
        except (ClientError, ValueError) as error:
            raise WibiError("WiBi returned an invalid response") from error
        if not isinstance(payload, dict):
            raise WibiError("WiBi returned an unexpected response")
        return payload

    def _set_auth(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = payload.get("token")
        if not isinstance(token, str) or not token or not isinstance(
            payload.get("user"), dict
        ):
            raise WibiError("WiBi returned incomplete authentication data")
        self.auth = payload
        return payload
