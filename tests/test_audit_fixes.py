"""审计修复的回归测试。

覆盖：农历年份、循环规则夹取、CSV/Markdown 导入导出、金额精度、
AI 草稿校验、设置密文保留、选择性同步分页退出。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import config
from app.config import SETTINGS_PATH, load_settings, save_settings
from app.db.models import Account, Budget, Note, Transaction
from app.db.repo import create
from app.db.session import init_db, session_scope
from app.services import budget as budget_service
from app.services import data_io, recurrence
from app.services import sync as sync_service
from app.services.ai import drafts as draft_service
from app.services.dates import parse_lunar


def _snapshot_settings() -> str | None:
    return SETTINGS_PATH.read_text(encoding="utf-8") if SETTINGS_PATH.exists() else None


def _restore_settings(backup: str | None) -> None:
    if backup is None:
        SETTINGS_PATH.unlink(missing_ok=True)
    else:
        SETTINGS_PATH.write_text(backup, encoding="utf-8")
    config._SETTINGS_CACHE = None
    config._DECRYPT_FAILED.clear()
    config._DPAPI_AVAILABLE = None


class LunarYearTests(unittest.TestCase):
    def test_explicit_year_is_kept(self) -> None:
        """「2027年九月初九」不能被解析成今年——到期日会整体错一年。"""
        self.assertEqual(parse_lunar("2027年九月初九"), (2027, 9, 9))

    def test_default_year_still_applies(self) -> None:
        self.assertEqual(parse_lunar("九月初九", 2027), (2027, 9, 9))


class RecurrenceClampTests(unittest.TestCase):
    def test_month_31_clamps_to_month_end(self) -> None:
        cfg = {"unit": "month", "interval": 1, "month_mode": "day", "month_days": [31]}
        self.assertEqual(recurrence.next_custom_due(date(2026, 1, 31), cfg), date(2026, 2, 28))
        self.assertEqual(recurrence.next_custom_due(date(2026, 3, 31), cfg), date(2026, 4, 30))

    def test_malformed_config_does_not_crash(self) -> None:
        self.assertIn("周", recurrence.describe({"unit": "week", "weekdays": [0, "x", None]}))
        self.assertIn("年", recurrence.describe({"unit": "year", "year_month": "abc"}))


class DataIoTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-audit-"))
        with session_scope() as session:
            session.query(Transaction).delete()
            session.query(Account).delete()
            session.add(
                Account(id="acc-1", name="现金", kind="银行卡", initial_balance=0, owner_id="local")
            )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_csv_excel_style_date_is_parsed(self) -> None:
        path = self.root / "tx.csv"
        path.write_text(
            "kind,amount,category,note,account_id,date\n支出,12.5,餐饮,午饭,acc-1,2026/1/1\n",
            encoding="utf-8-sig",
        )
        self.assertEqual(data_io.import_csv("transactions", path)["imported"], 1)
        with session_scope() as session:
            self.assertEqual(session.query(Transaction).one().date, date(2026, 1, 1))

    def test_csv_missing_date_uses_date_object(self) -> None:
        """缺日期列时以前塞的是字符串，SQLite Date 会直接拒绝整行。"""
        path = self.root / "tx2.csv"
        path.write_text(
            "kind,amount,category,note,account_id\n支出,9.9,餐饮,晚饭,acc-1\n",
            encoding="utf-8-sig",
        )
        self.assertEqual(data_io.import_csv("transactions", path)["imported"], 1)
        with session_scope() as session:
            self.assertIsInstance(session.query(Transaction).one().date, date)

    def test_markdown_export_dedupes_same_titles(self) -> None:
        with session_scope() as session:
            session.query(Note).delete()
            create(session, Note, title="重名", content="第一篇", tags="[]")
            create(session, Note, title="重名", content="第二篇", tags="[]")
        files = data_io.export_markdown(self.root, ["notes"])
        self.assertEqual(len({path.name for path in files}), 2)
        merged = "".join(path.read_text(encoding="utf-8") for path in files)
        self.assertIn("第一篇", merged)
        self.assertIn("第二篇", merged)


class BudgetPrecisionTests(unittest.TestCase):
    def test_exact_spend_is_not_over_budget(self) -> None:
        """1.10 + 2.20 与 3.30 在浮点下不相等，但按分比较必须判为"刚好花完"。"""
        init_db()
        with session_scope() as session:
            session.query(Budget).delete()
            session.query(Transaction).delete()
            session.query(Account).filter(Account.id == "acc-9").delete()
            session.add(
                Account(id="acc-9", name="现金", kind="银行卡", initial_balance=0, owner_id="local")
            )
            create(session, Budget, month=budget_service.month_key(date.today()),
                   category="餐饮", amount=3.30)
            create(session, Transaction, kind="expense", amount=1.10, category="餐饮",
                   account_id="acc-9", date=date.today(), note="a")
            create(session, Transaction, kind="expense", amount=2.20, category="餐饮",
                   account_id="acc-9", date=date.today(), note="b")
        overview = budget_service.overview()
        item = next(row for row in overview["categories"] if row["category"] == "餐饮")
        self.assertFalse(item["over"])
        self.assertEqual(item["remaining"], 0.0)
        self.assertEqual(budget_service.alerts(), [])


class AiDraftValidationTests(unittest.TestCase):
    def _draft(self, payload: dict):
        return draft_service.extract(json.dumps({"drafts": [payload]}))[0]

    def test_non_finite_amount_is_rejected(self) -> None:
        for value in (float("nan"), float("inf")):
            draft = self._draft({"type": "transaction", "amount": value, "category": "餐饮"})
            self.assertFalse(draft.valid, f"{value} 不应通过校验")

    def test_unknown_transaction_kind_is_rejected(self) -> None:
        draft = self._draft({"type": "transaction", "amount": 10, "kind": "transfer"})
        self.assertFalse(draft.valid)

    def test_private_cover_is_dropped(self) -> None:
        draft = self._draft(
            {"type": "entertainment", "title": "片", "cover": "http://127.0.0.1:8000/a.png"}
        )
        self.assertEqual(draft.fields["cover"], "")


class SettingsCiphertextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backup = _snapshot_settings()

    def tearDown(self) -> None:
        _restore_settings(self.backup)

    def test_decrypt_failure_does_not_wipe_ciphertext(self) -> None:
        save_settings({"sync_token": "real-token"})
        on_disk = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertTrue(str(on_disk["sync_token"]).startswith(("dpapi:", "b64:")))

        # 模拟换了 Windows 账户 / 机器：DPAPI 解不开这段密文
        bogus = "dpapi:bm90LWEtdmFsaWQtYmxvYg=="
        on_disk["sync_token"] = bogus
        SETTINGS_PATH.write_text(json.dumps(on_disk, ensure_ascii=False), encoding="utf-8")
        config._SETTINGS_CACHE = None
        self.assertEqual(load_settings()["sync_token"], "")  # 解不开，只能当未配置

        save_settings({"sync_cursors": {}})  # 后台保存游标不能抹掉原密文
        after = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(after["sync_token"], bogus)

    def test_explicit_empty_value_clears_secret(self) -> None:
        save_settings({"sync_token": "real-token"})
        save_settings({"sync_token": ""})
        after = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(after["sync_token"], "")


class SyncPullGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backup = _snapshot_settings()
        init_db()
        save_settings({
            "server_url": "http://127.0.0.1:8000",
            "sync_token": "tok",
            "sync_enabled": True,
            "sync_tables": ["todos"],
        })

    def tearDown(self) -> None:
        _restore_settings(self.backup)
        # 假响应里的 server_time 会写进全局时钟偏移；必须复位，否则后续用例
        # 的 updated_at 会被整体挪走，时间轴/首页按日期过滤时全部漏掉。
        from app.services.clock import set_clock_offset

        set_clock_offset(0.0)

    def test_unselected_backlog_does_not_loop_forever(self) -> None:
        calls = {"n": 0}
        from app.db.models import utcnow

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "changes": [{"table": "transactions", "data": {"id": "x"}}],
                    "cursors": {"transactions": {"sequence": 5}},
                    "has_more": True,
                    "server_time": utcnow().isoformat(),
                }

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args) -> bool:
                return False

            def post(self, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] > 3:
                    raise AssertionError("_pull 没有退出分页循环")
                return _Response()

        settings = sync_service.load_settings()
        sync_service.sync_client._pull(_Client(), settings)
        self.assertEqual(calls["n"], 1, "本页游标没有推进时必须立即停止分页")


if __name__ == "__main__":
    unittest.main()
