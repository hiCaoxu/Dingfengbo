from __future__ import annotations

import logging
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import Note
from app.db.repo import apply_remote


def note_change(title: str, updated_at: datetime) -> dict:
    timestamp = updated_at.isoformat()
    return {
        "table": "notes",
        "data": {
            "id": str(uuid4()),
            "owner_id": "local",
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_deleted": False,
            "sync_status": "pending",
            "title": title,
            "content": "",
            "tags": "[]",
        },
    }


class ServerSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        with patch.dict(
            os.environ,
            {"DINGFENGBO_SERVER_DB": os.path.join(cls.temp_dir.name, "initial.db")},
        ):
            import server.main as server_module
        cls.server = server_module

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        # 关闭日志文件句柄，否则 Windows 下无法删除临时目录中的日志文件。
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.original_session = self.server.SessionLocal
        self.server.SessionLocal = self.Session

    def tearDown(self) -> None:
        self.server.SessionLocal = self.original_session
        self.engine.dispose()

    def test_local_owner_can_push_and_remote_owner_is_normalized_on_pull(self) -> None:
        change = note_change("普通令牌", datetime(2026, 1, 1))
        accepted = self.server.push(
            self.server.PushRequest(changes=[change]), "Bearer token-a"
        )["accepted"]
        self.assertEqual(len(accepted), 1)
        owner = self.server.owner_of("Bearer token-a")
        self.assertEqual(accepted[0]["data"]["owner_id"], owner)

        with self.Session() as session:
            stored = session.scalar(select(Note))
            self.assertEqual(stored.owner_id, owner)

        pulled = self.server.pull(self.server.PullRequest(), "Bearer token-a")
        local_engine = create_engine("sqlite:///:memory:")
        initialize_schema(local_engine)
        LocalSession = sessionmaker(bind=local_engine)
        with LocalSession() as session:
            apply_remote(session, pulled["changes"], owner_id="local")
            local = session.scalar(select(Note))
            self.assertEqual(local.id, change["data"]["id"])
            self.assertEqual(local.owner_id, "local")
        self.assertEqual(self.server.pull(self.server.PullRequest(), "Bearer token-b")["changes"], [])
        local_engine.dispose()

    def test_keyset_pagination_returns_every_record_with_same_timestamp(self) -> None:
        timestamp = datetime(2026, 1, 1)
        changes = [note_change(f"笔记 {index}", timestamp) for index in range(5)]
        self.server.push(self.server.PushRequest(changes=changes), "Bearer token-a")

        cursors = {}
        pulled_ids = []
        page_count = 0
        while True:
            payload = self.server.pull(
                self.server.PullRequest(cursors=cursors, limit=2), "Bearer token-a"
            )
            page_count += 1
            pulled_ids.extend(change["data"]["id"] for change in payload["changes"])
            cursors.update(payload["cursors"])
            if not payload["has_more"]:
                break

        self.assertEqual(page_count, 3)
        self.assertEqual(len(pulled_ids), 5)
        self.assertEqual(set(pulled_ids), {change["data"]["id"] for change in changes})

    def test_older_timestamp_pushed_after_cursor_is_still_pulled(self) -> None:
        recent = note_change("较新", datetime(2026, 1, 10))
        self.server.push(self.server.PushRequest(changes=[recent]), "Bearer token-a")
        first = self.server.pull(self.server.PullRequest(limit=1), "Bearer token-a")
        cursors = first["cursors"]

        older = note_change("较旧", datetime(2026, 1, 1))
        self.server.push(self.server.PushRequest(changes=[older]), "Bearer token-a")
        second = self.server.pull(
            self.server.PullRequest(cursors=cursors, limit=10), "Bearer token-a"
        )

        pulled_ids = {change["data"]["id"] for change in second["changes"]}
        self.assertIn(older["data"]["id"], pulled_ids)

    def test_idempotent_push_does_not_advance_sequence(self) -> None:
        change = note_change("重复推送", datetime(2026, 1, 1))
        first = self.server.push(self.server.PushRequest(changes=[change]), "Bearer token-a")
        second = self.server.push(self.server.PushRequest(changes=[change]), "Bearer token-a")

        self.assertEqual(len(first["accepted"]), 1)
        self.assertEqual(len(second["accepted"]), 1)
        self.assertEqual(
            first["accepted"][0]["data"]["sync_seq"],
            second["accepted"][0]["data"]["sync_seq"],
        )

    def test_malformed_change_is_rejected_without_crashing_batch(self) -> None:
        good = note_change("正常", datetime(2026, 1, 1))
        bad_id = str(uuid4())
        bad = {"table": "notes", "data": {"id": bad_id, "title": "缺 updated_at"}}
        result = self.server.push(
            self.server.PushRequest(changes=[good, bad]), "Bearer token-a"
        )
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(len(result["rejected"]), 1)
        # 拒收回执必须带 table/id，客户端才能精准出队而不是无限重推。
        self.assertEqual(result["rejected"][0]["table"], "notes")
        self.assertEqual(result["rejected"][0]["id"], bad_id)
        self.assertIn("updated_at", result["rejected"][0]["reason"])

    def test_sequence_is_unique_across_records(self) -> None:
        a = note_change("A", datetime(2026, 1, 1))
        b = note_change("B", datetime(2026, 1, 2))
        result = self.server.push(self.server.PushRequest(changes=[a, b]), "Bearer token-a")
        seqs = [change["data"]["sync_seq"] for change in result["accepted"]]
        self.assertEqual(len(set(seqs)), 2)
        self.assertEqual(min(seqs), 1)

    def test_push_rate_limit_triggers_after_threshold(self) -> None:
        self.server._push_rate_log.clear()
        try:
            for _ in range(self.server._PUSH_RATE_LIMIT):
                self.server._enforce_rate_limit("rate-test")
            with self.assertRaises(HTTPException) as ctx:
                self.server._enforce_rate_limit("rate-test")
            self.assertEqual(ctx.exception.status_code, 429)
        finally:
            self.server._push_rate_log.clear()


if __name__ == "__main__":
    unittest.main()
