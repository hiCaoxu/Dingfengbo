"""首页「今日聚焦」：按比例分高、整页不滚动、卡片内部滚动。"""

from __future__ import annotations

import unittest
from datetime import date

import flet as ft

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


if __name__ == "__main__":
    unittest.main()
