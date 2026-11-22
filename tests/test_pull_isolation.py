"""pull 逐条隔离：一条毒记录不能卡住整页同步与游标推进。"""

from __future__ import annotations

import unittest

from app.db.models import Note
from app.db.repo import list_sync_issues
from app.db.session import session_scope
from app.services.sync import sync_client

from tests.helpers import reset_db


def _note_change(note_id: str, **overrides) -> dict:
    data = {
        "id": note_id,
        "owner_id": "local",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "is_deleted": False,
        "sync_status": "clean",
        "sync_seq": 5,
        "title": "来自云端",
        "content": "",
        "tags": "[]",
    }
    data.update(overrides)
    return {"table": "notes", "data": data}


class PullIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_poison_record_does_not_block_the_page(self) -> None:
        failed = sync_client._apply_page(
            [
                _note_change("good-1"),
                # 缺 updated_at：apply_remote 会 KeyError
                {"table": "notes", "data": {"id": "bad-missing"}},
                # updated_at 非法：fromisoformat 会 ValueError
                _note_change("bad-time", updated_at="不是时间"),
                _note_change("good-2"),
            ]
        )
        self.assertEqual(failed, 2, "两条坏记录应分别隔离，各自记一次失败")

        with session_scope() as session:
            self.assertIsNotNone(session.get(Note, "good-1"))
            self.assertIsNotNone(session.get(Note, "good-2"))
            self.assertIsNone(session.get(Note, "bad-missing"))
            self.assertIsNone(session.get(Note, "bad-time"))
            issues = list_sync_issues(session)
        reasons = {issue.record_id: issue.reason for issue in issues}
        self.assertIn("拉取失败", reasons.get("bad-missing", ""))
        self.assertIn("拉取失败", reasons.get("bad-time", ""))

    def test_clean_page_reports_no_failure(self) -> None:
        failed = sync_client._apply_page([_note_change("good-3")])
        self.assertEqual(failed, 0)
        with session_scope() as session:
            self.assertIsNotNone(session.get(Note, "good-3"))

    def test_empty_page_is_noop(self) -> None:
        self.assertEqual(sync_client._apply_page([]), 0)


if __name__ == "__main__":
    unittest.main()
