"""任务勾选：完成/取消完成后的状态复位，以及循环下一期的幂等。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models import Todo
from app.db.repo import create
from app.db.session import engine, session_scope
from app.ui.todo import toggle

from tests.helpers import reset_db


def _shown_as_open(item: Todo) -> bool:
    """复刻「未完成」视图的过滤条件。"""
    return not item.completed and not item.is_template


class ToggleStateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_uncompleting_restores_template_flag(self) -> None:
        """完成循环任务会标 is_template 隐去这一期；取消完成必须复位，
        否则它既不在「未完成」里、又在「全部」里显示为未完成。"""
        with session_scope() as session:
            todo = create(session, Todo, title="每日复盘", repeat_rule="day",
                          due_date=date.today())
            todo_id = todo.id

        toggle(None, todo_id, lambda: None)  # 完成
        with session_scope() as session:
            done = session.get(Todo, todo_id)
            self.assertTrue(done.completed)
            self.assertTrue(done.is_template)

        toggle(None, todo_id, lambda: None)  # 取消完成
        with session_scope() as session:
            back = session.get(Todo, todo_id)
            self.assertFalse(back.completed)
            self.assertIsNone(back.completed_at)
            self.assertFalse(back.is_template)
            self.assertTrue(_shown_as_open(back))

    def test_recompleting_does_not_duplicate_next_occurrence(self) -> None:
        today = date.today()
        with session_scope() as session:
            todo = create(session, Todo, title="每日复盘", repeat_rule="day", due_date=today)
            todo_id = todo.id

        toggle(None, todo_id, lambda: None)  # 完成 → 生成下一期
        toggle(None, todo_id, lambda: None)  # 取消完成
        toggle(None, todo_id, lambda: None)  # 再完成

        with session_scope() as session:
            count = session.scalar(
                select(func.count())
                .select_from(Todo)
                .where(Todo.title == "每日复盘", Todo.due_date == today + timedelta(days=1))
            )
        self.assertEqual(count, 1, "同一到期日只应有一期循环任务")

    def test_plain_task_keeps_template_flag_false(self) -> None:
        with session_scope() as session:
            todo = create(session, Todo, title="一次性任务", due_date=date.today())
            todo_id = todo.id
        toggle(None, todo_id, lambda: None)
        toggle(None, todo_id, lambda: None)
        with session_scope() as session:
            item = session.get(Todo, todo_id)
            self.assertFalse(item.completed)
            self.assertFalse(item.is_template)


class TemplateFlagRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_legacy_inconsistent_rows_are_repaired(self) -> None:
        """历史库可能存在 is_template=1 且 completed=0 的行，迁移时复位并标脏重同步。"""
        from app.db.migrations import initialize_schema

        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO todos
                    (id, owner_id, created_at, updated_at, is_deleted, sync_status, sync_seq,
                     title, note, due_date, due_time, tags, lunar_reminder, repeat_rule,
                     repeat_config, priority, quadrant, remind_days, remind_days_before,
                     remind_time, pomodoro_count, completed, completed_at, is_template)
                VALUES
                    ('legacy-tpl', 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00',
                     0, 'clean', 0, '历史脏数据', '', '2026-01-01', '', '[]', '', 'day',
                     '', '', '', '[]', -1, '', 0, 0, NULL, 1)
                """
            )
            connection.exec_driver_sql("PRAGMA user_version = 2")  # 已迁移金额与时间

        initialize_schema(engine)
        with engine.connect() as connection:
            is_template, sync_status = connection.exec_driver_sql(
                "SELECT is_template, sync_status FROM todos WHERE id = 'legacy-tpl'"
            ).fetchone()
            version = int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        self.assertEqual(is_template, 0)
        self.assertEqual(sync_status, "pending")
        self.assertGreaterEqual(version, 3)

    def test_repair_is_idempotent(self) -> None:
        from app.db.migrations import initialize_schema

        initialize_schema(engine)  # 空库，标记到最新版本
        with engine.connect() as connection:
            before = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM todos WHERE is_template = 1 AND completed = 0"
            ).scalar()
            version = int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        initialize_schema(engine)
        with engine.connect() as connection:
            after = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM todos WHERE is_template = 1 AND completed = 0"
            ).scalar()
        self.assertEqual(before, after)
        self.assertGreaterEqual(version, 3)


if __name__ == "__main__":
    unittest.main()
