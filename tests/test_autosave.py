"""自动保存状态机：最新快照胜出、失败重排队、丢弃生效。"""

from __future__ import annotations

import time
import unittest

from app.ui import autosave


class AutosaveTests(unittest.TestCase):
    def setUp(self) -> None:
        autosave.reset_for_tests()

    def tearDown(self) -> None:
        autosave.reset_for_tests()

    def test_latest_snapshot_wins(self) -> None:
        """连续输入时只有最后一次快照落库（旧实现会把旧值写进去）。"""
        written: list[str] = []
        writer = lambda _key, snapshot: written.append(snapshot)  # noqa: E731
        autosave.schedule("k", "v1", writer, 30)
        autosave.schedule("k", "v2", writer, 30)
        time.sleep(0.2)
        self.assertEqual(written, ["v2"])

    def test_flush_now_writes_pending_immediately(self) -> None:
        written: list[str] = []
        autosave.schedule("k", "v", lambda _k, s: written.append(s), 10_000)
        self.assertTrue(autosave.flush_now())
        self.assertEqual(written, ["v"])
        self.assertFalse(autosave.has_pending())

    def test_flush_now_without_pending_is_noop(self) -> None:
        self.assertTrue(autosave.flush_now())

    def test_discard_drops_pending(self) -> None:
        written: list[str] = []
        autosave.schedule("k", "v", lambda _k, s: written.append(s), 30)
        autosave.discard()
        time.sleep(0.15)
        self.assertEqual(written, [])
        self.assertFalse(autosave.has_pending())

    def test_write_failure_requeues_snapshot(self) -> None:
        attempts: list[str] = []

        def flaky(_key, snapshot):
            attempts.append(snapshot)
            if len(attempts) == 1:
                raise OSError("disk busy")

        autosave.schedule("k", "v", flaky, 20)
        time.sleep(0.2)
        self.assertTrue(autosave.has_pending(), "写库失败后应把快照放回队列")
        self.assertTrue(autosave.flush_now())
        self.assertEqual(attempts, ["v", "v"])

    def test_newer_edit_replaces_failed_snapshot(self) -> None:
        written: list[str] = []

        def flaky(_key, snapshot):
            if snapshot == "old":
                raise OSError("fail")
            written.append(snapshot)

        autosave.schedule("k", "old", flaky, 20)
        time.sleep(0.2)
        self.assertTrue(autosave.has_pending())
        autosave.schedule("k", "new", flaky, 20)
        time.sleep(0.2)
        self.assertEqual(written, ["new"], "失败重排队不能覆盖更新的输入")
        self.assertFalse(autosave.has_pending())

    def test_second_flush_after_failure_still_works(self) -> None:
        """失败后 flush_now 仍能写入（用户切页时会触发一次）。"""
        calls: list[str] = []

        def flaky(_key, snapshot):
            calls.append(snapshot)
            if len(calls) == 1:
                raise OSError("fail")

        autosave.schedule("k", "v", flaky, 30)
        time.sleep(0.2)
        self.assertTrue(autosave.flush_now())
        self.assertEqual(calls, ["v", "v"])


if __name__ == "__main__":
    unittest.main()
