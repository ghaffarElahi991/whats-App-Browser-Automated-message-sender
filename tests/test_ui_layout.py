import unittest

from whatsapp_tool.ui import calculate_window_geometry


class WindowGeometryTests(unittest.TestCase):
    def test_large_screen_uses_preferred_size(self) -> None:
        self.assertEqual(calculate_window_geometry(1920, 1080)[:2], (1180, 820))

    def test_small_mac_screen_leaves_vertical_room(self) -> None:
        width, height, x_position, y_position = calculate_window_geometry(1280, 800)
        self.assertEqual((width, height), (1180, 680))
        self.assertGreaterEqual(x_position, 0)
        self.assertGreaterEqual(y_position, 24)

    def test_very_small_screen_never_produces_negative_position(self) -> None:
        _width, _height, x_position, y_position = calculate_window_geometry(600, 500)
        self.assertGreaterEqual(x_position, 0)
        self.assertGreaterEqual(y_position, 24)


if __name__ == "__main__":
    unittest.main()
