"""一句话记账解析：年份不能当金额、负号表示收入、口语「N块M」。"""

from __future__ import annotations

import unittest

from app.services.expense_rules import parse_amount, parse_transaction


class ParseAmountTests(unittest.TestCase):
    def test_plain_yuan(self) -> None:
        self.assertEqual(parse_amount("今天午饭花了 32 元"), 32.0)
        self.assertEqual(parse_amount("¥ 5.5"), 5.5)

    def test_colloquial_block_fraction(self) -> None:
        self.assertEqual(parse_amount("买水 10块5"), 10.5)
        self.assertEqual(parse_amount("买水 10块5毛"), 10.5)
        self.assertEqual(parse_amount("花了 205 块"), 205.0)

    def test_yuan_and_jiao_use_the_same_scale(self) -> None:
        self.assertEqual(parse_amount("午饭 12元3角"), 12.3)
        self.assertEqual(parse_amount("午饭 10元5角"), 10.5)
        self.assertEqual(parse_amount("午饭 12元3毛"), 12.3)
        self.assertEqual(parse_amount("找零 5角"), 0.5)
        self.assertEqual(parse_amount("找零 5毛"), 0.5)
        self.assertEqual(parse_amount("午饭 12元3角5分"), 12.35)
        self.assertAlmostEqual(parse_amount("12.3元"), 12.3)

    def test_unit_multipliers(self) -> None:
        self.assertEqual(parse_amount("收入 10万"), 100_000.0)
        self.assertEqual(parse_amount("奖金 3千"), 3_000.0)
        self.assertEqual(parse_amount("工资 1.5 万元"), 15_000.0)

    def test_year_is_not_an_amount(self) -> None:
        """「2025年收入10万」不能把年份 2025 当金额。"""
        self.assertEqual(parse_amount("2025年收入10万"), 100_000.0)

    def test_year_only_returns_none(self) -> None:
        self.assertIsNone(parse_amount("2025年做的事"))

    def test_negative_sign_is_kept(self) -> None:
        self.assertEqual(parse_amount("退款 -32 元"), -32.0)


class ParseTransactionTests(unittest.TestCase):
    def test_expense(self) -> None:
        draft = parse_transaction("今天午饭花了 32 元")
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["amount"], 32.0)
        self.assertEqual(draft["category"], "餐饮")

    def test_income_by_word(self) -> None:
        draft = parse_transaction("工资到账 8000 元")
        self.assertEqual(draft["kind"], "income")
        self.assertEqual(draft["amount"], 8000.0)

    def test_negative_means_income(self) -> None:
        draft = parse_transaction("退款 -32 元")
        self.assertEqual(draft["kind"], "income")
        self.assertEqual(draft["amount"], 32.0)

    def test_year_and_wan(self) -> None:
        draft = parse_transaction("2025年收入10万")
        self.assertEqual(draft["amount"], 100_000.0)
        self.assertEqual(draft["kind"], "income")

    def test_missing_amount_returns_none(self) -> None:
        self.assertIsNone(parse_transaction("今天心情不错"))


if __name__ == "__main__":
    unittest.main()
