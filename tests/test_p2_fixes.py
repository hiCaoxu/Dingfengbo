"""P2 修补：账户幽灵键、报告预算回落、日记检索转义、导出原子写、备份路径编码、状态语义。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db.backup import verify_backup
from app.db.models import Account, Diary, Transaction
from app.db.repo import create, soft_delete
from app.db.session import session_scope
from app.services import data_io
from app.services.finance import account_balances
from app.services.reports import _budget_section
from app.ui import diary as diary_ui
from app.ui.state import state

from tests.helpers import reset_db


class GhostAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_deleted_account_produces_no_ghost_balance(self) -> None:
        with session_scope() as session:
            account = create(session, Account, name="旧卡", kind="银行卡",
                             initial_balance=100.0)
            account_id = account.id
            create(session, Transaction, kind="expense", amount=10.0, category="餐饮",
                   account_id=account_id, date=date.today(), note="")
        with session_scope() as session:
            soft_delete(session, session.get(Account, account_id))
        with session_scope() as session:
            balances = account_balances(session)
        self.assertEqual(balances, {}, "已删除账户不应留下余额键")

    def test_orphan_transaction_is_ignored(self) -> None:
        with session_scope() as session:
            create(session, Transaction, kind="expense", amount=5.0, category="餐饮",
                   account_id="no-such-account", date=date.today(), note="")
        with session_scope() as session:
            self.assertEqual(account_balances(session), {})


class ReportBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_standing_budget_does_not_drive_monthly_remaining(self) -> None:
        from app.db.models import Budget

        today = date.today()
        with session_scope() as session:
            create(session, Transaction, kind="expense", amount=100.0, category="餐饮",
                   account_id="acc", date=today, note="")
            # 只设「长期」总预算（month=""），本月没设。注意 upsert_budget 会把空月份
            # 归一到当月，这里必须直接建行才能表达「长期」。
            create(session, Budget, month="", category="", amount=5000.0)
        with session_scope() as session:
            section = _budget_section(session, today.replace(day=1), today)
        self.assertEqual(section["total"], 0.0, "本月未设总预算时不应回落长期预算")
        self.assertEqual(section["remaining"], 0.0)


class DiarySearchEscapeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        diary_ui._state.update(query="", year=date.today().year, month=date.today().month,
                               selected=date.today())

    def _add(self, title: str, content: str = "正文", day: int = 1) -> None:
        # 日期必须互不相同：日记有「同一 owner+date 只允许一条活跃行」的唯一索引
        with session_scope() as session:
            create(session, Diary, date=date(2026, 9, day), title=title, content=content)

    def test_percent_is_literal(self) -> None:
        self._add("完成 100% 目标", day=1)
        self._add("普通日记", day=2)
        diary_ui._state.update(query="100%")
        _query, rows = diary_ui._list_query()
        self.assertEqual(len(rows), 1, "% 不应被当成通配符匹配全部")
        self.assertIn("100%", rows[0][1])

    def test_underscore_is_literal(self) -> None:
        self._add("a_b", day=3)
        self._add("axb", day=4)
        diary_ui._state.update(query="a_b")
        _query, rows = diary_ui._list_query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "a_b")


class AtomicExportTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-export-"))

    def test_json_export_leaves_no_tmp_file(self) -> None:
        with session_scope() as session:
            create(session, Diary, date=date(2026, 9, 2), title="导出", content="正文")
        target = data_io.export_json(self.root / "out.json", tables=["diaries"])
        self.assertTrue(target.exists())
        self.assertFalse((self.root / "out.json.tmp").exists())

    def test_csv_export_leaves_no_tmp_file(self) -> None:
        with session_scope() as session:
            create(session, Diary, date=date(2026, 9, 3), title="导出2", content="正文")
        target = data_io.export_csv("diaries", self.root / "out.csv")
        self.assertTrue(target.exists())
        self.assertFalse((self.root / "out.csv.tmp").exists())


class BackupPathEncodingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-enc-"))

    def test_path_with_special_characters_is_verifiable(self) -> None:
        """路径含 % # 与中文时，只读 URI 必须编码，否则「备份无法打开」。"""
        target = self.root / "备份 100% #1.db"
        with sqlite3.connect(target) as conn:
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
        ok, message = verify_backup(target)
        self.assertTrue(ok, message)


class StateSemanticsTests(unittest.TestCase):
    def test_missing_key_raises_keyerror(self) -> None:
        with self.assertRaises(KeyError):
            state.todo["no_such_field"]  # type: ignore[index]

    def test_get_falls_back_when_value_is_none(self) -> None:
        state.home["picked_note"] = None
        try:
            self.assertEqual(state.home.get("picked_note", "默认"), "默认")
        finally:
            state.home["picked_note"] = None


if __name__ == "__main__":
    unittest.main()
