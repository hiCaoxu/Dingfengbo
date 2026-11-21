"""待办提醒：时刻计算、到点收集与去重。"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import Todo
from app.db.repo import create
from app.services.reminders import (
    collect_due,
    describe,
    parse_offsets,
    reminder_at,
    reminder_times,
)


class ReminderAtTests(unittest.TestCase):
    def test_days_before_and_time(self) -> None:
        todo = Todo(
            due_date=date(2026, 12, 1),
            due_time="10:00",
            remind_days_before=2,
            remind_time="08:30",
        )
        self.assertEqual(reminder_at(todo), datetime(2026, 11, 29, 8, 30))

    def test_falls_back_to_due_time_then_default(self) -> None:
        todo = Todo(
            due_date=date(2026, 12, 1),
            due_time="18:45",
            remind_days_before=0,
            remind_time="",
        )
        self.assertEqual(reminder_at(todo), datetime(2026, 12, 1, 18, 45))

        todo = Todo(due_date=date(2026, 12, 1), due_time="", remind_days_before=0)
        self.assertEqual(reminder_at(todo), datetime(2026, 12, 1, 9, 0))

    def test_disabled_and_missing_due_date(self) -> None:
        self.assertIsNone(reminder_at(Todo(due_date=date(2026, 12, 1), remind_days_before=-1)))
        self.assertIsNone(reminder_at(Todo(due_date=None, remind_days_before=0)))


class MultiOffsetTests(unittest.TestCase):
    def test_multiple_offsets_sorted(self) -> None:
        todo = Todo(
            due_date=date(2026, 12, 10), due_time="09:00", remind_days="[1, 3, 7]"
        )
        self.assertEqual(parse_offsets(todo), [1, 3, 7])
        self.assertEqual(
            reminder_times(todo),
            [
                datetime(2026, 12, 3, 9, 0),
                datetime(2026, 12, 7, 9, 0),
                datetime(2026, 12, 9, 9, 0),
            ],
        )
        self.assertEqual(reminder_at(todo), datetime(2026, 12, 3, 9, 0))

    def test_new_field_wins_over_legacy(self) -> None:
        todo = Todo(
            due_date=date(2026, 12, 10),
            remind_days="[0]",
            remind_days_before=7,
        )
        self.assertEqual(parse_offsets(todo), [0])
        self.assertEqual(reminder_times(todo), [datetime(2026, 12, 10, 9, 0)])

    def test_legacy_fallback(self) -> None:
        todo = Todo(
            due_date=date(2026, 12, 10), remind_days=None, remind_days_before=2,
            remind_time="08:00",
        )
        self.assertEqual(reminder_times(todo), [datetime(2026, 12, 8, 8, 0)])

    def test_describe_and_empty(self) -> None:
        self.assertEqual(
            describe(Todo(due_date=date(2026, 12, 10), remind_days="[0, 1, 3]")),
            "提醒 当天/1天/3天",
        )
        self.assertEqual(describe(Todo(due_date=date(2026, 12, 10), remind_days="[]")), "")


class CollectDueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _todo(self, **fields) -> Todo:
        data = {
            "title": "到点待办",
            "due_date": date(2026, 12, 1),
            "due_time": "09:00",
            "remind_days_before": 0,
            "remind_time": "09:00",
        }
        data.update(fields)
        return create(self.session, Todo, **data)

    def test_collect_returns_once(self) -> None:
        self._todo()
        now = datetime(2026, 12, 1, 9, 0)
        first = collect_due(self.session, now)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["title"], "到点待办")
        self.session.commit()

        self.assertEqual(collect_due(self.session, now), [])

    def test_same_session_dedup_without_commit(self) -> None:
        """回归：SessionLocal 关了 autoflush，同会话不 flush 会重复插入同一提醒。"""
        self._todo()
        now = datetime(2026, 12, 1, 9, 0)
        self.assertEqual(len(collect_due(self.session, now)), 1)
        self.assertEqual(collect_due(self.session, now), [])

    def test_not_yet_due_and_stale_are_both_skipped(self) -> None:
        self._todo()
        self.assertEqual(collect_due(self.session, datetime(2026, 12, 1, 8, 59)), [])

        # 超过 12 小时宽限：不补发
        self.assertEqual(collect_due(self.session, datetime(2026, 12, 1, 22, 0)), [])

    def test_completed_or_deleted_are_skipped(self) -> None:
        self._todo(title="已完成", completed=True)
        self._todo(title="已删除", is_deleted=True)
        self.assertEqual(collect_due(self.session, datetime(2026, 12, 1, 9, 0)), [])

    def test_multiple_todos_all_collected(self) -> None:
        self._todo(title="A")
        self._todo(title="B")
        fired = collect_due(self.session, datetime(2026, 12, 1, 9, 0))
        self.assertEqual(sorted(item["title"] for item in fired), ["A", "B"])

    def test_each_offset_fires_once(self) -> None:
        """多选提前天数：每个提醒时刻各触发一次，互不吞并。"""
        self._todo(
            title="多段提醒",
            due_date=date(2026, 12, 2),
            remind_days="[0, 1]",
            remind_days_before=1,
        )
        first = collect_due(self.session, datetime(2026, 12, 1, 9, 0))
        self.assertEqual([item["offset"] for item in first], [1])

        second = collect_due(self.session, datetime(2026, 12, 2, 9, 0))
        self.assertEqual([item["offset"] for item in second], [0])
        self.assertEqual(collect_due(self.session, datetime(2026, 12, 2, 9, 0)), [])


if __name__ == "__main__":
    unittest.main()
