"""窗口默认尺寸：按屏幕逻辑分辨率取比例，小屏不溢出、高分屏不浪费。"""

from __future__ import annotations

import unittest

from app.main import _default_window_size


class WindowSizeTests(unittest.TestCase):
    def test_high_dpi_laptop_screen(self) -> None:
        """2560×1600 @150% → 逻辑 1707×1067（用户当前配置）。"""
        self.assertEqual(_default_window_size((1707, 1067)), (1399, 938))

    def test_1080p_screen_is_capped(self) -> None:
        self.assertEqual(_default_window_size((1920, 1080)), (1440, 940))

    def test_small_screen_keeps_minimums(self) -> None:
        width, height = _default_window_size((1280, 720))
        self.assertEqual(width, 1120)
        self.assertEqual(height, 720)

    def test_never_below_min_window(self) -> None:
        width, height = _default_window_size((800, 600))
        self.assertGreaterEqual(width, 1120)
        self.assertGreaterEqual(height, 720)


if __name__ == "__main__":
    unittest.main()
