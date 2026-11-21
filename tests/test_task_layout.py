"""任务页布局回归：三栏结构、四象限填充剩余高度并列出任务、列表用 ListView 滚动。

曾经的问题：
- ``card(expand=...)`` 不把 expand 传给内层 Column，列表拿不到高度；
- 筛选行用 ``ft.Row(wrap=True)`` 却塞了 ``ft.Container(expand=True)`` 间隔器，
  Wrap 不接受 flex 子项，整列被 Flet 渲染成一整块灰色空白；
- 四象限不 expand，下方留出大片空白；
- 四象限只显示数量和首条标题，看不到任务内容。
"""

from __future__ import annotations

import asyncio
import unittest

import flet as ft

from app.services.todo_views import WEEK_LABELS
from app.ui import todo
from app.ui.widgets import card

from tests.helpers import reset_db, seed


class FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.dialogs: list = []
        self.services: list = []
        self.snack_bar = None
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        dialog.open = True
        self.dialogs.append(dialog)

    def run_thread(self, handler) -> None:
        handler()

    def run_task(self, handler, *args) -> None:
        asyncio.run(handler(*args) if callable(handler) else handler)


def walk(control):
    yield control
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from walk(content)


class CardExpandTests(unittest.TestCase):
    def test_expand_propagates_to_inner_column(self) -> None:
        expanded = card(ft.Text("x"), expand=1)
        self.assertEqual(expanded.expand, 1)
        self.assertTrue(expanded.content.expand)

    def test_non_expanded_card_keeps_tight_column(self) -> None:
        plain = card(ft.Text("x"))
        self.assertIsNone(plain.expand)
        self.assertFalse(bool(plain.content.expand))
        self.assertTrue(plain.content.tight)


class TaskPageLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()
        self.page = FakePage()

    def build(self) -> ft.Control:
        holder = todo.build(self.page, lambda: None)
        self.assertIsNotNone(holder.content)
        return holder.content

    def test_three_columns(self) -> None:
        row = self.build()
        self.assertIsInstance(row, ft.Row)
        self.assertEqual(len(row.controls), 3)
        left, middle, right = row.controls
        self.assertEqual(left.width, todo.LEFT_WIDTH)
        self.assertEqual(right.width, todo.RIGHT_WIDTH)
        self.assertTrue(middle.expand)

    def test_calendar_and_quadrant_on_the_left(self) -> None:
        left = self.build().controls[0]
        self.assertEqual(len(left.controls), 2)
        calendar, quadrants = left.controls
        # 四象限吃掉月历下方的剩余高度
        self.assertEqual(quadrants.expand, 1)
        texts = [c.value for c in walk(calendar) if isinstance(c, ft.Text)]
        for label in WEEK_LABELS:
            self.assertIn(label, texts)

    def test_task_list_uses_listview(self) -> None:
        middle = self.build().controls[1]
        listviews = [c for c in walk(middle) if isinstance(c, ft.ListView)]
        self.assertTrue(listviews)
        self.assertTrue(listviews[0].expand)
        titles = [c.value for c in walk(middle) if isinstance(c, ft.Text)]
        self.assertIn("写周报", titles)
        self.assertIn("体检预约", titles)

    def test_habits_panel_on_the_right(self) -> None:
        right = self.build().controls[2]
        texts = [c.value for c in walk(right) if isinstance(c, ft.Text)]
        self.assertIn("习惯", texts)
        self.assertIn("阅读", texts)
        self.assertIn("今天全部打卡完成", texts)  # 样本里今天的习惯已打卡
        # 右侧只有一个卡片，且撑满整列高度
        self.assertEqual(right.controls[0].expand, 1)

    def test_no_expand_child_inside_wrap_row(self) -> None:
        """Row(wrap=True) 里不能出现 expand 子项，否则整列会变成灰色空白。"""
        for control in walk(self.build()):
            if isinstance(control, ft.Row) and getattr(control, "wrap", False):
                for child in control.controls:
                    self.assertFalse(
                        bool(getattr(child, "expand", False)),
                        f"Wrap 行里出现了 expand 子项：{type(child).__name__}",
                    )

    def test_quadrant_counts_render(self) -> None:
        left = self.build().controls[0]
        quadrants = left.controls[1]
        texts = [c.value for c in walk(quadrants) if isinstance(c, ft.Text)]
        for label in ("重要且紧急", "重要不紧急", "紧急不重要", "不重要不紧急"):
            self.assertIn(label, texts)

    def test_quadrant_cells_list_task_titles(self) -> None:
        """格子里要能看到任务内容，而不是只有数量或首条标题。"""
        quadrants = self.build().controls[0].controls[1]
        texts = [c.value for c in walk(quadrants) if isinstance(c, ft.Text)]
        joined = " | ".join(texts)
        # 样本里两条未完成任务都属于「重要且紧急」，日期前缀不影响匹配
        self.assertIn("写周报", joined)
        self.assertIn("体检预约", joined)
        # 已完成的任务不进格子
        self.assertNotIn("已完成的整理", joined)
        # 其余三个象限为空
        self.assertEqual(texts.count("暂无任务"), 3)
        # 格子内容用自然高度，不能出现 ListView（无界高度会变灰块）
        self.assertFalse(
            [c for c in walk(quadrants) if isinstance(c, ft.ListView)]
        )


if __name__ == "__main__":
    unittest.main()
