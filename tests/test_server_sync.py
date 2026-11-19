from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

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
        self.assertEqual(accepted[0]["data"]["owner_id"], "token-a")

        with self.Session() as session:
            stored = session.scalar(select(Note))
            self.assertEqual(stored.owner_id, "token-a")

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


if __name__ == "__main__":
    unittest.main()
