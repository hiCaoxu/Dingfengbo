"""应用标志文件存在，并且能收成窗口用的 ico。"""

from __future__ import annotations

import unittest

from PIL import Image

from app.branding import logo_bytes, logo_path, window_icon_path


class BrandingTests(unittest.TestCase):
    def test_logo_is_a_square_png(self) -> None:
        path = logo_path()
        self.assertTrue(path.is_file())
        raw = logo_bytes()
        self.assertTrue(raw.startswith(b"\x89PNG"))
        with Image.open(path) as image:
            self.assertEqual(image.size[0], image.size[1])

    def test_window_icon_is_present(self) -> None:
        path = window_icon_path()
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        with Image.open(path) as image:
            self.assertEqual(image.format, "ICO")
