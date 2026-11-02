import unittest

from whatsapp_tool.templates import render_message


class RenderMessageTests(unittest.TestCase):
    def test_replaces_supported_placeholders(self) -> None:
        rendered = render_message(
            "Hi {name}: {phone}", name="Amin", phone="+60123456789"
        )
        self.assertEqual(rendered, "Hi Amin: +60123456789")

    def test_preserves_unknown_braces(self) -> None:
        rendered = render_message("Offer {valid_until}", name="Amin")
        self.assertEqual(rendered, "Offer {valid_until}")

    def test_placeholder_matching_is_case_insensitive(self) -> None:
        rendered = render_message("Hi {NAME}", name="Amin")
        self.assertEqual(rendered, "Hi Amin")


if __name__ == "__main__":
    unittest.main()
