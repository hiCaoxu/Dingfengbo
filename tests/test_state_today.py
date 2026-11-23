"""跨天同步：按「启动日」固化的日期状态要跟着刷新到今天。

应用常驻过夜/收托盘后 ``date.today()`` 变了，但首页日历卡、任务月历、日记选中
日期都还是启动那天——用户看到的就是「首页数据概览的日历卡还是昨天的日期」。
"""

from __future__ import annotations

import unittest
from datetime import date

from app.ui import state as ui_state


class SyncTodayTests(unittest.TestCase):
    def setUp(self) -> None:
        state = ui_state.state
        self._snapshot = (
            state.today,
            state.home.year,
            state.todo.year,
            state.todo.month,
            state.diary.year,
            state.diary.month,
            state.diary.selected,
        )

    def tearDown(self) -> None:
        state = ui_state.state
        (
            state.today,
            state.home.year,
            state.todo.year,
            state.todo.month,
            state.diary.year,
            state.diary.month,
            state.diary.selected,
        ) = self._snapshot

    def _freeze(self, day: date) -> None:
        """把状态摆成「进程在 day 那天启动、用户什么都没动」的样子。"""
        state = ui_state.state
        state.today = day
        state.home.year = day.year
        state.todo.year, state.todo.month = day.year, day.month
        state.diary.year, state.diary.month = day.year, day.month
        state.diary.selected = day

    def test_rolls_forward_to_today(self) -> None:
        self._freeze(date(2026, 11, 22))
        ui_state.sync_today(date(2026, 11, 23))
        state = ui_state.state
        self.assertEqual(state.diary.selected, date(2026, 11, 23))
        self.assertEqual((state.diary.year, state.diary.month), (2026, 11))
        self.assertEqual((state.todo.year, state.todo.month), (2026, 11))
        self.assertEqual(state.home.year, 2026)

    def test_crosses_month_and_year(self) -> None:
        self._freeze(date(2026, 12, 31))
        ui_state.sync_today(date(2027, 1, 1))
        state = ui_state.state
        self.assertEqual(state.diary.selected, date(2027, 1, 1))
        self.assertEqual((state.diary.year, state.diary.month), (2027, 1))
        self.assertEqual((state.todo.year, state.todo.month), (2027, 1))
        self.assertEqual(state.home.year, 2027)

    def test_keeps_user_selected_diary_date(self) -> None:
        self._freeze(date(2026, 11, 22))
        ui_state.state.diary.selected = date(2026, 11, 1)  # 用户手动选了别的日子
        ui_state.sync_today(date(2026, 11, 23))
        self.assertEqual(ui_state.state.diary.selected, date(2026, 11, 1))

    def test_keeps_user_browsed_month(self) -> None:
        self._freeze(date(2026, 11, 22))
        ui_state.state.diary.year, ui_state.state.diary.month = 2026, 10  # 用户翻到 10 月
        ui_state.sync_today(date(2026, 11, 23))
        # 月历和选中日期要么一起走、要么一起留，不能"日期跳了月历没跳"。
        self.assertEqual((ui_state.state.diary.year, ui_state.state.diary.month), (2026, 10))
        self.assertEqual(ui_state.state.diary.selected, date(2026, 11, 22))

    def test_keeps_user_browsed_task_month(self) -> None:
        self._freeze(date(2026, 11, 22))
        ui_state.state.todo.year, ui_state.state.todo.month = 2026, 12
        ui_state.sync_today(date(2027, 1, 1))
        self.assertEqual((ui_state.state.todo.year, ui_state.state.todo.month), (2026, 12))

    def test_same_day_is_noop(self) -> None:
        self._freeze(date(2026, 11, 23))
        ui_state.state.diary.selected = date(2026, 11, 22)
        self.assertEqual(ui_state.sync_today(date(2026, 11, 23)), date(2026, 11, 23))
        self.assertEqual(ui_state.state.diary.selected, date(2026, 11, 22))


if __name__ == "__main__":
    unittest.main()
