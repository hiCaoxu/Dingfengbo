"""月/年循环的日号锚点：1/31 不能漂成 2/28 之后再也回不到 31。"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime

from app.db.models import Todo
from app.db.repo import create
from app.db.session import session_scope
from app.services.dates import next_due, next_lunar_due
from app.services.todo_rules import ensure_anchor, next_occurrence
from app.ui.todo import toggle

from tests.helpers import reset_db


class AnchorTests(unittest.TestCase):
    def test_month_rule_returns_to_31(self) -> None:
        self.assertEqual(next_due(date(2026, 1, 31), "month", 31), date(2026, 2, 28))
        self.assertEqual(next_due(date(2026, 2, 28), "month", 31), date(2026, 3, 31))

    def test_year_rule_returns_to_leap_day(self) -> None:
        self.assertEqual(next_due(date(2024, 2, 29), "year", 29), date(2025, 2, 28))
        self.assertEqual(next_due(date(2027, 2, 28), "year", 29), date(2028, 2, 29))

    def test_without_anchor_keeps_legacy_drift(self) -> None:
        """没有锚点的存量数据维持旧行为（文档化的已知漂移，不静默改变历史）。"""
        self.assertEqual(next_due(date(2026, 2, 28), "month"), date(2026, 3, 28))

    def test_next_occurrence_reads_anchor_from_config(self) -> None:
        config = json.dumps({"anchor_day": 31})
        self.assertEqual(
            next_occurrence(date(2026, 2, 28), "month", config, False), date(2026, 3, 31)
        )

    def test_ensure_anchor_is_idempotent(self) -> None:
        raw = ensure_anchor("", date(2026, 1, 31))
        self.assertEqual(json.loads(raw)["anchor_day"], 31)
        self.assertEqual(ensure_anchor(raw, date(2026, 2, 28)), raw, "已有锚点不应被改写")

    def test_lunar_month_rule_uses_anchor(self) -> None:
        try:
            from zhdate import ZhDate
        except Exception:  # pragma: no cover - 没装 zhdate 时跳过
            self.skipTest("未安装 zhdate")

        start = ZhDate(2026, 1, 30).to_datetime().date()
        following = next_lunar_due(start, "month", 30)
        self.assertIsNotNone(following)
        # zhdate 的 from_datetime 只接受 datetime（传 date 会 TypeError）
        zh = ZhDate.from_datetime(datetime.combine(following, datetime.min.time()))
        # 下个农历月若只有 29 天则夹到 29，但锚点仍是 30：再推一个月应回到 30
        days = ZhDate.month_days(zh.lunar_year)[zh.lunar_month - 1]
        self.assertEqual(zh.lunar_day, min(30, days))
        if days < 30:
            again = next_lunar_due(following, "month", 30)
            zh_again = ZhDate.from_datetime(datetime.combine(again, datetime.min.time()))
            self.assertEqual(zh_again.lunar_day, min(30, ZhDate.month_days(
                zh_again.lunar_year)[zh_again.lunar_month - 1]))


class ToggleAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_toggle_persists_anchor_across_occurrences(self) -> None:
        with session_scope() as session:
            todo = create(session, Todo, title="月末复盘", repeat_rule="month",
                          due_date=date(2026, 1, 31))
            todo_id = todo.id

        toggle(None, todo_id, lambda: None)  # 完成 1/31 → 下一期 2/28，配置里写入锚点 31

        with session_scope() as session:
            rows = {
                row.due_date: row
                for row in session.query(Todo).filter(Todo.title == "月末复盘")
            }
        self.assertIn(date(2026, 2, 28), rows)
        config = json.loads(rows[date(2026, 2, 28)].repeat_config or "{}")
        self.assertEqual(config.get("anchor_day"), 31, "锚点必须随下一期一起持久化")

        toggle(None, rows[date(2026, 2, 28)].id, lambda: None)  # 完成 2/28 → 应回到 3/31

        with session_scope() as session:
            due_dates = {
                row.due_date for row in session.query(Todo).filter(Todo.title == "月末复盘")
            }
        self.assertIn(date(2026, 3, 31), due_dates, "锚点必须让 3 月回到 31 号")


if __name__ == "__main__":
    unittest.main()
