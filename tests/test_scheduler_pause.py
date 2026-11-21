"""自动同步暂停开关：恢复备份/停机窗口内不得触发后台同步。"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from app.ui import scheduler


class AutoSyncPauseTests(unittest.TestCase):
    def tearDown(self) -> None:
        scheduler.resume_auto_sync()
        scheduler._page = None
        scheduler._pending_timer = None

    def test_paused_sync_is_skipped_until_resumed(self) -> None:
        called = threading.Event()
        stub = mock.Mock(side_effect=lambda *args, **kwargs: called.set())
        with mock.patch.object(scheduler.sync_client, "sync_once", stub):
            scheduler.pause_auto_sync("测试恢复")
            self.assertTrue(scheduler.auto_sync_paused())
            scheduler._run_sync_async()
            self.assertFalse(called.wait(0.3), "暂停期间不应启动同步线程")
            self.assertFalse(stub.called)

            scheduler.resume_auto_sync()
            self.assertFalse(scheduler.auto_sync_paused())
            scheduler._run_sync_async()
            self.assertTrue(called.wait(2.0), "恢复后应立刻允许同步")

    def test_write_trigger_is_skipped_when_paused(self) -> None:
        scheduler._page = object()  # 绕过「没有页面就不排程」的前置条件
        scheduler.pause_auto_sync("测试恢复")
        scheduler.schedule_sync(1)
        self.assertIsNone(scheduler._pending_timer)

    def test_resume_is_idempotent(self) -> None:
        scheduler.resume_auto_sync()
        self.assertFalse(scheduler.auto_sync_paused())
        scheduler.resume_auto_sync()
        self.assertFalse(scheduler.auto_sync_paused())


if __name__ == "__main__":
    unittest.main()
