"""预算/周期记账与习惯打卡的纯逻辑与落库行为。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models import RecurringRule, Transaction
from app.db.repo import create
from app.db.session import session_scope
from app.services import budget as budget_service
from app.services import habits as habit_service
from app.services.expense_rules import parse_amount, parse_transaction, suggest_category

from tests.helpers import reset_db, seed


class ExpenseRuleTests(unittest.TestCase):
    def test_parse_chinese_amount(self) -> None:
        self.assertEqual(parse_amount("午饭花了 32 元"), 32.0)
        self.assertEqual(parse_amount("地铁 3块"), 3.0)
        self.assertIsNone(parse_amount("没有数字"))

    def test_suggest_category(self) -> None:
        self.assertEqual(suggest_category("全家便利店"), "购物")
        self.assertEqual(suggest_category("地铁充值"), "交通")
        self.assertEqual(suggest_category("拿铁咖啡"), "餐饮")

    def test_parse_transaction_expense(self) -> None:
        draft = parse_transaction("今天午饭花了 32 元", today=date(2026, 11, 21))
        self.assertIsNotNone(draft)
        self.assertEqual(draft["kind"], "expense")
        self.assertEqual(draft["category"], "餐饮")
        self.assertEqual(draft["amount"], 32.0)
        self.assertEqual(draft["date"], "2026-11-21")

    def test_parse_transaction_income(self) -> None:
        draft = parse_transaction("9月工资 12000 元", today=date(2026, 11, 21))
        self.assertEqual(draft["kind"], "income")
        self.assertEqual(draft["category"], "工资")

    def test_parse_transaction_needs_amount(self) -> None:
        self.assertIsNone(parse_transaction("今天天气不错"))


class BudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()

    def test_overview_reads_total_and_categories(self) -> None:
        data = budget_service.overview(date.today())
        self.assertEqual(data["total_budget"], 3000)
        self.assertEqual(data["expense"], 32.5)
        self.assertEqual(data["categories"][0]["category"], "餐饮")
        self.assertEqual(data["categories"][0]["used"], 32.5)

    def test_upsert_updates_existing_row(self) -> None:
        month = budget_service.month_key()
        budget_service.upsert_budget(month, "餐饮", 1500)
        rows = [row for row in budget_service.budgets(month) if row.category == "餐饮"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0].amount), 1500)

    def test_zero_amount_removes_budget(self) -> None:
        month = budget_service.month_key()
        budget_service.upsert_budget(month, "餐饮", 0)
        rows = [row for row in budget_service.budgets(month) if row.category == "餐饮"]
        self.assertEqual(rows, [])

    def test_alerts_for_over_budget(self) -> None:
        month = budget_service.month_key()
        budget_service.upsert_budget(month, "", 10)
        alerts = budget_service.alerts(date.today())
        self.assertTrue(any("超支" in message for message in alerts))


class RecurringTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def _rule(self, next_date: date, period: str = "month", day: int = 1) -> str:
        with session_scope() as session:
            rule = create(
                session,
                RecurringRule,
                name="房租",
                kind="expense",
                amount=2500,
                category="居住",
                account_id=self.ids["account_id"],
                period=period,
                day=day,
                next_date=next_date,
                active=True,
            )
            return rule.id

    def test_generate_due_creates_transactions_and_advances(self) -> None:
        today = date(2026, 11, 21)
        self._rule(date(2026, 10, 1))
        created = budget_service.generate_due(today)
        self.assertEqual(created, 2)  # 10-01、11-01 两期
        with session_scope() as session:
            total = session.scalar(
                select(func.count()).select_from(Transaction).where(
                    Transaction.note.like("[周期]%")
                )
            )
            rule = session.scalar(select(RecurringRule).where(RecurringRule.name == "房租"))
            self.assertEqual(int(total), 2)
            self.assertGreater(rule.next_date, today)

    def test_generate_due_is_idempotent(self) -> None:
        today = date(2026, 11, 21)
        self._rule(date(2026, 11, 1))
        first = budget_service.generate_due(today)
        second = budget_service.generate_due(today)
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)

    def test_next_occurrence_clamps_month_end(self) -> None:
        with session_scope() as session:
            rule = create(
                session, RecurringRule, name="月末", amount=1, period="month", day=31,
                next_date=date(2026, 1, 31),
            )
            following = budget_service.next_occurrence(rule, date(2026, 1, 31))
        self.assertEqual(following, date(2026, 2, 28))

    def test_upcoming_lists_rules_in_range(self) -> None:
        today = date(2026, 11, 21)
        self._rule(today + timedelta(days=3))
        upcoming = budget_service.upcoming(7, today)
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0]["name"], "房租")


class HabitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def test_streak_counts_consecutive_days(self) -> None:
        today = date.today()
        days = {today, today - timedelta(days=1), today - timedelta(days=3)}
        self.assertEqual(habit_service.streak(days, today), 2)
        self.assertEqual(habit_service.longest_streak(days), 2)

    def test_streak_falls_back_to_yesterday(self) -> None:
        today = date.today()
        days = {today - timedelta(days=1), today - timedelta(days=2)}
        self.assertEqual(habit_service.streak(days, today), 2)

    def test_week_count_uses_calendar_week(self) -> None:
        today = date.today()
        start = today - timedelta(days=today.weekday())
        self.assertEqual(habit_service.week_count({start, today}, today), 2)

    def test_check_in_is_idempotent(self) -> None:
        habit_id = self.ids["habit_id"]
        first = habit_service.check_in(habit_id, date.today(), True)
        second = habit_service.check_in(habit_id, date.today(), True)
        self.assertEqual(first, second)
        overview = {item["id"]: item for item in habit_service.overview()}
        self.assertTrue(overview[habit_id]["done_today"])

    def test_uncheck_and_calendar(self) -> None:
        habit_id = self.ids["habit_id"]
        habit_service.check_in(habit_id, date.today(), False)
        overview = {item["id"]: item for item in habit_service.overview()}
        self.assertFalse(overview[habit_id]["done_today"])
        columns = habit_service.calendar(habit_id, weeks=4)
        self.assertEqual(len(columns), 4)
        self.assertTrue(all(len(column) == 7 for column in columns))

    def test_pending_today_excludes_checked(self) -> None:
        pending = habit_service.pending_today()
        self.assertNotIn("阅读", pending)


if __name__ == "__main__":
    unittest.main()
