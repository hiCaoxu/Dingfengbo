"""UI 助手组件测试：弹窗生命周期与后台查询槽。

用极简 FakePage 替代真实 Flet 页面：``run_thread``/``run_task`` 同步执行，
使后台化逻辑可以在无 GUI 环境下断言。
"""

from __future__ import annotations

import asyncio
import unittest

import flet as ft

from app.ui.async_utils import background_section
from app.ui.widgets import close_modal, confirm, modal


def _walk(control):
    """遍历控件树（含 content 与 controls），用于断言错误占位的内容。"""
    yield control
    for child in getattr(control, "controls", None) or []:
        yield from _walk(child)
    content = getattr(control, "content", None)
    if content is not None and not isinstance(content, str):
        yield from _walk(content)


class FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.snack_bar = None
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def run_thread(self, handler) -> None:
        handler()

    def run_task(self, handler, *args) -> None:
        coro = handler(*args) if callable(handler) else handler
        asyncio.run(coro)


class ModalLifecycleTests(unittest.TestCase):
    def test_modal_is_appended_and_removed_on_close(self) -> None:
        page = FakePage()
        dialog = modal(page, "标题", ft.Text("内容"), [])
        self.assertIn(dialog, page.overlay)
        self.assertTrue(dialog.open)

        close_modal(page, dialog)
        self.assertFalse(dialog.open)
        self.assertFalse(any(control is dialog for control in page.overlay))

    def test_dismiss_removes_only_the_target_dialog(self) -> None:
        """Flet 控件按值相等，关闭时应按身份移除，不能误删参数相同的另一个弹窗。"""
        page = FakePage()
        first = modal(page, "标题", ft.Text("内容"), [])
        second = modal(page, "标题", ft.Text("内容"), [])
        self.assertEqual(first, second)  # 值相等，正是容易踩坑的场景

        close_modal(page, first)
        self.assertFalse(any(control is first for control in page.overlay))
        self.assertTrue(any(control is second for control in page.overlay))
        self.assertTrue(second.open)

    def test_repeated_close_is_safe(self) -> None:
        page = FakePage()
        dialog = modal(page, "标题", ft.Text("内容"), [])
        close_modal(page, dialog)
        before = page.updates
        close_modal(page, dialog)  # 已不在 overlay 中，不应抛异常
        self.assertEqual(page.updates, before + 1)
        self.assertEqual(page.overlay, [])

    def test_confirm_dialog_does_not_leave_overlay_residue(self) -> None:
        page = FakePage()
        triggered: list[bool] = []
        confirm(page, "确定删除？", lambda: triggered.append(True))
        dialog = page.overlay[-1]
        cancel = dialog.actions[0]
        cancel.on_click(None)
        self.assertNotIn(dialog, page.overlay)
        self.assertEqual(triggered, [])


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
