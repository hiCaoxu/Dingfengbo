"""金额工具：Decimal 累加、按分比较、HALF_UP 量化与格式化。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.services.money import (
    format_money,
    is_over,
    percent,
    quantize,
    to_decimal,
    to_float,
    total,
)


class DecimalMathTests(unittest.TestCase):
    def test_float_input_is_read_through_repr(self) -> None:
        self.assertEqual(to_decimal(0.1), Decimal("0.1"))

    def test_accumulating_cents_is_exact(self) -> None:
        """0.1 累加 10 次在二进制浮点下是 0.9999999999999999，按分累加必须是 1.00。"""
        self.assertEqual(total([0.1] * 10), Decimal("1.00"))

    def test_is_over_is_cent_accurate(self) -> None:
        self.assertFalse(is_over(1.10 + 2.20, 3.30))
        self.assertTrue(is_over(3.31, 3.30))

    def test_quantize_rounds_half_up(self) -> None:
        self.assertEqual(quantize(1.005), Decimal("1.01"))
        self.assertEqual(quantize(2.675), Decimal("2.68"))
        self.assertEqual(quantize(-1.005), Decimal("-1.01"))

    def test_percent_rounds_to_integer(self) -> None:
        self.assertEqual(percent(1, 3), 33)
        self.assertEqual(percent(2, 3), 67)
        self.assertEqual(percent(0, 0), 0)

    def test_format_money(self) -> None:
        self.assertEqual(format_money(1234.5), "¥1,234.50")
        self.assertEqual(format_money(0), "¥0.00")
        self.assertEqual(format_money(2.675), "¥2.68")

    def test_to_float_quantizes(self) -> None:
        self.assertEqual(to_float(1.005), 1.01)

    def test_invalid_input_is_zero(self) -> None:
        self.assertEqual(to_decimal("abc"), Decimal("0"))
        self.assertEqual(to_decimal(None), Decimal("0"))


if __name__ == "__main__":
    unittest.main()
