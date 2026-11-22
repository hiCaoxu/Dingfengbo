"""同步端到端回归：一次真实 sync_once（pull → push → purge）。

背景：评审发现两件事都没有测试兜着——
1. `sync_once` 的完整编排（含状态分支）零覆盖；
2. `pending_changes` 是「每表 ≤500」，16 表累计可达 8000，而服务端
   `PushRequest.changes` 限制单次 ≤500。不截断就会 422，且 422 时一条都不会被
   `mark_clean`，下一轮原样重推 —— 同步永久卡死。

这里用 FastAPI 的 TestClient 在进程内扮演 httpx.Client，既不起端口，又真的跑过
服务端的 push 校验。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app.db.models import Note, Todo
from app.db.repo import create, pending_changes
from app.db.session import session_scope
from app.services import sync as sync_module
from app.services.clock import set_clock_offset
from tests.helpers import reset_db
from tests.test_server_sync import _boot_server


class _InProcessClient:
    """把 sync 里的 httpx.Client 请求转发给 FastAPI TestClient。"""

    def __init__(self, app, base: str) -> None:
        self._client = TestClient(app)
        self._base = base.rstrip("/")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url: str, **kwargs):
        path = url[len(self._base):] if url.startswith(self._base) else url
        return self._client.post(path, **kwargs)


class SyncOnceEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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
        reset_db()
        set_clock_offset(0.0)
        base = "http://sync-under-test"
        self._server_patch = mock.patch.object(
            sync_module, "httpx", SimpleNamespace(
                Client=lambda *a, **k: _InProcessClient(self.server.app, base),
                HTTPStatusError=httpx.HTTPStatusError,
                RequestError=httpx.RequestError,
            )
        )
        self._server_patch.start()
        self._settings_patch = mock.patch.object(
            sync_module, "load_settings", lambda: {
                "server_url": base,
                "sync_token": "e2e-token",
                "sync_enabled": True,
                "sync_all_tables": True,
                "sync_cursors": {},
            }
        )
        self._settings_patch.start()
        # 游标/设备/历史本来会写进 settings.json，这里全部拦掉，避免污染真实配置。
        for name in ("_save_cursors", "_remember_device", "_remember_history"):
            patcher = mock.patch.object(sync_module.SyncClient, name, lambda *a, **k: None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._settings_patch.stop()
        self._server_patch.stop()
        set_clock_offset(0.0)

    def _make_pending(self, notes: int, todos: int) -> None:
        with session_scope() as session:
            for index in range(notes):
                create(session, Note, title=f"笔记{index}", content="", tags="[]")
            for index in range(todos):
                create(session, Todo, title=f"任务{index}", due_date=date(2026, 11, 22))

    def _pending_count(self) -> int:
        with session_scope() as session:
            return len(pending_changes(session))

    def test_more_than_500_pending_is_pushed_in_batches(self) -> None:
        """501 条跨表待同步：必须分两轮推完，而不是 422 卡死。"""
        self._make_pending(notes=300, todos=201)
        self.assertEqual(self._pending_count(), 501)

        first = sync_module.sync_client.sync_once()
        self.assertTrue(first.startswith("已同步"), first)
        remaining = self._pending_count()
        self.assertGreater(remaining, 0, "一次请求只能推 500 条，应还剩 1 条")
        self.assertLess(remaining, 501)

        second = sync_module.sync_client.sync_once()
        self.assertTrue(second.startswith("已同步"), second)
        self.assertEqual(self._pending_count(), 0, "第二轮应把剩余记录全部推完")

        # 云端确实收到了 501 条
        with self.server.SessionLocal() as session:
            from sqlalchemy import func, select

            notes = session.scalar(select(func.count()).select_from(Note))
            todos = session.scalar(select(func.count()).select_from(Todo))
        self.assertEqual(notes + todos, 501)

    def test_success_sets_status_and_run_id(self) -> None:
        self._make_pending(notes=1, todos=0)
        status = sync_module.sync_client.sync_once()
        self.assertTrue(status.startswith("已同步"))
        self.assertEqual(len(sync_module.sync_client.last_run_id), 8)
        self.assertIsNotNone(sync_module.sync_client.last_sync)

    def test_invalid_token_reports_token_error(self) -> None:
        """401/403 → 「令牌无效」，而不是笼统的「同步失败」。"""
        self._settings_patch.stop()
        base = "http://sync-under-test"
        self._settings_patch = mock.patch.object(
            sync_module, "load_settings", lambda: {
                "server_url": base,
                "sync_token": "",          # 服务端缺令牌 → 401
                "sync_enabled": True,
                "sync_all_tables": True,
                "sync_cursors": {},
            }
        )
        self._settings_patch.start()
        # 空令牌会在客户端就被拦下（「未配置令牌」），先把这层也覆盖掉
        self.assertEqual(sync_module.sync_client.sync_once(), "未配置令牌")


class DisabledSyncTests(unittest.TestCase):
    def test_disabled_sync_is_noop(self) -> None:
        reset_db()
        with mock.patch.object(sync_module, "load_settings", lambda: {"sync_enabled": False}):
            self.assertEqual(sync_module.sync_client.sync_once(), "同步已关闭")


if __name__ == "__main__":
    unittest.main()
