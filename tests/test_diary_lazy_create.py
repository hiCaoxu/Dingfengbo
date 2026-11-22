"""日记「浏览不建行、首次真实输入才建行」。"""

from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import func, select

from app.db.models import Diary
from app.db.repo import create
from app.db.session import session_scope
from app.ui import diary as diary_ui

from tests.helpers import reset_db


class DiaryLazyCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        diary_ui.autosave.reset_for_tests()

    def tearDown(self) -> None:
        diary_ui.autosave.reset_for_tests()

    def _count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(Diary)) or 0)

    def test_reading_a_day_does_not_create_a_row(self) -> None:
        data = diary_ui._editor_data(date(2026, 8, 1))
        self.assertEqual(data["id"], "", "浏览某天不能建空日记")
        self.assertEqual(data["content"], "")
        self.assertTrue(data["title"], "草稿也要有标题")
        self.assertEqual(self._count(), 0)

    def test_empty_content_does_not_create(self) -> None:
        diary_ui.save_content("", "", date(2026, 8, 2))
        diary_ui.save_content("", "   \n  ", date(2026, 8, 2))
        self.assertEqual(self._count(), 0)

    def test_first_real_input_creates_exactly_one_row(self) -> None:
        day = date(2026, 8, 3)
        new_id = diary_ui.save_content("", "今天读完了两章", day)
        self.assertTrue(new_id)
        # 第二次保存（编辑器里 id 仍是草稿的 ""）不能重复插入
        again = diary_ui.save_content("", "今天读完了两章，记一笔", day)
        self.assertEqual(again, new_id)
        self.assertEqual(self._count(), 1)
        with session_scope() as session:
            stored = session.get(Diary, new_id)
        self.assertEqual(stored.date, day)
        self.assertIn("记一笔", stored.content)
        self.assertEqual(stored.sync_status, "pending")

    def test_reading_existing_day_returns_it(self) -> None:
        day = date(2026, 8, 4)
        with session_scope() as session:
            existing = create(session, Diary, date=day, title="已有", content="正文")
            existing_id = existing.id
        data = diary_ui._editor_data(day)
        self.assertEqual(data["id"], existing_id)
        self.assertEqual(data["content"], "正文")

    def test_soft_deleted_day_is_treated_as_empty(self) -> None:
        from app.db.repo import soft_delete

        day = date(2026, 8, 5)
        with session_scope() as session:
            diary = create(session, Diary, date=day, title="旧", content="旧正文")
            soft_delete(session, diary)
        data = diary_ui._editor_data(day)
        self.assertEqual(data["id"], "", "已删除的日记应显示为空草稿")
        # 再次输入会复用墓碑，不会撞唯一约束
        new_id = diary_ui.save_content("", "重新写", day)
        with session_scope() as session:
            stored = session.get(Diary, new_id)
        self.assertFalse(stored.is_deleted)
        self.assertEqual(stored.content, "重新写")


if __name__ == "__main__":
    unittest.main()
