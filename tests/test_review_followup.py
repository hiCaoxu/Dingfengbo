"""评审跟进：令牌白名单、请求体上限、IP 限流、瞬时失败不推进游标、冲突可恢复。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db.models import Note, PurgeMark, utcnow
from app.db.repo import apply_remote, list_conflicts, restore_conflict
from app.db.session import session_scope
from app.services.sync import sync_client
from tests.helpers import reset_db
from tests.test_server_sync import _boot_server, note_change


def _note(note_id: str, title: str, stamp: datetime, owner: str = "local") -> Note:
    row = Note(title=title, content="", tags="[]")
    row.id = note_id
    row.owner_id = owner
    row.updated_at = stamp
    row.created_at = stamp
    row.is_deleted = False
    row.sync_status = "clean"
    return row


class TokenAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        import logging

        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        from sqlalchemy import create_engine
        from app.db.migrations import initialize_schema

        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._original = self.server.SessionLocal
        self.server.SessionLocal = self.Session

    def tearDown(self) -> None:
        self.server.SessionLocal = self._original
        self.engine.dispose()

    def test_unknown_token_is_rejected_and_creates_nothing(self) -> None:
        change = note_change("不该落库", datetime(2026, 3, 1))
        with mock.patch.dict(os.environ, {"DINGFENGBO_SYNC_TOKEN": "known-token"}):
            with self.assertRaises(HTTPException) as ctx:
                self.server.push(
                    self.server.PushRequest(changes=[change]), "Bearer someone-else"
                )
            self.assertEqual(ctx.exception.status_code, 401)
            accepted = self.server.push(
                self.server.PushRequest(changes=[change]), "Bearer known-token"
            )["accepted"]
        self.assertEqual(len(accepted), 1)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(Note.title)), "不该落库")

    def test_sync_body_limit_is_8mb(self) -> None:
        self.assertEqual(self.server._body_limit("/api/sync/push"), 8 * 1024 * 1024)
        self.assertEqual(self.server._body_limit("/api/sync/pull"), 8 * 1024 * 1024)
        self.assertGreater(self.server._body_limit("/api/upload"), 8 * 1024 * 1024)
        self.assertIsNone(self.server._body_limit("/api/health"))

    def test_ip_limit_trips(self) -> None:
        self.server._ip_rate_log.clear()
        ip = "203.0.113.10"
        for _ in range(self.server._IP_RATE_LIMIT):
            self.server.enforce_ip(ip)
        with self.assertRaises(HTTPException) as ctx:
            self.server.enforce_ip(ip)
        self.assertEqual(ctx.exception.status_code, 429)
        self.server.enforce_ip("")  # 直接调函数、没有来源地址时不计数

    def test_purge_watermark_is_per_owner(self) -> None:
        old = utcnow() - timedelta(days=200)
        with self.Session() as session:
            session.add(_note("keep-b", "另一租户", old, owner="owner-b"))
            session.add(_note("drop-a", "本租户墓碑", old, owner="owner-a"))
            for row in session.scalars(select(Note)):
                row.is_deleted = True
                row.sync_status = "clean"
            session.commit()
        self.server._last_purge = 0.0
        self.server._maybe_purge_tombstones("owner-a")
        with self.Session() as session:
            self.assertIsNone(session.get(Note, "drop-a"))
            self.assertIsNotNone(session.get(Note, "keep-b"))
            self.assertIsNotNone(session.get(PurgeMark, "owner-a"))
            self.assertIsNone(session.get(PurgeMark, "*"))


class PullCursorTests(unittest.TestCase):
    def test_transient_failure_does_not_advance_cursor(self) -> None:
        saved = {"n": 0}

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "changes": [{"table": "notes", "data": {"id": "1"}}],
                    "cursors": {"notes": {"sequence": 9}},
                    "has_more": False,
                    "server_time": "2026-01-01T00:00:00",
                }

        class _Client:
            def post(self, *args, **kwargs):
                return _Response()

        def locked(*args, **kwargs):
            raise OperationalError("select 1", {}, Exception("database is locked"))

        settings = {
            "server_url": "http://127.0.0.1:8000",
            "sync_token": "t",
            "sync_all_tables": True,
            "sync_cursors": {},
        }
        with mock.patch.object(sync_client, "_apply_page", side_effect=locked), \
             mock.patch.object(
                 sync_client, "_save_cursors",
                 side_effect=lambda *a, **k: saved.__setitem__("n", saved["n"] + 1),
             ):
            with self.assertRaises(OperationalError):
                sync_client._pull(_Client(), settings)
        self.assertEqual(saved["n"], 0)


class ConflictRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_losing_title_can_be_restored(self) -> None:
        note_id = str(uuid4())
        with session_scope() as session:
            row = _note(note_id, "本地标题", datetime(2026, 4, 2, 12, 0, 0))
            row.sync_status = "pending"
            session.add(row)
        with session_scope() as session:
            apply_remote(
                session,
                [{
                    "table": "notes",
                    "data": {
                        "id": note_id,
                        "owner_id": "local",
                        "updated_at": "2026-04-02T13:00:00",
                        "title": "云端标题",
                        "content": "",
                        "tags": "[]",
                        "is_deleted": False,
                        "created_at": "2026-04-02T12:00:00",
                    },
                }],
                owner_id="local",
            )
            conflicts = list_conflicts(session)
            self.assertEqual(len(conflicts), 1)
            self.assertTrue(conflicts[0]["restorable"])
            restore_conflict(session, conflicts[0]["conflict_id"])
        with session_scope() as session:
            restored = session.get(Note, note_id)
            self.assertEqual(restored.title, "本地标题")
            self.assertEqual(restored.sync_status, "pending")
            self.assertEqual(list_conflicts(session), [])


if __name__ == "__main__":
    unittest.main()
