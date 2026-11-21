"""待办自定义循环规则：推进日期与展示文案。"""

from __future__ import annotations

import unittest
from datetime import date

from app.services.dates import next_custom_due, nth_workday, parse_hm, valid_hm
from app.services.todo_rules import (
    describe,
    next_occurrence,
    parse_config,
    serialize_config,
)


class CustomRuleTests(unittest.TestCase):
    def test_weekly_times_spreads_evenly(self) -> None:
        value = date(2026, 11, 21)  # 周六
        self.assertEqual(
            next_custom_due(value, {"kind": "weekly_times", "times": 3}),
            date(2026, 11, 23),
        )
        self.assertEqual(
            next_custom_due(value, {"kind": "weekly_times", "times": 7}),
            date(2026, 11, 22),
        )
        self.assertEqual(
            next_custom_due(value, {"kind": "weekly_times", "times": 1}),
            date(2026, 11, 28),
        )

    def test_weekly_times_clamps_out_of_range_input(self) -> None:
        value = date(2026, 11, 21)
        self.assertEqual(
            next_custom_due(value, {"kind": "weekly_times", "times": 99}),
            date(2026, 11, 22),
        )
        self.assertEqual(
            next_custom_due(value, {"kind": "weekly_times", "times": 0}),
            date(2026, 11, 28),
        )

    def test_monthly_first_workday(self) -> None:
        self.assertEqual(
            next_custom_due(date(2026, 11, 15), {"kind": "monthly_workday", "nth": "first"}),
            date(2026, 12, 1),
        )

    def test_monthly_last_workday_skips_weekend(self) -> None:
        # 2026-01-31 是周六，最后一个工作日应为 01-30（周五）
        self.assertEqual(
            next_custom_due(date(2025, 12, 15), {"kind": "monthly_workday", "nth": "last"}),
            date(2026, 1, 30),
        )
        self.assertEqual(
            next_custom_due(date(2026, 11, 15), {"kind": "monthly_workday", "nth": "last"}),
            date(2026, 12, 31),
        )

    def test_monthly_days_picks_next_occurrence(self) -> None:
        config = {"kind": "monthly_days", "days": [1, 15]}
        self.assertEqual(next_custom_due(date(2026, 11, 10), config), date(2026, 11, 15))
        self.assertEqual(next_custom_due(date(2026, 11, 20), config), date(2026, 12, 1))

    def test_nth_workday_helper(self) -> None:
        self.assertEqual(nth_workday(2026, 11, "first"), date(2026, 11, 2))
        self.assertEqual(nth_workday(2026, 11, "last"), date(2026, 11, 30))


class RuleDispatchTests(unittest.TestCase):
    def test_legacy_irregular_does_not_advance(self) -> None:
        self.assertIsNone(next_occurrence(date(2026, 11, 21), "irregular", "", False))

    def test_custom_kind_aliases_from_ui(self) -> None:
        config = serialize_config({"kind": "monthly_first_workday"})
        self.assertEqual(
            next_occurrence(date(2026, 11, 15), "custom", config, False),
            date(2026, 12, 1),
        )
        config = serialize_config({"kind": "monthly_last_workday"})
        self.assertEqual(
            next_occurrence(date(2026, 11, 15), "custom", config, False),
            date(2026, 12, 31),
        )

    def test_simple_rules_still_work(self) -> None:
        self.assertEqual(
            next_occurrence(date(2026, 11, 21), "week", "", False), date(2026, 11, 28)
        )
        self.assertEqual(
            next_occurrence(date(2026, 11, 21), "day", "", False), date(2026, 11, 22)
        )

    def test_describe_matches_ui_labels(self) -> None:
        self.assertEqual(
            describe("custom", serialize_config({"kind": "weekly_times", "times": 3})),
            "每周 3 次",
        )
        self.assertEqual(
            describe("custom", serialize_config({"kind": "monthly_last_workday"})),
            "每月最后一个工作日",
        )
        self.assertEqual(describe("week", ""), "按周")
        self.assertEqual(describe("irregular", ""), "自定义")

    def test_config_parse_is_defensive(self) -> None:
        self.assertEqual(parse_config(""), {})
        self.assertEqual(parse_config("not json"), {})
        self.assertEqual(parse_config("[1,2]"), {})


class TimeValidationTests(unittest.TestCase):
    def test_valid_hm(self) -> None:
        self.assertTrue(valid_hm("00:00"))
        self.assertTrue(valid_hm("23:59"))
        self.assertFalse(valid_hm("24:00"))
        self.assertFalse(valid_hm("9:30"))
        self.assertFalse(valid_hm(""))

    def test_parse_hm(self) -> None:
        self.assertEqual(parse_hm("09:30").hour, 9)
        self.assertIsNone(parse_hm("9:30"))


if __name__ == "__main__":
    unittest.main()
