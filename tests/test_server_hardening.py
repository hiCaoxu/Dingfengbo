"""协议与提醒加固：push 条数上限、pull 表白名单、明文地址提示、通知状态与退避。"""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.services import notify as notify_module
from app.services.notify import NotifyResult
from app.services.sync import insecure_remote_server
from app.ui import scheduler
from tests.test_server_sync import _boot_server, note_change


def _todo_change(title: str = "同步任务") -> dict:
    stamp = "2026-02-01T00:00:00"
    return {
        "table": "todos",
        "data": {
            "id": str(uuid4()),
            "owner_id": "local",
            "created_at": stamp,
            "updated_at": stamp,
            "is_deleted": False,
            "sync_status": "pending",
            "sync_seq": 0,
            "title": title,
            "note": "",
            "due_date": None,
            "due_time": "",
            "tags": "[]",
            "lunar_reminder": "",
            "repeat_rule": "none",
            "repeat_config": "",
            "priority": "",
            "quadrant": "",
            "remind_days": "[]",
            "remind_days_before": -1,
            "remind_time": "",
            "pomodoro_count": 0,
            "completed": False,
            "completed_at": None,
            "is_template": False,
        },
    }


class ServerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._original = self.server.SessionLocal
        self.server.SessionLocal = self.Session

    def tearDown(self) -> None:
        self.server.SessionLocal = self._original
        self.engine.dispose()

    def test_push_body_has_a_count_limit(self) -> None:
        with self.assertRaises(ValidationError):
            self.server.PushRequest(changes=[{} for _ in range(501)])
        # 边界值必须可用
        padded = self.server.PushRequest(changes=[{} for _ in range(500)])
        self.assertEqual(len(padded.changes), 500)

    def test_pull_tables_whitelist_limits_work(self) -> None:
        self.server.push(
            self.server.PushRequest(
                changes=[note_change("笔记", datetime(2026, 2, 1)), _todo_change()]
            ),
            "Bearer wh-token",
        )
        payload = self.server.pull(
            self.server.PullRequest(tables=["notes"]), "Bearer wh-token"
        )
        self.assertEqual({change["table"] for change in payload["changes"]}, {"notes"})
        self.assertEqual(set(payload["cursors"]), {"notes"}, "未勾选的表不应推进游标")
        self.assertFalse(payload.get("has_more"), "未勾选的表不应让 has_more 永远为真")

    def test_pull_rejects_unknown_table(self) -> None:
        with self.assertRaises(ValidationError):
            self.server.PullRequest(tables=["hackers"])

    def test_pull_without_tables_returns_everything(self) -> None:
        self.server.push(
            self.server.PushRequest(
                changes=[note_change("笔记2", datetime(2026, 2, 2)), _todo_change("任务2")]
            ),
            "Bearer all-token",
        )
        payload = self.server.pull(self.server.PullRequest(), "Bearer all-token")
        self.assertEqual({change["table"] for change in payload["changes"]},
                         {"notes", "todos"})


class InsecureServerTests(unittest.TestCase):
    def test_plain_http_remote_is_flagged(self) -> None:
        self.assertTrue(insecure_remote_server("http://43.142.92.115:8000"))
        self.assertTrue(insecure_remote_server("http://example.com"))

    def test_https_and_loopback_are_not_flagged(self) -> None:
        self.assertFalse(insecure_remote_server("https://example.com"))
        self.assertFalse(insecure_remote_server("http://127.0.0.1:8000"))
        self.assertFalse(insecure_remote_server("http://localhost:8000"))
        self.assertFalse(insecure_remote_server(""))


class NotifyResultTests(unittest.TestCase):
    def test_toast_is_reported(self) -> None:
        with mock.patch.object(notify_module, "_powershell_toast", return_value=True):
            self.assertIs(notify_module.notify("标题", "内容"), NotifyResult.TOAST)

    def test_beep_fallback_is_reported(self) -> None:
        with mock.patch.object(notify_module, "_powershell_toast", return_value=False), \
             mock.patch.object(notify_module, "_beep", return_value=True):
            self.assertIs(notify_module.notify("标题", "内容"), NotifyResult.BEEP)

    def test_total_failure_is_reported(self) -> None:
        with mock.patch.object(notify_module, "_powershell_toast", return_value=False), \
             mock.patch.object(notify_module, "_beep", return_value=False):
            self.assertIs(notify_module.notify("标题", "内容"), NotifyResult.FAILED)


class NotifyBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        scheduler._NOTIFY_RETRY.clear()

    def tearDown(self) -> None:
        scheduler._NOTIFY_RETRY.clear()

    def test_backoff_blocks_immediate_retry(self) -> None:
        item = {"todo_id": "t1", "fire_at": "2026-01-01T10:00:00"}
        self.assertTrue(scheduler._notify_retry_ready(item), "首次应允许发送")
        scheduler._mark_notify_failed(item)
        self.assertFalse(scheduler._notify_retry_ready(item), "失败后应退避")
        record = scheduler._NOTIFY_RETRY[scheduler._notify_key(item)]
        self.assertEqual(record[0], 1)

    def test_backoff_grows_and_caps(self) -> None:
        item = {"todo_id": "t2", "fire_at": "2026-01-01T10:00:00"}
        delays = []
        for _ in range(8):
            scheduler._mark_notify_failed(item)
            record = scheduler._NOTIFY_RETRY[scheduler._notify_key(item)]
            delays.append(record[1] - time.time())
        self.assertLess(delays[0], delays[-1])
        self.assertLessEqual(max(delays), scheduler._NOTIFY_RETRY_CAP_SECONDS + 1)

    def test_ready_after_backoff_window(self) -> None:
        item = {"todo_id": "t3", "fire_at": "2026-01-01T10:00:00"}
        scheduler._mark_notify_failed(item)
        scheduler._NOTIFY_RETRY[scheduler._notify_key(item)] = (1, time.time() - 0.1)
        self.assertTrue(scheduler._notify_retry_ready(item))

    def test_success_clears_failure_state(self) -> None:
        item = {"todo_id": "t4", "fire_at": "2026-01-01T10:00:00"}
        scheduler._mark_notify_failed(item)
        scheduler._clear_notify_failure(item)
        self.assertTrue(scheduler._notify_retry_ready(item))
        self.assertEqual(scheduler._NOTIFY_RETRY, {})


if __name__ == "__main__":
    unittest.main()
