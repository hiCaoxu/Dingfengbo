"""CSV 日期解析：日/月 与 月/日 同时成立时必须报错，不能静默选一个。"""

from __future__ import annotations

import unittest
from datetime import date

from app.db.models import Transaction
from app.services.data_io import _coerce_date, _prepare_row


class DateCoercionTests(unittest.TestCase):
    def test_unambiguous_formats_are_accepted(self) -> None:
        self.assertEqual(_coerce_date("2026-05-06"), date(2026, 5, 6))
        self.assertEqual(_coerce_date("2026/5/6"), date(2026, 5, 6))
        self.assertEqual(_coerce_date("2026.05.06"), date(2026, 5, 6))
        self.assertEqual(_coerce_date("2026年5月6日"), date(2026, 5, 6))

    def test_slash_date_with_only_one_valid_reading(self) -> None:
        # 22 不可能是月份，只可能是「月/日」
        self.assertEqual(_coerce_date("11/22/2026"), date(2026, 11, 22))
        # 25 不可能是月份，只可能是「日/月」
        self.assertEqual(_coerce_date("25/12/2026"), date(2026, 12, 25))

    def test_ambiguous_slash_date_is_rejected(self) -> None:
        # 05/06 可以读成 5 月 6 日，也可以读成 6 月 5 日
        self.assertIsNone(_coerce_date("05/06/2026"))
        self.assertIsNone(_coerce_date("01/02/2026"))

    def test_garbage_is_rejected(self) -> None:
        self.assertIsNone(_coerce_date("not-a-date"))
        self.assertIsNone(_coerce_date(""))


class PrepareRowTests(unittest.TestCase):
    def test_ambiguous_date_marks_the_column(self) -> None:
        values, bad = _prepare_row(Transaction, {
            "id": "tx-1",
            "date": "05/06/2026",
            "amount": "12.5",
            "kind": "expense",
        })
        self.assertIn("date", bad, "有歧义的日期必须报错，而不是猜一个")
        self.assertNotIn("date", values)

    def test_valid_row_has_no_bad_columns(self) -> None:
        values, bad = _prepare_row(Transaction, {
            "id": "tx-2",
            "date": "2026-05-06",
            "amount": "12.5",
            "kind": "expense",
        })
        self.assertEqual(bad, [])
        self.assertEqual(values["date"], date(2026, 5, 6))


if __name__ == "__main__":
    unittest.main()
