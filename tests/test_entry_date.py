"""手工录入日期：缺少年份时用当年，并接受常见的短写。"""

from __future__ import annotations

import unittest
from datetime import date

from app.services.dates import parse_entry_date
from app.ui.forms import _date


class EntryDateTests(unittest.TestCase):
    def test_month_day_uses_the_given_year(self) -> None:
        today = date(2026, 3, 1)
        self.assertEqual(parse_entry_date("11-28", today=today), date(2026, 11, 28))
        self.assertEqual(parse_entry_date("11/28", today=today), date(2026, 11, 28))
        self.assertEqual(parse_entry_date("1128", today=today), date(2026, 11, 28))
        self.assertEqual(parse_entry_date("3-1", today=today), date(2026, 3, 1))

    def test_listed_formats(self) -> None:
        expected = date(2026, 11, 28)
        for text in (
            "20261128",
            "261128",
            "2026/11/28",
            "26/11/28",
            "11/28",
            "2026-11-28",
            "26-11-28",
            "11-28",
            "2026-11-28",
        ):
            self.assertEqual(parse_entry_date(text, today=date(2026, 6, 1)), expected, text)

    def test_two_digit_year_pivot(self) -> None:
        self.assertEqual(parse_entry_date("900101"), date(1990, 1, 1))
        self.assertEqual(parse_entry_date("000101"), date(2000, 1, 1))
        self.assertEqual(parse_entry_date("691231"), date(2069, 12, 31))
        self.assertEqual(parse_entry_date("700101"), date(1970, 1, 1))

    def test_leap_day_follows_the_year(self) -> None:
        self.assertEqual(parse_entry_date("0229", today=date(2024, 1, 1)), date(2024, 2, 29))
        with self.assertRaises(ValueError):
            parse_entry_date("0229", today=date(2026, 1, 1))

    def test_garbage_and_impossible_dates_fail(self) -> None:
        for text in ("不是日期", "2026112", "13-40", "2026-02-30", "2026-11/28"):
            with self.assertRaises(ValueError, msg=text):
                parse_entry_date(text, today=date(2026, 6, 1))

    def test_form_helper_accepts_short_dates(self) -> None:
        self.assertEqual(_date("1128"), date(date.today().year, 11, 28))
        self.assertIsNone(_date(""))
        self.assertEqual(_date("今天"), date.today())


if __name__ == "__main__":
    unittest.main()
