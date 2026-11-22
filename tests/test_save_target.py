"""保存骨架：编辑目标失效时必须显式失败，不能假保存或误新建。"""

from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import func, select

from app.db.models import Note, Sleep
from app.db.repo import create, soft_delete
from app.db.session import session_scope
from app.ui.forms import SaveTargetMissing, save_record

from tests.helpers import reset_db


class SaveTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def _note(self, title: str = "原标题") -> str:
        with session_scope() as session:
            return create(session, Note, title=title, content="", tags="[]").id

    def test_missing_target_raises_instead_of_creating(self) -> None:
        with session_scope() as session:
            before = session.scalar(select(func.count()).select_from(Note))
        with self.assertRaises(SaveTargetMissing):
            save_record(Note, "no-such-id", {"title": "不该被创建"})
        with session_scope() as session:
            after = session.scalar(select(func.count()).select_from(Note))
            created = session.scalar(select(Note).where(Note.title == "不该被创建"))
        self.assertEqual(before, after, "目标不存在时不能新建一条记录")
        self.assertIsNone(created)

    def test_soft_deleted_target_raises_and_keeps_content(self) -> None:
        note_id = self._note("删除前")
        with session_scope() as session:
            soft_delete(session, session.get(Note, note_id))
        with self.assertRaises(SaveTargetMissing):
            save_record(Note, note_id, {"title": "改墓碑"})
        with session_scope() as session:
            stored = session.get(Note, note_id)
        self.assertEqual(stored.title, "删除前", "墓碑内容不能被改写")
        self.assertTrue(stored.is_deleted)

    def test_editing_existing_target_updates(self) -> None:
        note_id = self._note()
        returned = save_record(Note, note_id, {"title": "改好了"})
        self.assertEqual(returned, note_id)
        with session_scope() as session:
            stored = session.get(Note, note_id)
        self.assertEqual(stored.title, "改好了")
        self.assertEqual(stored.sync_status, "pending")

    def test_create_without_id_creates_new_row(self) -> None:
        new_id = save_record(Note, None, {"title": "新建", "content": "", "tags": "[]"})
        with session_scope() as session:
            self.assertIsNotNone(session.get(Note, new_id))

    def test_resolve_only_applies_to_new_records(self) -> None:
        """新建时按业务键复用同一天的睡眠行；编辑时绝不 resolve 到别的行。"""
        day = date(2026, 7, 1)
        with session_scope() as session:
            existing_today = create(session, Sleep, date=day, hours=7.0, quality=3)
            existing_today_id = existing_today.id
            other_day = create(session, Sleep, date=date(2026, 7, 2), hours=6.0, quality=3)
            other_day_id = other_day.id

        def resolve(session):
            return session.scalar(
                select(Sleep).where(Sleep.is_deleted.is_(False), Sleep.date == day)
            )

        # 新建同一天：复用已有行（不新增）
        save_record(Sleep, None, {"hours": 8.0}, resolve=resolve)
        with session_scope() as session:
            rows = list(session.scalars(select(Sleep)))
            same_day = session.get(Sleep, existing_today_id)
        self.assertEqual(len(rows), 2, "同一天只应保留一条睡眠记录")
        self.assertEqual(same_day.hours, 8.0)

        # 编辑另一天：写的是它自己，不能因为 resolve 命中同一天而改错行
        save_record(Sleep, other_day_id, {"hours": 5.5}, resolve=resolve)
        with session_scope() as session:
            edited = session.get(Sleep, other_day_id)
            untouched = session.get(Sleep, existing_today_id)
        self.assertEqual(edited.hours, 5.5)
        self.assertEqual(untouched.hours, 8.0)


if __name__ == "__main__":
    unittest.main()
