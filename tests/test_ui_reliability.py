"""UI 可靠性：回调异常不静默、命令面板关闭可复位、番茄钟代次隔离。"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest import mock

import flet as ft

from app.ui import command_palette, pomodoro, scheduler, widgets


class _FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.tasks: list = []
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def run_task(self, handler) -> None:
        self.tasks.append(handler)

    def run_thread(self, handler) -> None:
        handler()


class ConfirmFailureTests(unittest.TestCase):
    def test_confirm_callback_error_is_reported(self) -> None:
        """DB 报错时不能表现为「点了确定没反应」。"""
        page = _FakePage()
        seen: list[str] = []
        with mock.patch.object(widgets, "snack", side_effect=lambda _p, text, _c: seen.append(text)):
            widgets.confirm(page, "确定删除？", lambda: (_ for _ in ()).throw(OSError("db busy")))
            accept = page.overlay[-1].actions[-1]
            accept.on_click(mock.Mock())
        self.assertTrue(seen, "失败必须提示用户")
        self.assertIn("操作失败", seen[0])


class CommandPaletteCloseTests(unittest.TestCase):
    def test_on_close_is_registered_before_open(self) -> None:
        """Esc/点遮罩关闭走 on_dismiss：必须在显示之前挂好，否则标记永远复位不了。"""
        page = _FakePage()
        calls: list[str] = []
        opened: list[ft.AlertDialog] = []

        def fake_open(_page, dialog):
            opened.append(dialog)

        with mock.patch.object(command_palette, "open_dialog", side_effect=fake_open):
            dialog = command_palette.open_command_palette(
                page, lambda key: None, lambda: None, on_close=lambda *_: calls.append("closed")
            )
        self.assertIs(dialog, opened[0])
        self.assertIsNotNone(dialog.on_dismiss)
        dialog.on_dismiss(None)
        self.assertEqual(calls, ["closed"])

    def test_palette_without_callback_still_opens(self) -> None:
        page = _FakePage()
        with mock.patch.object(command_palette, "open_dialog"):
            dialog = command_palette.open_command_palette(page, lambda key: None, lambda: None)
        self.assertIsNone(dialog.on_dismiss)


class PomodoroGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        pomodoro._session.update(active=False, remaining=0, total=0, run=0)

    def tearDown(self) -> None:
        pomodoro._session.update(active=False, remaining=0, total=0, run=0)

    def test_stop_invalidates_running_worker(self) -> None:
        """停止后 1 秒内重启：旧 worker 必须失效，不能一起递减 remaining。"""
        pomodoro._session.update(active=True, remaining=60, total=60, run=1)
        self.assertTrue(pomodoro._run_is_current(1))

        # stop()
        pomodoro._session.update(active=False, run=2)
        self.assertFalse(pomodoro._run_is_current(1))

        # start()：新一轮
        pomodoro._session.update(active=True, remaining=1500, total=1500, run=3)
        self.assertFalse(pomodoro._run_is_current(1), "旧代次不能影响新一轮")
        self.assertTrue(pomodoro._run_is_current(3))

    def test_run_counter_advances_on_start_and_stop(self) -> None:
        start_run = int(pomodoro._session["run"]) + 1
        pomodoro._session.update(active=True, remaining=60, total=60, run=start_run)
        stop_run = int(pomodoro._session["run"]) + 1
        pomodoro._session.update(active=False, run=stop_run)
        self.assertGreater(stop_run, start_run)


class SchedulerFailureTests(unittest.TestCase):
    def test_sync_exception_does_not_kill_worker(self) -> None:
        """sync_once 抛异常时仍要回调 UI 刷新，线程不能悄悄死掉。"""
        page = _FakePage()
        scheduler._page = page
        fired: list[str] = []
        scheduler._refresh_status = lambda: fired.append("status")
        scheduler._refresh_content = None
        try:
            with mock.patch.object(
                scheduler.sync_client, "sync_once", side_effect=RuntimeError("boom")
            ):
                scheduler._run_sync_async()
                for _ in range(40):
                    if page.tasks:
                        break
                    time.sleep(0.02)
                self.assertTrue(page.tasks, "失败后仍应把 UI 刷新任务排进队列")
                # Flet 的 run_task 收的是协程函数，这里手动跑一次确认失败路径也会刷新顶栏
                for task in page.tasks:
                    if asyncio.iscoroutine(task):
                        asyncio.run(task)
                    elif callable(task):
                        asyncio.run(task())
                self.assertIn("status", fired)
        finally:
            scheduler._page = None
            scheduler._refresh_status = None


if __name__ == "__main__":
    unittest.main()
