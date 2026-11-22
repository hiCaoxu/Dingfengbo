"""数值校验：非有限值/越界/手误不能被静默写成 0 或清空数据。"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import flet as ft

from app import theme as th
from app.db.models import Exercise
from app.db.session import session_scope
from app.services import budget as budget_service
from app.ui import budget_dialogs, forms
from app.ui.forms import _number, open_exercise_dialog

from tests.helpers import reset_db


class _FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.updates = 0

    def update(self) -> None:
        self.updates += 1

    def run_task(self, handler) -> None:  # noqa: ARG002
        return None

    def run_thread(self, handler) -> None:  # noqa: ARG002
        return None


def _text_fields(control) -> list[ft.TextField]:
    found: list[ft.TextField] = []
    stack = [control]
    while stack:
        current = stack.pop()
        if isinstance(current, ft.TextField):
            found.append(current)
        for name in ("controls", "content", "actions"):
            value = getattr(current, name, None)
            if isinstance(value, list):
                stack.extend(value)
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                stack.append(value)
    return found


def _save_button(dialog) -> ft.Button:
    buttons = [item for item in dialog.actions if isinstance(item, ft.Button)]
    assert buttons, "弹窗里应有主按钮"
    return buttons[-1]


class NumberHelperTests(unittest.TestCase):
    def test_accepts_plain_numbers(self) -> None:
        self.assertEqual(_number("12.5", label="金额"), (12.5, None))

    def test_empty_is_allowed_by_default(self) -> None:
        self.assertEqual(_number("", label="金额"), (None, None))
        self.assertEqual(_number(None, label="金额"), (None, None))

    def test_rejects_nan_and_inf(self) -> None:
        for raw in ("nan", "NaN", "inf", "-inf", "1e400"):
            value, error = _number(raw, label="体重")
            self.assertIsNone(value, raw)
            self.assertIn("有限数值", error, raw)

    def test_rejects_garbage(self) -> None:
        value, error = _number("abc", label="金额")
        self.assertIsNone(value)
        self.assertIn("格式不正确", error)

    def test_range_checks(self) -> None:
        _, error = _number("0.5", label="金额", minimum=1)
        self.assertIn("不能小于", error)
        _, error = _number("101", label="体脂率", maximum=100)
        self.assertIn("不能大于", error)


class ExerciseDialogValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.page = _FakePage()
        self.snacks: list[tuple[str, str]] = []

    def _dialog(self):
        return open_exercise_dialog(self.page, None, lambda: None)

    def test_infinite_minutes_is_rejected(self) -> None:
        with mock.patch.object(forms, "snack",
                               side_effect=lambda _p, text, color: self.snacks.append((text, color))):
            dialog = self._dialog()
            minutes = next(f for f in _text_fields(dialog) if "时长" in (f.label or ""))
            minutes.value = "inf"
            _save_button(dialog).on_click(mock.Mock())
        self.assertTrue(self.snacks, "inf 时长必须报错")
        self.assertIn("有限数值", self.snacks[0][0])
        with session_scope() as session:
            self.assertEqual(session.query(Exercise).count(), 0, "校验失败不能落库")

    def test_negative_distance_is_rejected(self) -> None:
        with mock.patch.object(forms, "snack",
                               side_effect=lambda _p, text, color: self.snacks.append((text, color))):
            dialog = self._dialog()
            distance = next(f for f in _text_fields(dialog) if "距离" in (f.label or ""))
            distance.value = "-3"
            _save_button(dialog).on_click(mock.Mock())
        self.assertTrue(self.snacks)
        self.assertIn("不能小于", self.snacks[0][0])

    def test_valid_input_still_saves(self) -> None:
        with mock.patch.object(forms, "snack", side_effect=lambda *a: None):
            dialog = self._dialog()
            minutes = next(f for f in _text_fields(dialog) if "时长" in (f.label or ""))
            minutes.value = "45"
            _save_button(dialog).on_click(mock.Mock())
        with session_scope() as session:
            saved = session.query(Exercise).one()
        self.assertEqual(saved.minutes, 45)


class BudgetDialogValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.page = _FakePage()
        self.snacks: list[tuple[str, str]] = []
        self.month = date.today().isoformat()[:7]
        self._patch = mock.patch.object(
            budget_dialogs, "snack",
            side_effect=lambda _p, text, color: self.snacks.append((text, color)),
        )
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def _budget(self) -> float:
        rows = {row.category: float(row.amount or 0) for row in budget_service.budgets(self.month)}
        return rows.get("", 0.0)

    def test_invalid_text_does_not_clear_budget(self) -> None:
        budget_service.upsert_budget(self.month, "", 1000.0)
        dialog = budget_dialogs.open_budget_dialog(self.page, lambda: None)
        total = next(f for f in _text_fields(dialog) if "总预算" in (f.label or ""))
        total.value = "1O00"  # 手误：字母 O
        _save_button(dialog).on_click(mock.Mock())

        self.assertTrue(self.snacks, "非法金额必须提示")
        self.assertIn("格式不正确", self.snacks[0][0])
        self.assertEqual(self._budget(), 1000.0, "非法输入不能把整月预算清空")

    def test_negative_amount_is_rejected(self) -> None:
        dialog = budget_dialogs.open_budget_dialog(self.page, lambda: None)
        total = next(f for f in _text_fields(dialog) if "总预算" in (f.label or ""))
        total.value = "-100"
        _save_button(dialog).on_click(mock.Mock())
        self.assertTrue(self.snacks)
        self.assertIn("0 ~", self.snacks[0][0])

    def test_blank_clears_category_on_purpose(self) -> None:
        budget_service.upsert_budget(self.month, "", 1000.0)
        dialog = budget_dialogs.open_budget_dialog(self.page, lambda: None)
        total = next(f for f in _text_fields(dialog) if "总预算" in (f.label or ""))
        total.value = ""  # 用户主动留空 = 清除
        _save_button(dialog).on_click(mock.Mock())
        self.assertEqual(self._budget(), 0.0)
        errors = [text for text, color in self.snacks if color == th.ACCENT]
        self.assertFalse(errors, f"留空是显式清除，不应报错：{errors}")


if __name__ == "__main__":
    unittest.main()
