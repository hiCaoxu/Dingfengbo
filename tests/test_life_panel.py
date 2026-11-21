"""人生大事：数据操作与日记页三栏布局（月历 / 日记编辑 / 人生大事）。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import flet as ft

from app.services import life
from app.ui import diary

from tests.helpers import reset_db


class FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.dialogs: list = []
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        dialog.open = True
        self.dialogs.append(dialog)


def walk(control):
    yield control
    children = getattr(control, "controls", None)
    if children:
        for child in children:
            yield from walk(child)
    content = getattr(control, "content", None)
    if content is not None:
        yield from walk(content)


def _payload(events: list[dict] | None = None) -> dict:
    today = date.today()
    return {
        "view": "月历",
        "main": (today.year, today.month, set(), []),
        "editor": {"id": "d1", "title": f"{today} 日记", "content": "今天状态不错"},
        "life": events or [],
    }


class LifeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_add_toggle_delete(self) -> None:
        event_id = life.add_event("写完一本书")
        self.assertIsNotNone(event_id)
        self.assertEqual([row["title"] for row in life.list_events()], ["写完一本书"])

        self.assertTrue(life.toggle_event(event_id))
        row = life.list_events()[0]
        self.assertTrue(row["done"])
        self.assertIsNotNone(row["done_at"])

        self.assertFalse(life.toggle_event(event_id))
        self.assertFalse(life.list_events()[0]["done"])

        self.assertTrue(life.delete_event(event_id))
        self.assertEqual(life.list_events(), [])

    def test_empty_title_ignored(self) -> None:
        self.assertIsNone(life.add_event("   "))
        self.assertEqual(life.list_events(), [])

    def test_order_unfinished_first_then_due_date(self) -> None:
        today = date.today()
        life.add_event("无日期")
        life.add_event("年底的目标", target_date=today + timedelta(days=30))
        life.add_event("本月目标", target_date=today + timedelta(days=3))
        done_id = life.add_event("已完成")
        life.toggle_event(done_id)

        self.assertEqual(
            [row["title"] for row in life.list_events()],
            ["本月目标", "年底的目标", "无日期", "已完成"],
        )

    def test_toggle_missing_returns_none(self) -> None:
        self.assertIsNone(life.toggle_event("nope"))
        self.assertFalse(life.delete_event("nope"))


class DiaryLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.page = FakePage()

    def _row(self, events: list[dict] | None = None) -> ft.Row:
        view = diary._render(self.page, lambda: None, _payload(events))
        return view.controls[1]

    def test_three_columns_editor_is_twice_the_life_panel(self) -> None:
        row = self._row()
        self.assertEqual(len(row.controls), 3)
        main, editor, life_panel = row.controls
        self.assertEqual(main.width, 360)
        self.assertEqual(editor.expand, 2)
        self.assertEqual(life_panel.expand, 1)

    def test_editor_field_fills_the_card(self) -> None:
        _, editor, _ = self._row().controls
        fields = [c for c in walk(editor) if isinstance(c, ft.TextField)]
        self.assertEqual(len(fields), 1)
        self.assertTrue(fields[0].expand)
        self.assertEqual(fields[0].value, "今天状态不错")

    def test_life_panel_renders_events_and_add_button(self) -> None:
        events = [
            {"id": "e1", "title": "带父母去旅行", "done": False, "done_at": None},
            {"id": "e2", "title": "写完一本书", "done": True, "done_at": None},
        ]
        _, _, life_panel = self._row(events).controls
        texts = [c.value for c in walk(life_panel) if isinstance(c, ft.Text)]
        self.assertIn("人生大事", texts)
        self.assertIn("带父母去旅行", texts)
        self.assertIn("写完一本书", texts)
        checkboxes = [c for c in walk(life_panel) if isinstance(c, ft.Checkbox)]
        self.assertEqual([c.value for c in checkboxes], [False, True])

    def test_life_panel_empty_state(self) -> None:
        _, _, life_panel = self._row().controls
        texts = [c.value for c in walk(life_panel) if isinstance(c, ft.Text)]
        self.assertIn("还没有目标，先写下第一件", texts)


if __name__ == "__main__":
    unittest.main()
