"""帮助页：模块覆盖是否齐全、分区是否渲染、跳转按钮是否可用。"""

from __future__ import annotations

import unittest

import flet as ft

from app.main import MODULES
from app.ui import help


class FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


def walk(control):
    yield control
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None:
        yield from walk(content)


class HelpContentTests(unittest.TestCase):
    def test_every_module_has_help(self) -> None:
        self.assertEqual(set(help.MODULE_HELP), {key for key, _label in MODULES})
        for key, points in help.MODULE_HELP.items():
            self.assertGreaterEqual(len(points), 2, msg=key)
            for point in points:
                self.assertTrue(point.strip(), msg=key)
                # 单条太长会在卡片里挤成一堵墙，帮助要能一眼扫完
                self.assertLessEqual(len(point), 70, msg=point)

    def test_quick_start_and_faq_are_filled(self) -> None:
        self.assertGreaterEqual(len(help.QUICK_START), 3)
        self.assertGreaterEqual(len(help.DATA_TIPS), 3)
        self.assertGreaterEqual(len(help.FAQ), 4)
        for question, answer in help.FAQ:
            self.assertTrue(question.strip())
            self.assertTrue(answer.strip())

    def test_shortcuts_cover_shipped_keys(self) -> None:
        keys = [key for key, _desc in help.SHORTCUTS]
        self.assertIn("Ctrl+K", keys)
        self.assertIn("Ctrl+T", keys)
        self.assertIn("F1", keys)
        self.assertTrue(any(key.startswith("Ctrl+1") for key in keys))


class HelpPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.jumps: list[str] = []
        self.view = help.build(FakePage(), self.jumps.append)

    def test_page_renders_all_sections(self) -> None:
        texts = [c.value for c in walk(self.view) if isinstance(c, ft.Text)]
        self.assertIn("帮助与上手", texts)
        for _key, label in MODULES:
            self.assertIn(label, texts)
        for title in ("快速上手", "各模块能做什么", "快捷键与鼠标", "数据、备份与同步", "常见问题"):
            self.assertIn(title, texts)
        self.assertIn(help.FAQ[0][0], texts)

    def test_each_module_card_can_jump(self) -> None:
        buttons = [c for c in walk(self.view) if isinstance(c, ft.TextButton)]
        self.assertEqual(len(buttons), len(MODULES))
        for button in buttons:
            button.on_click(None)
        self.assertEqual(self.jumps, [key for key, _label in MODULES])

    def test_page_scrolls_when_content_overflows(self) -> None:
        self.assertEqual(self.view.scroll, ft.ScrollMode.AUTO)


if __name__ == "__main__":
    unittest.main()
