"""Tests for WiBi API message enrichment."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock


def _load_api() -> ModuleType:
    """Load the API module with a small standalone constants module."""
    root = Path(__file__).parents[1] / "custom_components" / "wibi"
    package_name = "wibi_test_api"
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package

    constants = ModuleType(f"{package_name}.const")
    constants.API_BASE_URL = "https://api.example.test"
    constants.API_VERSION = "2.0.0"
    constants.API_VERSION_HEADER = "ZUMO-API-VERSION"
    constants.APPLICATION_TYPE = "Wibi"
    constants.AUTH_HEADER = "X-ZUMO-AUTH"
    constants.SSO_CALLBACK_HOST = "wibi.example.test"
    constants.SSO_CALLBACK_PATH = "/sso-success"
    sys.modules[constants.__name__] = constants

    spec = spec_from_file_location(f"{package_name}.api", root / "api.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the WiBi API module")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


api = _load_api()


class WibiMessageApiTests(unittest.IsolatedAsyncioTestCase):
    """Verify direct-answer records are included in message retrieval."""

    async def test_get_messages_attaches_direct_replies(self) -> None:
        client = api.WibiClient(object(), {"token": "token"})
        client._async_request = AsyncMock(
            side_effect=[
                {
                    "count": 1,
                    "results": [
                        {
                            "id": "message-id",
                            "instantMessagesCount": 1,
                        }
                    ],
                },
                {
                    "count": 1,
                    "results": [{"id": "group-id"}],
                },
                [
                    {
                        "id": "reply-id",
                        "content": "The answer",
                        "isIncoming": True,
                    }
                ],
            ]
        )

        messages = await client.async_get_messages("class-id", "pupil-id")

        self.assertEqual(
            messages[0]["replies"],
            [
                {
                    "id": "reply-id",
                    "content": "The answer",
                    "isIncoming": True,
                }
            ],
        )
        requests = client._async_request.await_args_list
        self.assertEqual(len(requests), 3)
        self.assertEqual(
            requests[0].kwargs["params"]["$filter"],
            "Deleted eq false and SchoolClassId eq 'class-id' "
            "and PupilId eq 'pupil-id'",
        )
        self.assertEqual(
            requests[1].kwargs["params"]["$filter"],
            "MessageId eq 'message-id' and PupilId eq 'pupil-id'",
        )
        self.assertEqual(
            requests[2].kwargs["params"]["$filter"],
            "InstantMessageGroupId eq 'group-id' and PupilId eq 'pupil-id'",
        )


if __name__ == "__main__":
    unittest.main()
