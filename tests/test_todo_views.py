"""任务视图：月历网格、四象限分类、计数与模块合并后的导航。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest import mock

from app.db.models import Todo
from app.db.repo import create
from app.db.session import session_scope
from app.services.todo_views import (
    AUTO_LABEL,
    LABEL_TO_QUADRANT,
    QUADRANT_KEYS,
    QUADRANT_LABELS,
    counts_by_date,
    group_by_quadrant,
    month_grid,
    quadrant_counts,
    quadrant_of,
    shift_month,
)

from tests.helpers import reset_db


class MonthGridTests(unittest.TestCase):
    def test_november_2026_starts_monday_and_covers_month(self) -> None:
        grid = month_grid(2026, 11)
        self.assertEqual(grid[0][0], date(2026, 10, 26))  # 周一
        self.assertTrue(all(len(week) == 7 for week in grid))
        self.assertEqual(len(grid), 6)
        days = [day for week in grid for day in week]
        self.assertEqual(days, sorted(days))
        self.assertTrue(all((days[i + 1] - days[i]).days == 1 for i in range(len(days) - 1)))
        self.assertIn(date(2026, 11, 30), days)
        self.assertNotIn(date(2026, 12, 7), days)

    def test_february_2026_has_five_weeks(self) -> None:
        grid = month_grid(2026, 2)
        self.assertEqual(len(grid), 5)
        self.assertIn(date(2026, 2, 28), [day for week in grid for day in week])

    def test_leap_february(self) -> None:
        grid = month_grid(2028, 2)
        self.assertIn(date(2028, 2, 29), [day for week in grid for day in week])

    def test_shift_month(self) -> None:
        self.assertEqual(shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(shift_month(2026, 6, 0), (2026, 6))
        self.assertEqual(shift_month(2026, 6, 13), (2027, 7))


class QuadrantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 11, 21)

    def todo(self, **kwargs) -> dict:
        data = {"priority": "", "due_date": None, "quadrant": ""}
        data.update(kwargs)
        return data

    def test_auto_rules(self) -> None:
        self.assertEqual(quadrant_of(self.todo(), self.today), "q4")
        self.assertEqual(
            quadrant_of(self.todo(priority="high", due_date=self.today), self.today), "q1"
        )
        self.assertEqual(
            quadrant_of(self.todo(priority="high", due_date=self.today + timedelta(days=5)),
                        self.today),
            "q2",
        )
        self.assertEqual(
            quadrant_of(self.todo(due_date=self.today - timedelta(days=1)), self.today), "q3"
        )
        self.assertEqual(
            quadrant_of(self.todo(due_date=self.today + timedelta(days=1)), self.today), "q3"
        )
        self.assertEqual(
            quadrant_of(self.todo(due_date=self.today + timedelta(days=2)), self.today), "q4"
        )
        # 中优先级也算「重要」，避免中等任务全部掉进「不重要」桶
        self.assertEqual(
            quadrant_of(self.todo(priority="medium", due_date=self.today), self.today), "q1"
        )
        self.assertEqual(
            quadrant_of(self.todo(priority="medium",
                                  due_date=self.today + timedelta(days=10)), self.today),
            "q2",
        )

    def test_manual_override_wins(self) -> None:
        item = self.todo(priority="high", due_date=self.today, quadrant="q4")
        self.assertEqual(quadrant_of(item, self.today), "q4")
        # 手选的非法值退回自动推导
        item["quadrant"] = "q9"
        self.assertEqual(quadrant_of(item, self.today), "q1")

    def test_label_mapping_round_trip(self) -> None:
        for key, label in QUADRANT_LABELS.items():
            self.assertIn(key, QUADRANT_KEYS)
            self.assertEqual(LABEL_TO_QUADRANT[label], key)
        self.assertNotIn(AUTO_LABEL, LABEL_TO_QUADRANT)


class CountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 11, 21)
        self.items = [
            self.todo(id="a", due_date=self.today, priority="high"),
            self.todo(id="b", due_date=self.today),
            self.todo(id="c", due_date=self.today, completed=True),
            self.todo(id="d", due_date=self.today + timedelta(days=1)),
            self.todo(id="e", due_date=None),
        ]

    def todo(self, **kwargs) -> dict:
        data = {
            "id": "", "priority": "", "due_date": None, "quadrant": "",
            "completed": False, "is_template": False,
        }
        data.update(kwargs)
        return data

    def test_counts_by_date_skips_done_and_undated(self) -> None:
        counts = counts_by_date(self.items)
        self.assertEqual(counts[self.today], 2)
        self.assertEqual(counts[self.today + timedelta(days=1)], 1)
        self.assertNotIn(None, counts)

    def test_quadrant_counts_and_grouping(self) -> None:
        counts = quadrant_counts(self.items, self.today)
        self.assertEqual(sum(counts.values()), 4)
        self.assertEqual(counts["q1"], 1)
        self.assertEqual(counts["q3"], 2)
        self.assertEqual(counts["q4"], 1)
        grouped = group_by_quadrant(self.items, self.today)
        self.assertEqual([item["id"] for item in grouped["q3"]], ["b", "d"])


class TodoModelTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_quadrant_defaults_to_auto(self) -> None:
        with session_scope() as session:
            todo = create(session, Todo, title="新任务")
            self.assertEqual(todo.quadrant, "")

    def test_quadrant_persists(self) -> None:
        with session_scope() as session:
            todo = create(session, Todo, title="重要任务", priority="high", quadrant="q2")
            todo_id = todo.id
        with session_scope() as session:
            self.assertEqual(session.get(Todo, todo_id).quadrant, "q2")


class ModuleMergeTests(unittest.TestCase):
    def test_habits_merged_into_todo_module(self) -> None:
        from app.main import MODULE_LABELS, MODULES

        self.assertNotIn("habits", MODULE_LABELS)
        self.assertEqual(MODULE_LABELS["todo"], "任务")
        self.assertEqual(len(MODULES), 10)

    def test_module_list_filters_hidden_and_legacy_habits(self) -> None:
        from app.main import _module_list

        settings = {"module_order": ["todo", "habits", "home"], "hidden_modules": ["notes"]}
        with mock.patch("app.main.load_settings", return_value=settings):
            modules = [key for key, _label in _module_list()]
        self.assertEqual(modules[:2], ["todo", "home"])
        self.assertNotIn("habits", modules)
        self.assertNotIn("notes", modules)

    def test_all_hidden_falls_back_to_home(self) -> None:
        from app.main import MODULES, _module_list

        settings = {
            "module_order": [key for key, _label in MODULES],
            "hidden_modules": [key for key, _label in MODULES],
        }
        with mock.patch("app.main.load_settings", return_value=settings):
            self.assertEqual(_module_list(), [("home", "首页")])


if __name__ == "__main__":
    unittest.main()
