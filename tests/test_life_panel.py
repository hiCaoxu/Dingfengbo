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
        "editor": {"id": "d1", "day": today, "title": f"{today} 日记",
                   "content": "今天状态不错"},
        "life": events or [],
    }


class LifeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_add_update_delete(self) -> None:
        event_id = life.add_event("写完一本书", target_date=date(2026, 5, 1))
        self.assertIsNotNone(event_id)
        self.assertEqual([row["title"] for row in life.list_events()], ["写完一本书"])

        self.assertTrue(life.update_event(event_id, "带父母去旅行", date(2027, 3, 8)))
        row = life.list_events()[0]
        self.assertEqual(row["title"], "带父母去旅行")
        self.assertEqual(row["target_date"], date(2027, 3, 8))

        # 日期可以清空
        self.assertTrue(life.update_event(event_id, "带父母去旅行", None))
        self.assertIsNone(life.list_events()[0]["target_date"])

        self.assertTrue(life.delete_event(event_id))
        self.assertEqual(life.list_events(), [])

    def test_empty_title_ignored(self) -> None:
        self.assertIsNone(life.add_event("   "))
        self.assertEqual(life.list_events(), [])

    def test_update_rejects_empty_title_or_missing_id(self) -> None:
        event_id = life.add_event("写完一本书")
        self.assertFalse(life.update_event(event_id, "   ", None))
        self.assertFalse(life.update_event("nope", "标题", None))
        self.assertFalse(life.delete_event("nope"))

    def test_title_is_capped_at_fifteen_chars(self) -> None:
        event_id = life.add_event("一" * 40)
        self.assertIsNotNone(event_id)
        self.assertEqual(life.list_events()[0]["title"], "一" * 15)

    def test_parse_target_date(self) -> None:
        self.assertIsNone(life.parse_target_date(""))
        self.assertIsNone(life.parse_target_date("   "))
        self.assertEqual(life.parse_target_date("2026-11-21"), date(2026, 11, 21))
        for bad in ("2026-1-1", "2026/11/21", "2026-13-01", "2026-02-30", "明天"):
            with self.assertRaises(ValueError, msg=bad):
                life.parse_target_date(bad)

    def test_order_is_target_date_desc(self) -> None:
        today = date.today()
        life.add_event("无日期")
        life.add_event("年底的目标", target_date=today + timedelta(days=30))
        life.add_event("本月目标", target_date=today + timedelta(days=3))
        life.add_event("没定日子")

        # 有日期的按日期新的在上；没日期的沉底。两条无日期记录可能落在同一微秒，
        # 创建时间相同时先后不定，这里只断言它们都在末尾。
        titles = [row["title"] for row in life.list_events()]
        self.assertEqual(titles[:2], ["年底的目标", "本月目标"])
        self.assertEqual(set(titles[2:]), {"没定日子", "无日期"})


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

    def test_life_panel_renders_events_with_edit_and_delete(self) -> None:
        events = [
            {"id": "e1", "title": "带父母去旅行", "target_date": date(2026, 5, 1)},
            {"id": "e2", "title": "写完一本书", "target_date": None},
        ]
        _, _, life_panel = self._row(events).controls
        texts = [c.value for c in walk(life_panel) if isinstance(c, ft.Text)]
        self.assertIn("人生大事", texts)
        self.assertIn("带父母去旅行", texts)
        self.assertIn("写完一本书", texts)
        self.assertIn("2026-05-01", texts)
        self.assertIn("未定", texts)
        # 没有勾选框，也没有"点击标题切换完成"的入口
        self.assertEqual([c for c in walk(life_panel) if isinstance(c, ft.Checkbox)], [])
        # 每行一个编辑 + 一个删除按钮
        buttons = [c for c in walk(life_panel) if isinstance(c, ft.IconButton)]
        tooltips = [b.tooltip for b in buttons]
        self.assertEqual(tooltips.count("编辑"), 2)
        self.assertEqual(tooltips.count("删除"), 2)
        self.assertIn("新增人生大事", tooltips)

    def test_life_title_is_not_truncated(self) -> None:
        long_title = "十五个字以内的人生大事标题"
        events = [{"id": "e1", "title": long_title, "target_date": None}]
        _, _, life_panel = self._row(events).controls
        titles = [
            c
            for c in walk(life_panel)
            if isinstance(c, ft.Text) and c.value == long_title
        ]
        self.assertEqual(len(titles), 1)
        # 完整换行显示：不限制行数、不省略
        self.assertIsNone(titles[0].max_lines)
        self.assertNotEqual(titles[0].overflow, ft.TextOverflow.ELLIPSIS)
        self.assertEqual([c for c in walk(life_panel) if getattr(c, "tooltip", "") == "点击标记完成 / 取消完成"], [])

    def test_editor_shows_inserted_images(self) -> None:
        payload = _payload()
        payload["editor"]["content"] = "今天\n![](http://h/files/a.jpg)"
        _, editor, _ = diary._render(self.page, lambda: None, payload).controls[1].controls
        images = [c for c in walk(editor) if isinstance(c, ft.Image)]
        self.assertEqual([image.src for image in images], ["http://h/files/a.jpg"])

    def _open_add_dialog(self, panel) -> ft.AlertDialog:
        add_button = [
            c
            for c in walk(panel)
            if isinstance(c, ft.IconButton) and c.tooltip == "新增人生大事"
        ][0]
        add_button.on_click(None)
        return self.page.dialogs[-1]

    def test_add_dialog_saves_manual_date(self) -> None:
        refreshed: list[int] = []
        panel = diary._life_panel(self.page, lambda: refreshed.append(1), [])
        dialog = self._open_add_dialog(panel)
        fields = [c for c in walk(dialog.content) if isinstance(c, ft.TextField)]
        self.assertEqual(len(fields), 2)

        fields[0].value = "带父母去旅行"
        fields[1].value = "2026-05-01"
        dialog.actions[1].on_click(None)

        rows = life.list_events()
        self.assertEqual([row["title"] for row in rows], ["带父母去旅行"])
        self.assertEqual(rows[0]["target_date"], date(2026, 5, 1))
        self.assertFalse(dialog.open)
        self.assertEqual(refreshed, [1])

    def test_add_dialog_rejects_bad_date_format(self) -> None:
        panel = diary._life_panel(self.page, lambda: None, [])
        dialog = self._open_add_dialog(panel)
        fields = [c for c in walk(dialog.content) if isinstance(c, ft.TextField)]

        fields[0].value = "带父母去旅行"
        fields[1].value = "2026-5-1"
        dialog.actions[1].on_click(None)

        self.assertEqual(life.list_events(), [])
        self.assertTrue(dialog.open)
        texts = [c.value for c in walk(dialog.content) if isinstance(c, ft.Text)]
        self.assertIn("日期格式应为 YYYY-MM-DD，例如 2026-11-21", texts)

    def test_life_row_gives_title_the_leftover_width(self) -> None:
        title = "带父母去旅行"
        events = [{"id": "e1", "title": title, "target_date": date(2026, 5, 1)}]
        _, _, life_panel = self._row(events).controls

        holders = [
            c
            for c in walk(life_panel)
            if isinstance(c, ft.Column)
            and any(isinstance(i, ft.Text) and i.value == title for i in c.controls)
        ]
        self.assertEqual(len(holders), 1)
        self.assertTrue(holders[0].expand)
        # 日期不再是固定宽度的独立列（会把标题挤成一字一行），而是排在标题下方
        self.assertEqual(
            [c for c in walk(life_panel) if isinstance(c, ft.Container) and c.width == 74], []
        )
        # 行内操作按钮改为紧凑尺寸，少占宽度
        small = [
            b
            for b in walk(life_panel)
            if isinstance(b, ft.IconButton) and b.tooltip in ("编辑", "删除")
        ]
        self.assertEqual(len(small), 2)
        self.assertTrue(all(b.icon_size == 14 for b in small))

    def test_life_panel_empty_state(self) -> None:
        _, _, life_panel = self._row().controls
        texts = [c.value for c in walk(life_panel) if isinstance(c, ft.Text)]
        self.assertIn("还没有目标，先写下第一件", texts)


if __name__ == "__main__":
    unittest.main()
