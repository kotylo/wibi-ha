"""Tests for WiBi message rendering."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType
import unittest


def _load_models() -> ModuleType:
    """Load the standalone models module without importing Home Assistant."""
    path = Path(__file__).parents[1] / "custom_components" / "wibi" / "models.py"
    spec = spec_from_file_location("wibi_test_models", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the WiBi models module")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


models = _load_models()


class WibiMessageRenderingTests(unittest.TestCase):
    """Verify both public message content representations."""

    def test_paragraphs_lists_and_formatting_are_preserved(self) -> None:
        source = (
            '<p>Hello <span style="font-weight: bolder">'
            '<span style="text-decoration: underline">parents</span></span>:</p>'
            "<ul><li>First</li><li><b>Second</b><br>continued</li></ul>"
            "<p>Bye &amp; thanks</p>"
        )

        message = models.WibiMessage.from_payload(
            {"id": "message-id", "content": source},
            models.MessageScope("class-id"),
        )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(
            message.content,
            "Hello parents:\n\n- First\n- Second\ncontinued\n\nBye & thanks",
        )
        self.assertEqual(
            message.content_html,
            "Hello <b><u>parents</u></b>:\n\n"
            "- First\n- <b>Second</b>\ncontinued\n\nBye &amp; thanks",
        )
        self.assertEqual(message.as_dict()["contentHtml"], message.content_html)

    def test_unsupported_markup_and_empty_formatting_are_removed(self) -> None:
        source = '<p>Safe<script>visible</script><b></b><img alt=" diagram "></p>'

        plain, formatted = models._render_content(source)

        self.assertEqual(plain, "Safe diagram")
        self.assertEqual(formatted, "Safe diagram")


if __name__ == "__main__":
    unittest.main()
