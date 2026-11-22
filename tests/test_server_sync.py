from __future__ import annotations

import logging
import os
import sys
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


def diary_change(day: str, content: str, updated_at: datetime, record_id: str | None = None) -> dict:
    timestamp = updated_at.isoformat()
    return {
        "table": "diaries",
        "data": {
            "id": record_id or str(uuid4()),
            "owner_id": "local",
            "created_at": timestamp,
            "updated_at": timestamp,
            "is_deleted": False,
            "sync_status": "pending",
            "sync_seq": 0,
            "date": day,
            "title": content,
            "content": content,
        },
    }


def _boot_server(temp_dir: str):
    """在临时目录里重新加载 server.main（库 + 图片目录都指向 temp_dir）。"""
    env = {
        "DINGFENGBO_SERVER_DB": os.path.join(temp_dir, "initial.db"),
        "DINGFENGBO_SERVER_FILES": os.path.join(temp_dir, "files"),
    }
    with patch.dict(os.environ, env):
        # 别的测试文件可能已经导入过 server.main（会把库/图片目录钉在别处），
        # 先清掉缓存，确保这里的环境变量生效。
        sys.modules.pop("server.main", None)
        import server.main as server_module
    return server_module


class ServerSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

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

    def test_business_key_merge_advances_sequence(self) -> None:
        """同日日记被业务键合并后，序列号必须推进，否则其它设备永远拉不到这次修改。"""
        first = diary_change("2026-05-01", "A 版", datetime(2026, 1, 1))
        payload = self.server.push(self.server.PushRequest(changes=[first]), "Bearer token-a")
        self.assertEqual(payload["accepted"][0]["data"]["sync_seq"], 1)

        second = diary_change("2026-05-01", "B 版", datetime(2026, 1, 2))
        payload = self.server.push(self.server.PushRequest(changes=[second]), "Bearer token-a")
        merged = payload["accepted"][0]
        # 合并进的是第一行（保留本地 id），但必须拿到新的序列号
        self.assertEqual(merged["record_id"], first["data"]["id"])
        self.assertEqual(merged["data"]["sync_seq"], 2)

        pulled = self.server.pull(
            self.server.PullRequest(cursors={"diaries": {"sequence": 1}}), "Bearer token-a"
        )
        self.assertEqual([change["data"]["content"] for change in pulled["changes"]], ["B 版"])

    def test_lww_tie_push_returns_receipt_and_converges(self) -> None:
        """同一 updated_at、不同内容：按内容摘要决胜，败方也要拿到回执出队。"""
        from app.db.repo import payload_digest

        stamp = datetime(2026, 3, 1, 12, 0, 0)
        first = note_change("A 版", stamp)
        second = note_change("B 版", stamp)
        second["data"]["id"] = first["data"]["id"]

        self.server.push(self.server.PushRequest(changes=[first]), "Bearer tie-token")
        payload = self.server.push(self.server.PushRequest(changes=[second]), "Bearer tie-token")

        self.assertEqual(len(payload["accepted"]), 1, "败方也必须进 accepted 才能出队")
        a_wins = payload_digest(Note, first["data"]) >= payload_digest(Note, second["data"])
        winner = "A 版" if a_wins else "B 版"
        with self.Session() as session:
            stored = session.scalar(select(Note))
        self.assertEqual(stored.title, winner, "两端必须收敛到同一胜者")
        self.assertEqual(bool(payload["superseded"]), a_wins)

    def test_stale_push_is_reported_as_superseded(self) -> None:
        """被云端更新版本覆盖的推送必须回执，否则客户端会永久停在待同步队列。"""
        newer = note_change("新版本", datetime(2026, 1, 2))
        self.server.push(self.server.PushRequest(changes=[newer]), "Bearer token-a")

        stale = note_change("旧版本", datetime(2026, 1, 1))
        stale["data"]["id"] = newer["data"]["id"]
        payload = self.server.push(self.server.PushRequest(changes=[stale]), "Bearer token-a")

        self.assertEqual(payload["rejected"], [])
        self.assertEqual(len(payload["superseded"]), 1)
        self.assertEqual(payload["superseded"][0]["id"], newer["data"]["id"])
        # 同时进 accepted：客户端据此把这条记录从待同步队列里出队
        self.assertEqual(len(payload["accepted"]), 1)
        self.assertFalse(payload["accepted"][0]["changed"])

    def test_duplicate_diary_in_one_batch_does_not_fail_whole_push(self) -> None:
        """同一批里两条同日日记（并发/重试）不能整批 500。"""
        first = diary_change("2026-06-01", "甲", datetime(2026, 1, 1))
        second = diary_change("2026-06-01", "乙", datetime(2026, 1, 2))
        payload = self.server.push(
            self.server.PushRequest(changes=[first, second]), "Bearer token-a"
        )
        self.assertEqual(len(payload["accepted"]) + len(payload["rejected"]), 2)
        self.assertEqual(payload["rejected"], [])

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


class ServerUploadTests(unittest.TestCase):
    """POST /api/upload + /files 静态目录（图片端到端）。"""

    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)
        from fastapi.testclient import TestClient

        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)
        cls.temp_dir.cleanup()

    def test_upload_requires_token(self) -> None:
        response = self.client.post("/api/upload", files={"file": ("a.png", self.PNG, "image/png")})
        self.assertEqual(response.status_code, 401)

    def test_upload_stores_file_and_serves_it_back(self) -> None:
        response = self.client.post(
            "/api/upload",
            files={"file": ("a.png", self.PNG, "image/png")},
            headers={"Authorization": "Bearer token-a"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["url"].endswith(f"/files/{body['name']}"))
        stored = self.server.FILES_DIR / body["name"]
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), self.PNG)

        fetched = self.client.get(f"/files/{body['name']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.content, self.PNG)

    def test_same_content_is_deduplicated(self) -> None:
        headers = {"Authorization": "Bearer token-a"}
        first = self.client.post("/api/upload", files={"file": ("a.png", self.PNG, "image/png")}, headers=headers)
        second = self.client.post("/api/upload", files={"file": ("b.png", self.PNG, "image/png")}, headers=headers)
        self.assertEqual(first.json()["name"], second.json()["name"])

    def test_non_image_is_rejected(self) -> None:
        response = self.client.post(
            "/api/upload",
            files={"file": ("a.txt", b"hello", "text/plain")},
            headers={"Authorization": "Bearer token-a"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
