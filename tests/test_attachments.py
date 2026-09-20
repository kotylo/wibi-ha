"""Attachment API, storage, and automation response regression tests."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import api

spec = spec_from_file_location(
    "wibi_test_api.attachments",
    Path(__file__).parents[1] / "custom_components/wibi/attachments.py",
)
attachments = module_from_spec(spec)
sys.modules[spec.name] = attachments
spec.loader.exec_module(attachments)


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_redirect_does_not_forward_credentials(self):
        async def chunks(_size):
            yield b"image"

        initial = AsyncMock()
        initial.__aenter__.return_value = SimpleNamespace(
            status=302,
            headers={
                "Location": "https://getfile.foxeducation.com/file?signature=test"
            },
        )
        final = AsyncMock()
        final.__aenter__.return_value = SimpleNamespace(
            status=200,
            content_length=5,
            content_type="image/jpeg",
            content=SimpleNamespace(iter_chunked=chunks),
        )
        session = SimpleNamespace(get=Mock(side_effect=[initial, final]))
        client = api.WibiClient(session, {"token": "secret"})
        self.assertEqual(
            await client.async_download_attachment("m", "f"), (b"image", "image/jpeg")
        )
        second = session.get.call_args_list[1]
        self.assertEqual(second.kwargs["headers"], {})
        self.assertIsNone(second.kwargs["params"])
        self.assertFalse(second.kwargs["allow_redirects"])
        for url in (
            "http://getfile.foxeducation.com/f",
            "https://evil.example/f",
            "https://getfile.foxeducation.com.evil.example/f",
            "https://user@getfile.foxeducation.com/f",
            "https://getfile.foxeducation.com:123/f",
        ):
            self.assertFalse(api._is_attachment_redirect(url))
        initial.__aenter__.return_value.headers["Location"] = "https://evil.example/f"
        session.get = Mock(return_value=initial)
        with self.assertRaises(api.WibiError):
            await client.async_download_attachment("m", "f")
        session.get.assert_called_once()

    async def test_metadata_empty_and_invalid(self):
        client = api.WibiClient(object(), {"token": "secret"})
        client._async_request = AsyncMock(return_value=[])
        self.assertEqual(await client.async_get_attachments("a/b"), [])
        self.assertEqual(
            client._async_request.call_args.args[1], "/api/Files/Messages/a%2Fb"
        )
        client._async_request.return_value = [{"size": 1}]
        with self.assertRaises(api.WibiError):
            await client.async_get_attachments("message")

    async def test_download_binary_and_authentication(self):
        async def chunks(_size):
            yield b"%PDF-"
            yield b"sample"

        response = SimpleNamespace(
            status=200,
            headers={},
            content_length=None,
            content_type="application/pdf",
            content=SimpleNamespace(iter_chunked=chunks),
        )
        context = AsyncMock()
        context.__aenter__.return_value = response
        session = SimpleNamespace(get=Mock(return_value=context))
        client = api.WibiClient(session, {"token": "secret"})
        data, mime = await client.async_download_attachment("a/b", "file & name.pdf")
        self.assertEqual((data, mime), (b"%PDF-sample", "application/pdf"))
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["params"], {"fileName": "file & name.pdf"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["X-ZUMO-AUTH"], "secret")
        for status in (401, 403):
            response.status = status
            with self.assertRaises(api.WibiAuthenticationError):
                await client.async_download_attachment("m", "f")
        for status in (302, 404, 500):
            response.status = status
            with self.assertRaises(api.WibiError):
                await client.async_download_attachment("m", "f")
        response.status = 200
        with patch.object(api, "MAX_ATTACHMENT_BYTES", 5):
            with self.assertRaises(api.WibiError):
                await client.async_download_attachment("m", "f")
            response.content_length = 10
            with self.assertRaises(api.WibiError):
                await client.async_download_attachment("m", "f")

    async def test_download_response_empty_selection_and_paths(self):
        client = SimpleNamespace(
            async_get_attachments=AsyncMock(
                return_value=[{"name": "../../image.jpg"}, {"name": "a.pdf"}]
            ),
            async_download_attachment=AsyncMock(
                side_effect=[
                    (b"image", "image/jpeg"),
                    (b"%PDF-test", "application/pdf"),
                ]
            ),
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result = await attachments.async_download_attachments(
                client, root, "../../message"
            )
            self.assertEqual(result["count"], 2)
            self.assertTrue(result["attachments"][0]["is_image"])
            self.assertFalse(result["attachments"][1]["is_image"])
            for item in result["attachments"]:
                file = Path(item["file"])
                self.assertEqual(file.parent.parent, root)
                self.assertEqual(file.stat().st_size, item["size"])
            with self.assertRaises(api.WibiError):
                await attachments.async_download_attachments(
                    client, root, "m", "missing"
                )
            client.async_get_attachments.return_value = []
            empty = await attachments.async_download_attachments(client, root, "m")
            self.assertEqual(empty["attachments"], [])

    async def test_exact_selection(self):
        client = SimpleNamespace(
            async_get_attachments=AsyncMock(
                return_value=[{"name": "one.pdf"}, {"name": "two.pdf"}]
            ),
            async_download_attachment=AsyncMock(
                return_value=(b"pdf", "application/pdf")
            ),
        )
        with TemporaryDirectory() as temp:
            result = await attachments.async_download_attachments(
                client, Path(temp), "m", "two.pdf"
            )
            self.assertEqual(result["count"], 1)
            client.async_download_attachment.assert_awaited_once_with("m", "two.pdf")

    async def test_atomic_replacement_and_collision_resistance(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            first = attachments._save_attachment(root, "m", "a/b.pdf", b"old")
            other = attachments._save_attachment(root, "m", "a?b.pdf", b"other")
            self.assertNotEqual(first, other)
            again = attachments._save_attachment(root, "m", "a/b.pdf", b"new")
            self.assertEqual(first, again)
            self.assertEqual(first.read_bytes(), b"new")
            self.assertEqual(len([p for p in root.rglob("*") if p.is_file()]), 2)
            with patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    attachments._save_attachment(root, "m", "a/b.pdf", b"broken")
            self.assertEqual(first.read_bytes(), b"new")
            self.assertEqual(len([p for p in root.rglob("*") if p.is_file()]), 2)


if __name__ == "__main__":
    unittest.main()
