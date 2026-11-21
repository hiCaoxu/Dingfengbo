"""内置节假日数据与补充文件解析。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.services import holidays
from app.services.holiday_data import HOLIDAY_DATES, HOLIDAY_NAMES, MAKEUP_WORKDAYS


class BuiltinHolidayTests(unittest.TestCase):
    def test_2026_totals_match_notice(self) -> None:
        days = {day for day in HOLIDAY_DATES if day.year == 2026}
        # 元旦3 + 春节9 + 清明3 + 劳动5 + 端午3 + 中秋3 + 国庆7
        self.assertEqual(len(days), 33)
        self.assertEqual(len({day for day in MAKEUP_WORKDAYS if day.year == 2026}), 6)

    def test_2027_totals_match_notice(self) -> None:
        days = {day for day in HOLIDAY_DATES if day.year == 2027}
        # 元旦3 + 春节9 + 清明3 + 劳动5 + 端午1 + 中秋1 + 国庆7
        self.assertEqual(len(days), 29)
        self.assertEqual(len({day for day in MAKEUP_WORKDAYS if day.year == 2027}), 5)

    def test_spring_festival_ranges(self) -> None:
        for day in (date(2026, 2, 15), date(2026, 2, 23)):
            self.assertIn(day, HOLIDAY_DATES)
        for day in (date(2027, 2, 5), date(2027, 2, 13)):
            self.assertIn(day, HOLIDAY_DATES)
        self.assertNotIn(date(2026, 2, 14), HOLIDAY_DATES)
        self.assertIn(date(2026, 2, 14), MAKEUP_WORKDAYS)

    def test_national_day_ranges(self) -> None:
        self.assertTrue(
            {date(2026, 10, day) for day in range(1, 8)} <= HOLIDAY_DATES
        )
        self.assertTrue(
            {date(2027, 10, day) for day in range(1, 8)} <= HOLIDAY_DATES
        )

    def test_makeup_workdays_are_not_holidays(self) -> None:
        self.assertFalse(HOLIDAY_DATES & MAKEUP_WORKDAYS)

    def test_weekend_off_respects_makeup_workdays(self) -> None:
        self.assertFalse(holidays.is_weekend_off(date(2026, 2, 14)))  # 周六补班
        self.assertTrue(holidays.is_weekend_off(date(2026, 2, 21)))  # 春节里的周六
        self.assertFalse(holidays.is_weekend_off(date(2026, 2, 16)))  # 周一

    def test_holiday_names(self) -> None:
        self.assertEqual(HOLIDAY_NAMES[date(2026, 10, 1)], "2026 国庆")
        self.assertEqual(HOLIDAY_NAMES[date(2027, 6, 9)], "2027 端午")


class SupplementFileTests(unittest.TestCase):
    def test_parse_holidays_and_makeup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holidays.txt"
            path.write_text(
                "# 注释\n2028-01-01\n!2028-01-02\n坏数据\n", encoding="utf-8"
            )
            self.assertTrue(holidays.is_holiday(date(2028, 1, 1), path))
            self.assertTrue(holidays.is_makeup_workday(date(2028, 1, 2), path))
            self.assertFalse(holidays.is_holiday(date(2028, 1, 3), path))
            # 2028-01-02 是周日，但属于调休上班日
            self.assertFalse(holidays.is_weekend_off(date(2028, 1, 2), path))

    def test_missing_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.txt"
            self.assertFalse(holidays.is_holiday(date(2028, 1, 1), path))
            self.assertEqual(holidays.holiday_dates(path), set(HOLIDAY_DATES))

    def test_sample_file_created_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "holidays.txt"
            holidays.ensure_sample_file(path)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("!2028-01-02", content)

            path.write_text("2029-01-01\n", encoding="utf-8")
            holidays.ensure_sample_file(path)  # 已存在则不覆盖
            self.assertEqual(path.read_text(encoding="utf-8"), "2029-01-01\n")


if __name__ == "__main__":
    unittest.main()
