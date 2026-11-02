import unittest

from whatsapp_tool.contacts import normalize_phone


class NormalizePhoneTests(unittest.TestCase):
    def test_keeps_international_malaysian_number(self) -> None:
        self.assertEqual(normalize_phone("+60 12-345 6789"), ("60123456789", ""))

    def test_converts_local_malaysian_number(self) -> None:
        self.assertEqual(normalize_phone("012-345 6789"), ("60123456789", ""))

    def test_converts_double_zero_prefix(self) -> None:
        self.assertEqual(normalize_phone("0060123456789"), ("60123456789", ""))

    def test_uses_selected_country_for_local_number_without_zero(self) -> None:
        self.assertEqual(normalize_phone("7123456789", "44"), ("447123456789", ""))

    def test_rejects_letters(self) -> None:
        number, error = normalize_phone("+60 12 ABC 6789")
        self.assertEqual(number, "")
        self.assertIn("unsupported", error)

    def test_rejects_extension(self) -> None:
        number, error = normalize_phone("+60123456789 ext. 4")
        self.assertEqual(number, "")
        self.assertIn("Extensions", error)

    def test_rejects_too_short_number(self) -> None:
        number, error = normalize_phone("1234", "60")
        self.assertEqual(number, "")
        self.assertIn("8 to 15", error)


if __name__ == "__main__":
    unittest.main()
