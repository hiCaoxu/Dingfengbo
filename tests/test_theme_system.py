"""主题切换与系统集成（不触碰注册表）。"""

from __future__ import annotations

import unittest

from app import theme as th
from app.services import system_integration


class ThemeTests(unittest.TestCase):
    def tearDown(self) -> None:
        th.set_mode("light")

    def test_set_mode_changes_palette(self) -> None:
        light_bg = th.BG
        th.set_mode("dark")
        self.assertTrue(th.is_dark())
        self.assertNotEqual(th.BG, light_bg)
        self.assertEqual(th.current_mode(), "dark")
        th.set_mode("light")
        self.assertEqual(th.BG, light_bg)

    def test_unknown_mode_falls_back_to_light(self) -> None:
        self.assertEqual(th.set_mode("neon"), "light")
        self.assertFalse(th.is_dark())

    def test_toggle_round_trip(self) -> None:
        th.set_mode("light")
        self.assertEqual(th.toggle_mode(), "dark")
        self.assertEqual(th.toggle_mode(), "light")

    def test_palette_keys_are_complete(self) -> None:
        for key in ("BG", "CARD", "TEXT", "TEXT_MUTED", "DIVIDER", "PRIMARY", "ACCENT",
                    "PRIMARY_LIGHT", "ACCENT_LIGHT", "HEATMAP_LEVELS"):
            self.assertIn(key, th.palette())

    def test_card_border_uses_current_divider(self) -> None:
        th.set_mode("dark")
        border = th.card_border()
        self.assertEqual(border.top.color, th.DIVIDER)

    def test_style_factories_follow_theme(self) -> None:
        th.set_mode("dark")
        self.assertEqual(th.title_style().color, th.TEXT)
        self.assertEqual(th.body_style().color, th.TEXT_MUTED)


class SystemIntegrationTests(unittest.TestCase):
    def test_launch_command_points_to_entry(self) -> None:
        command = system_integration._launch_command()
        self.assertIn("main.py", command.lower())

    def test_flags_are_boolean(self) -> None:
        self.assertIsInstance(system_integration.autostart_supported(), bool)
        self.assertIsInstance(system_integration.is_autostart_enabled(), bool)
        self.assertIsInstance(system_integration.tray_available(), bool)

    def test_set_autostart_disabled_is_safe(self) -> None:
        # 关闭只在 Windows 上访问注册表；非 Windows 返回失败文案而不是抛异常。
        ok, message = system_integration.set_autostart(False)
        self.assertIsInstance(ok, bool)
        self.assertTrue(message)


if __name__ == "__main__":
    unittest.main()
