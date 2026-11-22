"""P2 性能修补：任务状态过滤下推、热力图只取完成时间列、提醒清理时区。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.db.models import Note, Todo, TodoReminder
from app.db.repo import create
from app.db.session import session_scope
from app.services.reminders import PRUNE_DAYS, _prune
from app.ui import todo as todo_ui

from tests.helpers import reset_db


class TodoQueryFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        todo_ui._state.update(status="未完成", tag="全部", date="", quadrant="")
        with session_scope() as session:
            create(session, Todo, title="未完成A", due_date=date(2026, 9, 1))
            create(session, Todo, title="未完成B", due_date=date(2026, 9, 2))
            done = create(session, Todo, title="已完成C", due_date=date(2026, 9, 3))
            done.completed = True
        with session_scope() as session:
            template = create(session, Todo, title="模板D", due_date=date(2026, 9, 4))
            template.is_template = True

    def tearDown(self) -> None:
        todo_ui._state.update(status="未完成", tag="全部", date="", quadrant="")

    def test_open_filter_excludes_completed_and_templates(self) -> None:
        titles = {item.title for item in todo_ui._query()["todos"]}
        self.assertEqual(titles, {"未完成A", "未完成B"})

    def test_done_filter_only_completed(self) -> None:
        todo_ui._state.update(status="已完成")
        titles = {item.title for item in todo_ui._query()["todos"]}
        self.assertEqual(titles, {"已完成C"})

    def test_all_filter_includes_everything(self) -> None:
        todo_ui._state.update(status="全部")
        titles = {item.title for item in todo_ui._query()["todos"]}
        self.assertEqual(titles, {"未完成A", "未完成B", "已完成C", "模板D"})


class ActivityCountsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_completed_todo_counts_on_local_day(self) -> None:
        from app.ui.home import _activity_counts

        with session_scope() as session:
            item = create(session, Todo, title="完成项", due_date=date(2026, 9, 5))
            item.completed = True
            item.completed_at = datetime(2026, 9, 5, 2, 0, 0)  # UTC → 本地仍为 9/5
        counts = _activity_counts(date(2026, 9, 1))
        self.assertGreaterEqual(counts.get(date(2026, 9, 5), 0), 1)

    def test_window_start_filters_old_completions(self) -> None:
        from app.ui.home import _activity_counts

        with session_scope() as session:
            item = create(session, Todo, title="很久以前", due_date=date(2020, 1, 1))
            item.completed = True
            item.completed_at = datetime(2020, 1, 1, 3, 0, 0)
        counts = _activity_counts(date(2026, 9, 1))
        self.assertNotIn(date(2020, 1, 1), counts)


class ReminderPruneTimezoneTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_utc_created_at_is_pruned_with_utc_cutoff(self) -> None:
        """created_at 是 naive UTC：用本地时间当截止点会偏一个时区（UTC+8 下 8 小时）。"""
        from app.db.models import utcnow

        old = utcnow() - timedelta(days=PRUNE_DAYS + 1)
        fresh = utcnow() - timedelta(days=PRUNE_DAYS - 1)
        with session_scope() as session:
            session.add(TodoReminder(todo_id="t-old", fire_at=datetime(2026, 1, 1, 9, 0),
                                     created_at=old))
            session.add(TodoReminder(todo_id="t-new", fire_at=datetime(2026, 1, 2, 9, 0),
                                     created_at=fresh))
            session.flush()
        with session_scope() as session:
            _prune(session)
        with session_scope() as session:
            remaining = {
                row.todo_id for row in session.scalars(select(TodoReminder))
            }
        self.assertEqual(remaining, {"t-new"})


if __name__ == "__main__":
    unittest.main()

class TimelineTitleTests(unittest.TestCase):
    """关联对端标题：软删要显示「已删除」，批量取标题避免 N+1。"""

    def setUp(self) -> None:
        reset_db()

    def test_soft_deleted_peer_shows_deleted(self) -> None:
        from app.db.repo import soft_delete
        from app.services.timeline import record_title

        with session_scope() as session:
            note = create(session, Note, title="旧笔记", content="", tags="[]")
            note_id = note.id
        with session_scope() as session:
            soft_delete(session, session.get(Note, note_id))
        with session_scope() as session:
            self.assertEqual(record_title(session, "notes", note_id), "（已删除）")

    def test_links_for_uses_peer_titles(self) -> None:
        from app.services.timeline import create_link, links_for

        with session_scope() as session:
            note = create(session, Note, title="笔记A", content="", tags="[]")
            note_id = note.id
            todo = create(session, Todo, title="任务B", due_date=date(2026, 9, 9))
            todo_id = todo.id
        create_link("notes", note_id, "todos", todo_id, "相关")
        links = links_for("notes", note_id)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["title"], "任务B")

    def test_links_for_deleted_peer(self) -> None:
        from app.db.repo import soft_delete
        from app.services.timeline import create_link, links_for

        with session_scope() as session:
            note = create(session, Note, title="笔记C", content="", tags="[]")
            note_id = note.id
            todo = create(session, Todo, title="任务D", due_date=date(2026, 9, 10))
            todo_id = todo.id
        create_link("notes", note_id, "todos", todo_id, "相关")
        with session_scope() as session:
            soft_delete(session, session.get(Todo, todo_id))
        links = links_for("notes", note_id)
        self.assertEqual(links[0]["title"], "（已删除）")


class TagCountsWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_old_notes_are_excluded_from_window(self) -> None:
        from app.services.timeline import tag_counts

        with session_scope() as session:
            fresh = create(session, Note, title="新", content="", tags='["新标签"]')
            old = create(session, Note, title="旧", content="", tags='["旧标签"]')
            old.created_at = datetime(2020, 1, 1)
            fresh.created_at = datetime.now()
        tags = {name for name, _count, _sources in tag_counts(days=30)}
        self.assertIn("新标签", tags)
        self.assertNotIn("旧标签", tags)
