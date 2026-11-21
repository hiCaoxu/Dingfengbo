"""UI 助手组件测试：弹窗生命周期与后台查询槽。

用极简 FakePage 替代真实 Flet 页面：``run_thread``/``run_task`` 同步执行，
使后台化与弹窗逻辑可以在无 GUI 环境下断言。
"""

from __future__ import annotations

import asyncio
import unittest

import flet as ft

from app.ui.async_utils import background_section
from app.ui.widgets import close_modal, confirm, modal, open_dialog


def _walk(control):
    """遍历控件树（含 content / controls / actions），用于断言占位内容。"""
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from _walk(child)
    for child in getattr(control, "actions", None) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)


class FakePage:
    """模拟 Flet 0.86 的受管 dialog 栈（page.show_dialog）。"""

    def __init__(self) -> None:
        self.overlay: list = []
        self.dialogs: list = []
        self.snack_bar = None
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def show_dialog(self, dialog) -> None:
        if dialog in self.dialogs:
            raise RuntimeError("Dialog is already opened")
        dialog.open = True
        self.dialogs.append(dialog)
        self.update()

    def run_thread(self, handler) -> None:
        handler()

    def run_task(self, handler, *args) -> None:
        coro = handler(*args) if callable(handler) else handler
        asyncio.run(coro)


class LegacyPage(FakePage):
    """模拟 0.86 之前没有 show_dialog 的 Flet：退回 overlay 方案。"""

    show_dialog = None


class DialogLifecycleTests(unittest.TestCase):
    def test_modal_uses_managed_stack_when_available(self) -> None:
        page = FakePage()
        dialog = modal(page, "标题", ft.Text("内容"), [])
        self.assertTrue(dialog.open)
        self.assertIn(dialog, page.dialogs)
        self.assertEqual(page.overlay, [])

    def test_dismiss_closes_without_unmounting(self) -> None:
        """关闭只置 open=False：提前从栈里删除会丢掉 Flet 的 dismiss 回调，导致关不掉。"""
        page = FakePage()
        dialog = modal(page, "标题", ft.Text("内容"), [])
        before = page.updates

        close_modal(page, dialog)
        self.assertFalse(dialog.open)
        self.assertIn(dialog, page.dialogs)
        self.assertEqual(page.updates, before + 1)

    def test_repeated_dismiss_is_safe(self) -> None:
        page = FakePage()
        dialog = modal(page, "标题", ft.Text("内容"), [])
        close_modal(page, dialog)
        close_modal(page, dialog)
        self.assertFalse(dialog.open)

    def test_confirm_cancel_closes_dialog(self) -> None:
        page = FakePage()
        triggered: list[bool] = []
        confirm(page, "确定删除？", lambda: triggered.append(True))
        dialog = page.dialogs[-1]

        dialog.actions[0].on_click(None)
        self.assertFalse(dialog.open)
        self.assertEqual(triggered, [])

    def test_confirm_accept_runs_action_and_closes(self) -> None:
        page = FakePage()
        triggered: list[bool] = []
        confirm(page, "确定删除？", lambda: triggered.append(True))
        dialog = page.dialogs[-1]

        dialog.actions[1].on_click(None)
        self.assertFalse(dialog.open)
        self.assertEqual(triggered, [True])

    def test_open_dialog_falls_back_to_overlay(self) -> None:
        page = LegacyPage()
        dialog = ft.AlertDialog(title=ft.Text("标题"))
        open_dialog(page, dialog)
        self.assertTrue(dialog.open)
        self.assertIn(dialog, page.overlay)


class BackgroundSectionTests(unittest.TestCase):
    def test_renders_loader_result(self) -> None:
        page = FakePage()
        holder = background_section(page, lambda: [1, 2, 3], lambda data: ft.Text(str(sum(data))))
        self.assertIsInstance(holder.content, ft.Text)
        self.assertEqual(holder.content.value, "6")

    def test_loader_exception_keeps_placeholder(self) -> None:
        page = FakePage()

        def boom():
            raise RuntimeError("查询失败")

        holder = background_section(page, boom, lambda data: ft.Text("不该出现"), placeholder="加载中…")
        self.assertNotEqual(getattr(holder.content, "value", None), "不该出现")

    def test_failure_shows_retry_and_retry_recovers(self) -> None:
        page = FakePage()
        attempts = {"count": 0}

        def loader():
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("数据库忙")
            return [1, 2]

        holder = background_section(page, loader, lambda data: ft.Text(str(sum(data))))
        texts = [c.value for c in _walk(holder) if isinstance(c, ft.Text)]
        self.assertIn("加载失败（可能是数据库忙或磁盘异常）", texts)

        retry = next(c for c in _walk(holder) if isinstance(c, ft.TextButton))
        retry.on_click(None)
        self.assertEqual(attempts["count"], 2)
        self.assertIsInstance(holder.content, ft.Text)
        self.assertEqual(holder.content.value, "3")

    def test_renderer_exception_is_swallowed(self) -> None:
        page = FakePage()

        def bad_renderer(data):
            raise RuntimeError("渲染失败")

        holder = background_section(page, lambda: [], bad_renderer)
        self.assertIsNotNone(holder)


if __name__ == "__main__":
    unittest.main()
