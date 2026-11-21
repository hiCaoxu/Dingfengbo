"""自定义重复规则：天/周/月/年、间隔、跳过双休日/节假日与旧配置兼容。"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from app.services import recurrence
from app.services.dates import nth_workday, parse_hm, valid_hm
from app.services.recurrence import describe, next_custom_due, normalize_config
from app.services.todo_rules import next_occurrence, parse_config, serialize_config


class DayRuleTests(unittest.TestCase):
    def test_interval_days(self) -> None:
        self.assertEqual(
            next_custom_due(date(2026, 11, 21), {"unit": "day", "interval": 3}),
            date(2026, 11, 24),
        )

    def test_skip_weekends(self) -> None:
        # 2026-11-20 是周五，+1 天是周六 -> 顺延到周一
        self.assertEqual(
            next_custom_due(
                date(2026, 11, 20),
                {"unit": "day", "interval": 1, "skip_weekends": True},
            ),
            date(2026, 11, 23),
        )

    def test_skip_holidays(self) -> None:
        holiday = date(2026, 11, 23)
        with mock.patch.object(recurrence, "is_holiday", lambda day: day == holiday):
            self.assertEqual(
                next_custom_due(
                    date(2026, 11, 20),
                    {"unit": "day", "interval": 1, "skip_weekends": True,
                     "skip_holidays": True},
                ),
                date(2026, 11, 24),
            )


class WeekRuleTests(unittest.TestCase):
    def test_single_weekday(self) -> None:
        # 2026-11-21 是周六，选周六 -> 下一次是 11-28
        self.assertEqual(
            next_custom_due(date(2026, 11, 21), {"unit": "week", "weekdays": [5]}),
            date(2026, 11, 28),
        )

    def test_multiple_weekdays_picks_nearest(self) -> None:
        self.assertEqual(
            next_custom_due(
                date(2026, 11, 21), {"unit": "week", "weekdays": [0, 2, 5]}
            ),
            date(2026, 11, 23),
        )

    def test_interval_weeks(self) -> None:
        self.assertEqual(
            next_custom_due(
                date(2026, 11, 21), {"unit": "week", "interval": 2, "weekdays": [5]}
            ),
            date(2026, 12, 5),
        )

    def test_no_weekday_falls_back_to_current(self) -> None:
        self.assertEqual(
            next_custom_due(date(2026, 11, 21), {"unit": "week"}),
            date(2026, 11, 28),
        )


class MonthRuleTests(unittest.TestCase):
    def test_specific_days(self) -> None:
        config = {"unit": "month", "month_mode": "day", "month_days": [1, 15]}
        self.assertEqual(next_custom_due(date(2026, 11, 10), config), date(2026, 11, 15))
        self.assertEqual(next_custom_due(date(2026, 11, 20), config), date(2026, 12, 1))

    def test_last_day(self) -> None:
        config = {"unit": "month", "month_mode": "day", "month_last_day": True}
        self.assertEqual(next_custom_due(date(2026, 11, 30), config), date(2026, 12, 31))

    def test_first_monday(self) -> None:
        config = {
            "unit": "month", "month_mode": "weekday",
            "month_nth": "first", "month_weekday": 0,
        }
        # 2026-12-07 是 12 月第一个周一
        self.assertEqual(next_custom_due(date(2026, 11, 15), config), date(2026, 12, 7))

    def test_last_workday(self) -> None:
        config = {"unit": "month", "month_mode": "workday", "month_nth": "last"}
        # 月中还没到月末：取本月最后一个工作日
        self.assertEqual(next_custom_due(date(2026, 11, 15), config), date(2026, 11, 30))
        # 已过本月最后一天：取下一个月的最后一个工作日（2027-01-31 是周日 -> 01-29）
        self.assertEqual(next_custom_due(date(2027, 1, 31), config), date(2027, 2, 26))
        # 2026-01-31 是周六 -> 当月最后一个工作日 01-30
        self.assertEqual(next_custom_due(date(2026, 1, 30), config), date(2026, 2, 27))

    def test_interval_months(self) -> None:
        config = {"unit": "month", "interval": 2, "month_mode": "day", "month_days": [1]}
        self.assertEqual(next_custom_due(date(2026, 11, 2), config), date(2027, 1, 1))

    def test_nth_workday_helper(self) -> None:
        self.assertEqual(nth_workday(2026, 11, "first"), date(2026, 11, 2))
        self.assertEqual(nth_workday(2026, 11, "last"), date(2026, 11, 30))


class YearRuleTests(unittest.TestCase):
    def test_date_mode(self) -> None:
        config = {"unit": "year", "year_mode": "date", "year_month": 11, "year_day": 30}
        self.assertEqual(next_custom_due(date(2026, 1, 1), config), date(2026, 11, 30))
        self.assertEqual(next_custom_due(date(2026, 11, 30), config), date(2027, 11, 30))

    def test_weekday_mode(self) -> None:
        config = {
            "unit": "year", "year_mode": "weekday", "year_month": 11,
            "year_nth": "first", "year_weekday": 0,
        }
        # 2026-11-02 是 11 月第一个周一
        self.assertEqual(next_custom_due(date(2026, 1, 1), config), date(2026, 11, 2))

    def test_invalid_values_fall_back(self) -> None:
        config = {"unit": "year", "year_mode": "date", "year_month": 99, "year_day": 99}
        result = next_custom_due(date(2026, 1, 1), config)
        self.assertIsNotNone(result)


class LegacyConfigTests(unittest.TestCase):
    def test_legacy_weekly_times(self) -> None:
        config = normalize_config({"kind": "weekly_times", "times": 3})
        self.assertEqual(config["unit"], "day")
        self.assertEqual(config["interval"], 2)
        self.assertEqual(
            next_custom_due(date(2026, 11, 21), {"kind": "weekly_times", "times": 3}),
            date(2026, 11, 23),
        )

    def test_legacy_workday_kinds(self) -> None:
        self.assertEqual(
            next_custom_due(date(2026, 11, 15), {"kind": "monthly_first_workday"}),
            date(2026, 12, 1),
        )
        # 旧配置等价于「每月最后一个工作日」：月中出发取本月月末
        self.assertEqual(
            next_custom_due(date(2026, 11, 15), {"kind": "monthly_last_workday"}),
            date(2026, 11, 30),
        )

    def test_legacy_monthly_days(self) -> None:
        self.assertEqual(
            next_custom_due(date(2026, 11, 20), {"kind": "monthly_days", "days": [1, 15]}),
            date(2026, 12, 1),
        )

    def test_irregular_does_not_advance(self) -> None:
        self.assertIsNone(next_occurrence(date(2026, 11, 21), "irregular", "", False))

    def test_simple_rules_still_work(self) -> None:
        self.assertEqual(
            next_occurrence(date(2026, 11, 21), "week", "", False), date(2026, 11, 28)
        )
        self.assertEqual(
            next_occurrence(date(2026, 11, 21), "day", "", False), date(2026, 11, 22)
        )


class DescribeTests(unittest.TestCase):
    def test_day_week_month_year(self) -> None:
        self.assertEqual(describe({"unit": "day", "interval": 2}), "每 2 天")
        self.assertEqual(
            describe({"unit": "week", "weekdays": [0, 5]}), "每 1 周 · 周一、周六"
        )
        self.assertEqual(
            describe({"unit": "month", "month_mode": "workday", "month_nth": "last"}),
            "每 1 月 · 最后一个工作日",
        )
        self.assertEqual(
            describe({"unit": "year", "year_mode": "date", "year_month": 11, "year_day": 30}),
            "每 1 年 · 11月30日",
        )

    def test_skip_flags_and_legacy(self) -> None:
        self.assertIn(
            "跳过双休日", describe({"unit": "day", "interval": 1, "skip_weekends": True})
        )
        self.assertEqual(describe({"kind": "weekly_times", "times": 3}), "每周 3 次")
        self.assertEqual(describe({}), "自定义")

    def test_config_parse_is_defensive(self) -> None:
        self.assertEqual(parse_config(""), {})
        self.assertEqual(parse_config("not json"), {})
        self.assertEqual(parse_config("[1,2]"), {})
        self.assertEqual(serialize_config(None), "")


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
