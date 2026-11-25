"""手机端录入规则，以及写后同步不再依赖桌面调度。"""

from __future__ import annotations

import sys
import unittest
from datetime import date

from sqlalchemy import func, select

from app.db.models import Account, Diary, Todo, Transaction
from app.db.repo import create
from app.db.session import session_scope
from app.mobile import records
from app.services import sync

from tests.helpers import reset_db


class WriteHookTests(unittest.TestCase):
    def tearDown(self) -> None:
        sync.set_after_write(None)

    def test_unregistered_write_does_not_import_desktop_scheduler(self) -> None:
        sync.set_after_write(None)
        removed = sys.modules.pop("app.ui.scheduler", None)
        try:
            sync.mark_pending_write()
            self.assertNotIn("app.ui.scheduler", sys.modules)
        finally:
            if removed is not None:
                sys.modules["app.ui.scheduler"] = removed

    def test_registered_callback_runs(self) -> None:
        called: list[str] = []
        sync.set_after_write(lambda: called.append("go"))
        sync.mark_pending_write()
        self.assertEqual(called, ["go"])


class MobileRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        sync.set_after_write(None)

    def tearDown(self) -> None:
        sync.set_after_write(None)

    def _diary_count(self) -> int:
        with session_scope() as session:
            return int(session.scalar(select(func.count()).select_from(Diary)) or 0)

    def test_empty_diary_does_not_create_a_row(self) -> None:
        day = date(2026, 8, 2)
        self.assertEqual(records.load_diary(day)["id"], "")
        self.assertEqual(records.save_diary("   \n", day), "")
        self.assertEqual(self._diary_count(), 0)
        record_id = records.save_diary("今天写了一点", day)
        self.assertTrue(record_id)
        self.assertEqual(self._diary_count(), 1)

    def test_transaction_requires_an_account(self) -> None:
        with self.assertRaises(records.RecordError) as caught:
            records.save_transaction("expense", 12.5, "餐饮", "", date.today(), "")
        self.assertIn("账户", str(caught.exception))
        with session_scope() as session:
            account = create(session, Account, name="现金", kind="现金", initial_balance=0)
            account_id = account.id
        record_id = records.save_transaction(
            "expense", 12.5, "餐饮", account_id, date.today(), "午饭"
        )
        with session_scope() as session:
            row = session.get(Transaction, record_id)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.amount, 12.5)
            self.assertEqual(row.kind, "expense")

    def test_new_todo_skips_quadrant_and_pomodoro(self) -> None:
        record_id = records.create_todo("买菜", date.today(), "高")
        with session_scope() as session:
            row = session.get(Todo, record_id)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.priority, "high")
            self.assertEqual(row.quadrant, "")
            self.assertEqual(row.pomodoro_count, 0)
            self.assertEqual(row.repeat_rule, "none")
