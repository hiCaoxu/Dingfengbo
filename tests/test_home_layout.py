"""首页「今日聚焦」：按比例分高、整页不滚动、卡片内部滚动。"""

from __future__ import annotations

import inspect
import unittest
from datetime import date
from types import SimpleNamespace

import flet as ft

from app import main as app_main
from app import theme as th
from app.main import SLOGAN, SLOGAN_DAILY, slogan_metrics
from app.services.focus import today_focus
from app.ui import focus

from tests.helpers import reset_db


class _FakePage:
    def update(self) -> None:
        return None

    def run_thread(self, handler) -> None:  # noqa: ARG002
        return None

    def run_task(self, handler) -> None:  # noqa: ARG002
        return None


class FocusLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.control = focus.render(
            _FakePage(), lambda: None, today_focus(date.today()),
            lambda day: None, lambda note: None, lambda key: None,
        )

    def test_page_fills_height_without_global_scroll(self) -> None:
        """整页不再上下滚动：高度由三行按 4:3:3 瓜分。"""
        self.assertIsInstance(self.control, ft.Column)
        self.assertIsNone(self.control.scroll)
        self.assertTrue(self.control.expand)
        rows = self.control.controls
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.expand for row in rows], [4, 3, 3])
        self.assertTrue(all(row.height is None for row in rows))

    def test_first_row_is_today_card_then_todos_then_diary(self) -> None:
        """日期卡复制自数据概览，放在今日必做和日记的左边。"""
        row = self.control.controls[0]
        self.assertEqual(len(row.controls), 3)
        self.assertEqual([card.expand for card in row.controls], [2, 3, 2])

        def texts(control) -> list[str]:
            return [
                node.value for node in _walk(control)
                if isinstance(node, ft.Text) and isinstance(node.value, str)
            ]

        today_texts = texts(row.controls[0])
        self.assertTrue(any(value.endswith("月") for value in today_texts))
        self.assertTrue(any("距今年结束还有" in value for value in today_texts))
        self.assertIn(86, [node.size for node in _walk(row.controls[0]) if isinstance(node, ft.Text)])
        self.assertIn("今日必做", texts(row.controls[1]))
        self.assertIn("日记", texts(row.controls[2]))

    def test_cards_stretch_inside_rows(self) -> None:
        for row in self.control.controls:
            for card in row.controls:
                self.assertTrue(card.expand, "同排卡片应按比例拉伸")

    def test_scrollable_bodies_are_expanded(self) -> None:
        """卡片正文里带滚动的 Column 必须 expand，否则滚不起来（会被裁切）。"""
        def bodies(control):
            if isinstance(control, ft.Column):
                yield control
                for child in control.controls:
                    yield from bodies(child)
            elif isinstance(control, (ft.Row, ft.Container)):
                children = control.controls if isinstance(control, ft.Row) else (
                    [control.content] if control.content is not None else []
                )
                for child in children:
                    yield from bodies(child)

        scrollers = [c for c in bodies(self.control) if c.scroll is not None]
        self.assertTrue(scrollers, "应至少有一个可滚动正文")
        self.assertTrue(all(c.expand for c in scrollers), "可滚动正文必须 expand 才能拿到高度")


def _walk(control):
    stack = [control]
    while stack:
        node = stack.pop()
        yield node
        for name in ("controls", "content"):
            value = getattr(node, name, None)
            if isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, ft.Control):
                stack.append(value)


def _button_labeled(root, label: str):
    for node in _walk(root):
        if not isinstance(node, ft.Button):
            continue
        texts = [item.value for item in _walk(node) if isinstance(item, ft.Text)]
        if label in texts:
            return node
    return None


class FocusActionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.data = today_focus(date.today())
        self.opened: list = []
        self.control = focus.render(
            _FakePage(), lambda: None, self.data,
            lambda day: None, lambda note: self.opened.append(note), lambda key: None,
        )

    def test_slogan_order_and_size(self) -> None:
        self.assertEqual(SLOGAN, "要事第一  刻意练习  持续精进")
        self.assertEqual(SLOGAN_DAILY, ("早起", "冥想", "阅读", "写作", "运动"))
        top_size, sub_size, top_gap, sub_gap = slogan_metrics()
        self.assertLess(sub_size, top_size)
        self.assertGreaterEqual(sub_size, th.FONT_MIN)
        top_width = sum(len(part) * top_size for part in SLOGAN.split("  ")) + top_gap * 2
        sub_width = sum(len(part) * sub_size for part in SLOGAN_DAILY) + sub_gap * (len(SLOGAN_DAILY) - 1)
        self.assertAlmostEqual(top_width, sub_width)
        source = inspect.getsource(app_main.slogan_metrics)
        self.assertIn("th.FONT_SECTION_TITLE + 2", source)
        self.assertIn("th.FONT_SECTION_TITLE", source)

    def test_go_todo_has_no_plus_icon(self) -> None:
        button = _button_labeled(self.control, "去任务")
        self.assertIsNotNone(button)
        icons = [node for node in _walk(button) if isinstance(node, ft.Icon)]
        self.assertEqual(icons, [])

    def test_habit_manage_uses_primary_background(self) -> None:
        button = _button_labeled(self.control, "管理")
        self.assertIsNotNone(button)
        self.assertEqual(button.bgcolor, th.PRIMARY)

    def test_review_card_opens_on_click_without_a_link_button(self) -> None:
        texts = [node.value for node in _walk(self.control) if isinstance(node, ft.Text)]
        self.assertNotIn("打开这条笔记", texts)
        note = SimpleNamespace(id="n1", title="旧笔记", content="正文")
        self.data["note"] = note
        control = focus.render(
            _FakePage(), lambda: None, self.data,
            lambda day: None, lambda note: self.opened.append(note), lambda key: None,
        )
        clicked = False
        for node in _walk(control):
            if not isinstance(node, ft.Container) or node.on_click is None:
                continue
            titles = [item.value for item in _walk(node) if isinstance(item, ft.Text)]
            if "随机回顾" in titles:
                node.on_click(None)
                clicked = True
                break
        self.assertTrue(clicked)
        self.assertEqual(self.opened, [note])


if __name__ == "__main__":
    unittest.main()
