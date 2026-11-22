"""第三轮深度评审 P1 修复的回归用例。

对应《定风波-深度评审报告-2026-11-22》P1 清单中可在无 UI 环境下验证的部分：
必填数值、搜索日期边界、农历锚点、收支 kind 归一化。
（同步 500 分片见 tests/test_sync_e2e.py；关窗 flush、恢复后 resume 属 UI 路径，
需要真实 Flet 页面，另行手工验证。）
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from app.db.models import Note
from app.db.repo import create
from app.db.session import session_scope
from sqlalchemy import select

from tests.helpers import reset_db


class RequiredNumberTests(unittest.TestCase):
    """必填数值留空必须返回错误，而不是 None 或 0.0。"""

    def test_required_empty_returns_error(self) -> None:
        from app.ui.forms import _number

        value, error = _number("", label="金额", minimum=0.01, allow_empty=False)
        self.assertIsNone(value)
        self.assertIn("不能为空", error or "")

    def test_required_whitespace_returns_error(self) -> None:
        from app.ui.forms import _number

        value, error = _number("   ", label="体重", minimum=1, allow_empty=False)
        self.assertIsNone(value)
        self.assertIn("不能为空", error or "")

    def test_optional_empty_stays_none(self) -> None:
        from app.ui.forms import _number

        self.assertEqual(_number("", label="热量", allow_empty=True), (None, None))

    def test_out_of_range_still_errors(self) -> None:
        from app.ui.forms import _number

        value, error = _number("0", label="金额", minimum=0.01, allow_empty=False)
        self.assertIsNone(value)
        self.assertTrue(error)


class SearchLocalDayBoundaryTests(unittest.TestCase):
    """笔记搜索的「今天」必须按本地日历判断（updated_at 是 naive UTC）。"""

    def setUp(self) -> None:
        reset_db()

    def test_note_from_early_local_day_is_found(self) -> None:
        # 本地今天 07:00 = UTC 昨天 23:00（UTC+8）。按本地日历属于「今天」。
        today = date.today()
        local_midnight_utc = datetime.combine(today, datetime.min.time()) - timedelta(hours=8)
        early_local_today = local_midnight_utc + timedelta(hours=7)
        with session_scope() as session:
            note = create(session, Note, title="凌晨写的", content="正文", tags="[]")
            note.updated_at = early_local_today

        from app.services.search import search

        grouped = search("笔记 今天", today=today)
        titles = [hit.title for hit in grouped.get("notes", [])]
        self.assertIn("凌晨写的", titles, "本地今天 07:00 编辑的笔记不应被算成昨天")

    def test_note_from_previous_local_day_is_excluded(self) -> None:
        today = date.today()
        local_midnight_utc = datetime.combine(today, datetime.min.time()) - timedelta(hours=8)
        late_yesterday_local = local_midnight_utc - timedelta(hours=2)  # 本地昨天 22:00
        with session_scope() as session:
            note = create(session, Note, title="昨晚写的", content="正文", tags="[]")
            note.updated_at = late_yesterday_local

        from app.services.search import search

        grouped = search("笔记 今天", today=today)
        titles = [hit.title for hit in grouped.get("notes", [])]
        self.assertNotIn("昨晚写的", titles)


class LunarAnchorTests(unittest.TestCase):
    """农历按月/按年循环的锚点必须是农历日号。"""

    def test_anchor_uses_lunar_day(self) -> None:
        from app.services.todo_rules import _lunar_day_of, ensure_anchor

        # 2026-02-17 是农历正月初一
        spring = date(2026, 2, 17)
        self.assertEqual(_lunar_day_of(spring), 1)
        self.assertIn('"anchor_day": 1', ensure_anchor("", spring, lunar=True))

    def test_gregorian_anchor_unchanged(self) -> None:
        from app.services.todo_rules import ensure_anchor

        self.assertIn('"anchor_day": 31', ensure_anchor("", date(2026, 1, 31), lunar=False))

    def test_lunar_monthly_advances_one_lunar_month(self) -> None:
        from app.services.dates import next_lunar_due
        from app.services.todo_rules import ensure_anchor, next_occurrence

        spring = date(2026, 2, 17)
        config = ensure_anchor("", spring, lunar=True)
        following = next_occurrence(spring, "month", config, True)
        self.assertIsNotNone(following)
        # 农历二月初一
        from zhdate import ZhDate

        self.assertEqual(ZhDate.from_datetime(datetime.combine(following, datetime.min.time())).lunar_day, 1)
        self.assertEqual(next_lunar_due(spring, "month", 1), following)


class TransactionKindTests(unittest.TestCase):
    """历史/导入的中文 kind 不能再让收支页 KeyError。"""

    def test_chinese_kind_is_normalized(self) -> None:
        from app.ui.finance_ui import _kind_code, _kind_sign

        self.assertEqual(_kind_code("支出"), "expense")
        self.assertEqual(_kind_code("收入"), "income")
        self.assertEqual(_kind_code("转账"), "transfer")
        self.assertEqual(_kind_sign("支出"), "-")
        self.assertEqual(_kind_sign("收入"), "+")
        self.assertEqual(_kind_sign("转账"), "⇄")

    def test_unknown_kind_does_not_raise(self) -> None:
        from app.ui.finance_ui import _kind_code, _kind_sign

        self.assertEqual(_kind_code("weird"), "weird")
        self.assertEqual(_kind_sign("weird"), "-")
        self.assertEqual(_kind_code(None), "expense")


if __name__ == "__main__":
    unittest.main()

class MultiAccountDraftTests(unittest.TestCase):
    """多账户时 AI 交易草稿不能替用户猜账户（静默落最旧账户 = 报表算错）。"""

    def setUp(self) -> None:
        reset_db()

    def _draft(self):
        from app.services.ai.drafts import Draft

        return Draft(
            "transaction",
            fields={"kind": "expense", "amount": 32.0, "category": "餐饮",
                    "account_id": "", "date": date(2026, 11, 22), "note": "午饭"},
        )

    def test_single_account_is_used(self) -> None:
        from app.db.models import Account
        from app.db.repo import create
        from app.db.session import session_scope

        with session_scope() as session:
            create(session, Account, name="现金", kind="现金", initial_balance=0)

        from app.services.ai import drafts as service

        draft = self._draft()
        saved = service.save([draft])
        self.assertEqual(len(saved), 1, draft.reason)

    def test_multiple_accounts_requires_explicit_choice(self) -> None:
        from app.db.models import Account
        from app.db.repo import create
        from app.db.session import session_scope

        with session_scope() as session:
            create(session, Account, name="现金", kind="现金", initial_balance=0)
            create(session, Account, name="工资卡", kind="银行卡", initial_balance=0)

        from app.services.ai import drafts as service

        draft = self._draft()
        saved = service.save([draft])
        self.assertEqual(saved, [], "多账户时不应自动落库")
        self.assertIn("多个账户", draft.reason or "")

    def test_explicit_account_still_wins(self) -> None:
        from app.db.models import Account, Transaction
        from app.db.repo import create
        from app.db.session import session_scope
        from sqlalchemy import select

        with session_scope() as session:
            first = create(session, Account, name="现金", kind="现金", initial_balance=0)
            create(session, Account, name="工资卡", kind="银行卡", initial_balance=0)
            account_id = first.id

        from app.services.ai import drafts as service

        draft = self._draft()
        draft.fields["account_id"] = account_id
        saved = service.save([draft])
        self.assertEqual(len(saved), 1, draft.reason)

        with session_scope() as session:
            tx = session.scalar(select(Transaction))
            self.assertEqual(tx.account_id, account_id)
