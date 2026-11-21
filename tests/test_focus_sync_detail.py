"""今日聚焦数据与同步明细（失败记录重试/忽略、选择性同步）。"""

from __future__ import annotations

import unittest
from datetime import date

from app.db.models import Todo
from app.db.repo import ignore_issue, list_error_records, mark_rejected, retry_issue, sync_queue_stats
from app.db.session import session_scope
from app.services.focus import brief_text, today_focus
from app.services.sync import SyncClient

from tests.helpers import reset_db, seed


class FocusTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def test_focus_contains_expected_keys(self) -> None:
        data = today_focus(date.today())
        for key in (
            "todos", "pending_count", "completed_today", "completion_rate", "diary",
            "diary_written", "writing_streak", "exercise_minutes", "sleep_hours",
            "spent_today", "budget", "habits", "habits_pending", "note",
        ):
            self.assertIn(key, data)

    def test_focus_values(self) -> None:
        data = today_focus(date.today())
        self.assertTrue(data["diary_written"])
        self.assertGreaterEqual(data["writing_streak"], 1)
        self.assertEqual(data["spent_today"], 32.5)
        self.assertEqual(data["budget"]["total_budget"], 3000)
        self.assertEqual(data["completed_today"], 1)
        self.assertIn("阅读", [item["name"] for item in data["habits"]])

    def test_habits_pending_excludes_checked(self) -> None:
        data = today_focus(date.today())
        self.assertNotIn("阅读", data["habits_pending"])

    def test_brief_text_mentions_today(self) -> None:
        text = brief_text(today_focus(date.today()))
        self.assertIn("今日", text)
        self.assertIn("待办", text)


class SyncDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def _fail(self, todo_id: str, reason: str = "载荷不合法") -> None:
        with session_scope() as session:
            mark_rejected(session, [{"table": "todos", "id": todo_id, "reason": reason}])

    def test_mark_rejected_shows_in_stats_and_list(self) -> None:
        self._fail(self.ids["todo_id"])
        with session_scope() as session:
            stats = sync_queue_stats(session)
            rows = list_error_records(session)
        self.assertGreaterEqual(stats["error"], 1)
        self.assertTrue(any(row["id"] == self.ids["todo_id"] for row in rows))
        row = next(row for row in rows if row["id"] == self.ids["todo_id"])
        self.assertEqual(row["reason"], "载荷不合法")
        self.assertEqual(row["title"], "写周报")

    def test_retry_single_moves_back_to_pending(self) -> None:
        self._fail(self.ids["todo_id"])
        with session_scope() as session:
            self.assertTrue(retry_issue(session, "todos", self.ids["todo_id"]))
            todo = session.get(Todo, self.ids["todo_id"])
            self.assertEqual(todo.sync_status, "pending")
        with session_scope() as session:
            self.assertEqual(list_error_records(session), [])

    def test_ignore_single_marks_clean(self) -> None:
        self._fail(self.ids["todo_id"])
        with session_scope() as session:
            self.assertTrue(ignore_issue(session, "todos", self.ids["todo_id"]))
            todo = session.get(Todo, self.ids["todo_id"])
            self.assertEqual(todo.sync_status, "clean")

    def test_retry_unknown_record_returns_false(self) -> None:
        with session_scope() as session:
            self.assertFalse(retry_issue(session, "todos", "missing"))
            self.assertFalse(ignore_issue(session, "nope", "missing"))

    def test_remote_older_version_records_conflict(self) -> None:
        from datetime import timedelta

        from app.db.repo import apply_remote, clear_conflicts, list_conflicts

        with session_scope() as session:
            todo = session.get(Todo, self.ids["todo_id"])
            local_time = todo.updated_at
        with session_scope() as session:
            apply_remote(
                session,
                [
                    {
                        "table": "todos",
                        "data": {
                            "id": self.ids["todo_id"],
                            "updated_at": (local_time - timedelta(minutes=5)).isoformat(),
                            "title": "旧标题",
                        },
                    }
                ],
                owner_id="local",
            )
            conflicts = list_conflicts(session)
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0]["id"], self.ids["todo_id"])
            # 本地获胜：标题没被旧版本覆盖
            self.assertEqual(session.get(Todo, self.ids["todo_id"]).title, "写周报")
        with session_scope() as session:
            self.assertEqual(clear_conflicts(session), 1)

    def test_selective_tables_setting(self) -> None:
        self.assertEqual(SyncClient.selected_tables({"sync_tables": []}), set())
        self.assertEqual(
            SyncClient.selected_tables({"sync_tables": ["todos", "notes"]}),
            {"todos", "notes"},
        )
        self.assertEqual(SyncClient.selected_tables({"sync_tables": "bad"}), set())


if __name__ == "__main__":
    unittest.main()
