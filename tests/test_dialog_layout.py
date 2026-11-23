"""录入弹窗布局：行内录入控件必须显式给宽度，整行也不能宽过弹窗内容宽度。

背景：Flutter 的 Row 主轴约束是「无界」的，行内 TextField/Dropdown 不写 width
时会按自身期望宽度铺开，两个加起来就超过弹窗内容宽度、溢出到弹窗外面
（记录锻炼/饮食/睡眠都出现过）。这里把「弹窗内容宽度」和「行内宽度之和」钉死，
以后新增弹窗漏写宽度会直接报错。
"""

from __future__ import annotations

import unittest

import flet as ft

from app.db.models import Account
from app.db.repo import create
from app.db.session import session_scope
from app.ui import forms

from tests.helpers import reset_db
from tests.test_ui_helpers import FakePage


def _walk(control):
    yield control
    for name in ("controls", "content"):
        value = getattr(control, name, None)
        if isinstance(value, list):
            for child in value:
                yield from _walk(child)
        elif isinstance(value, ft.Control):
            yield from _walk(value)


def _rows(control) -> list[ft.Row]:
    return [item for item in _walk(control) if isinstance(item, ft.Row)]


class DialogLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.page = FakePage()
        with session_scope() as session:
            create(session, Account, name="招商银行", kind="银行卡", initial_balance=1000)

    def _assert_fits(self, dialog) -> None:
        content = dialog.content
        width = content.width
        self.assertTrue(width, "弹窗内容必须有宽度，否则行内控件只能按内在宽度铺开")
        for row in _rows(content):
            inputs = [
                child for child in row.controls
                if isinstance(child, (ft.TextField, ft.Dropdown))
            ]
            if not inputs:
                continue
            for control in inputs:
                self.assertTrue(
                    control.width or control.expand,
                    f"「{control.label}」既没 width 也没 expand：会按内在宽度把弹窗撑破",
                )
            total = sum(control.width or 0 for control in inputs)
            total += row.spacing * (len(inputs) - 1)
            self.assertLessEqual(
                total, width, f"一行录入框合计 {total}，超过弹窗内容宽度 {width}"
            )
            self.assertLessEqual(
                max(control.width or 0 for control in inputs), width,
                "单个录入框比弹窗内容还宽",
            )

    def test_sleep_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_sleep_dialog(self.page))

    def test_exercise_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_exercise_dialog(self.page))

    def test_diet_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_diet_dialog(self.page))

    def test_weight_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_weight_dialog(self.page))

    def test_transaction_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_transaction_dialog(self.page))

    def test_entertainment_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_entertainment_dialog(self.page))

    def test_account_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_account_dialog(self.page))

    def test_note_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_note_dialog(self.page))

    def test_todo_dialog_inputs_fit(self) -> None:
        self._assert_fits(forms.open_todo_dialog(self.page))


if __name__ == "__main__":
    unittest.main()
