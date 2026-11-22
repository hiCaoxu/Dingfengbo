"""金额整数分存储：落库单位、读取换算、聚合口径与一次性迁移。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.db.migrations import initialize_schema
from app.db.models import Account, Transaction
from app.db.repo import create
from app.db.session import engine, session_scope
from app.services import data_io
from app.services.finance import month_summary

from tests.helpers import reset_db


def _raw_amount() -> object:
    with engine.connect() as connection:
        return connection.exec_driver_sql("SELECT amount FROM transactions").scalar()


class CentsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def _account(self) -> str:
        with session_scope() as session:
            return create(session, Account, name="现金", kind="银行卡", initial_balance=0).id

    def test_stores_integer_cents_and_reads_yuan(self) -> None:
        account_id = self._account()
        with session_scope() as session:
            create(
                session, Transaction, kind="expense", amount=12.34, category="餐饮",
                account_id=account_id, date=date.today(), note="",
            )
        self.assertEqual(_raw_amount(), 1234)
        with session_scope() as session:
            self.assertAlmostEqual(session.scalar(select(Transaction.amount)), 12.34, places=2)

    def test_write_rounds_half_up(self) -> None:
        account_id = self._account()
        with session_scope() as session:
            create(
                session, Transaction, kind="expense", amount=1.005, category="餐饮",
                account_id=account_id, date=date.today(), note="",
            )
        self.assertEqual(_raw_amount(), 101)  # 1.005 → 1.01 元

    def test_sql_sum_is_yuan_and_exact(self) -> None:
        account_id = self._account()
        with session_scope() as session:
            for _ in range(3):
                create(
                    session, Transaction, kind="expense", amount=0.1, category="餐饮",
                    account_id=account_id, date=date.today(), note="",
                )
        with session_scope() as session:
            # 列里是整数分，SUM 由类型装饰器换算回元：0.1 × 3 必须正好 0.3
            self.assertEqual(session.scalar(select(func.sum(Transaction.amount))), 0.3)
            today = date.today()
            self.assertEqual(month_summary(session, today.year, today.month)["expense"], 0.3)

    def test_csv_export_still_writes_yuan(self) -> None:
        account_id = self._account()
        with session_scope() as session:
            create(
                session, Transaction, kind="expense", amount=12.34, category="餐饮",
                account_id=account_id, date=date.today(), note="",
            )
        folder = Path(tempfile.mkdtemp(prefix="dfb-cents-csv-"))
        try:
            path = data_io.export_csv("transactions", folder / "tx.csv")
            body = path.read_text(encoding="utf-8-sig")
            self.assertIn("12.34", body)
            self.assertNotIn("1234", body)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class MoneyMigrationTests(unittest.TestCase):
    """用 PRAGMA user_version 保证历史「元」数据只换算一次。"""

    def setUp(self) -> None:
        reset_db()

    def _insert_legacy_yuan_row(self) -> None:
        """绕过类型装饰器直接写入「元」值，并把库标记成未迁移。"""
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO transactions
                    (id, owner_id, created_at, updated_at, is_deleted, sync_status,
                     sync_seq, kind, amount, category, account_id, date, note,
                     to_account_id)
                VALUES
                    ('legacy-1', 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00',
                     0, 'clean', 0, 'expense', 12.34, '餐饮', 'acc-1', '2026-01-01', '',
                     NULL)
                """
            )
            connection.exec_driver_sql("PRAGMA user_version = 0")

    def test_legacy_yuan_rows_are_converted_once(self) -> None:
        self._insert_legacy_yuan_row()
        initialize_schema(engine)
        self.assertEqual(_raw_amount(), 1234)

        # 再跑一次不能重复乘 100
        initialize_schema(engine)
        self.assertEqual(_raw_amount(), 1234)

    def test_fresh_database_is_already_cents(self) -> None:
        with engine.connect() as connection:
            version = int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        self.assertGreaterEqual(version, 1)
        self.assertIsNone(_raw_amount())


if __name__ == "__main__":
    unittest.main()
