"""补齐审计建议的覆盖率盲区：跨月边界、时间轴大区间、JSON 大数据量导入导出。"""

from __future__ import annotations

import json
import time
import unittest
from datetime import date, timedelta

from app.db.models import Account, Diary, Note, Todo
from app.db.repo import create
from app.db.session import session_scope
from app.services import data_io
from app.services.finance import month_summary, six_month_trend
from app.services.timeline import collect

from tests.helpers import reset_db


class MonthSummaryBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        with session_scope() as session:
            account = create(session, Account, name="现金", kind="银行卡", initial_balance=0)

    def _add(self, session, day: date, amount: float, kind: str = "expense", category: str = "餐饮") -> None:
        from app.db.models import Transaction
        from app.db.repo import create as repo_create

        repo_create(
            session, Transaction, kind=kind, amount=amount, category=category,
            account_id="acc-1", date=day, note="",
        )

    def test_only_records_inside_the_month_are_counted(self) -> None:
        """月初/月末算在内，下月 1 号必须排除（区间是左闭右开）。"""
        with session_scope() as session:
            self._add(session, date(2026, 3, 1), 10.0)
            self._add(session, date(2026, 3, 31), 20.0)
            self._add(session, date(2026, 4, 1), 999.0)   # 下月，必须排除
            self._add(session, date(2026, 2, 28), 888.0)  # 上月，必须排除
            self._add(session, date(2026, 3, 15), 100.0, kind="income", category="工资")
        summary = month_summary(session, 2026, 3)
        self.assertEqual(summary["expense"], 30.0)
        self.assertEqual(summary["income"], 100.0)
        self.assertEqual(summary["balance"], 70.0)
        self.assertEqual(summary["categories"], {"餐饮": 30.0})

    def test_december_rolls_over_to_next_year(self) -> None:
        with session_scope() as session:
            self._add(session, date(2026, 12, 31), 5.0)
            self._add(session, date(2027, 1, 1), 7.0)
        self.assertEqual(month_summary(session, 2026, 12)["expense"], 5.0)

    def test_six_month_trend_has_six_rows(self) -> None:
        with session_scope() as session:
            self._add(session, date(2026, 11, 2), 12.5)
        rows = six_month_trend(session, date(2026, 11, 21))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1]["label"], "11月")
        self.assertEqual(rows[-1]["expense"], 12.5)


class TimelineLargeRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_large_range_is_capped_and_fast_enough(self) -> None:
        """跨度一年、上千条记录时，collect 只回 limit 条并且不退化到分钟级。"""
        today = date.today()
        with session_scope() as session:
            for index in range(1500):
                create(session, Todo, title=f"任务{index}", due_date=today - timedelta(days=index % 300))
            for index in range(500):
                # 日记按日期唯一，必须用互不相同的日期
                create(session, Diary, date=today - timedelta(days=index),
                       title=f"日记{index}", content="内容")
        started = time.perf_counter()
        items = collect(today - timedelta(days=365), today, limit=400)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(items), 400)
        self.assertLess(elapsed, 10.0, f"时间轴收集过慢：{elapsed:.1f}s")


class JsonLargeDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_export_import_roundtrip_keeps_all_rows(self) -> None:
        import shutil
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp(prefix="dfb-json-"))
        try:
            with session_scope() as session:
                for index in range(3000):
                    create(session, Note, title=f"笔记{index}", content=f"正文{index}", tags="[]")
            started = time.perf_counter()
            path = data_io.export_json(folder / "dump.json", ["notes"])
            export_seconds = time.perf_counter() - started
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["tables"]["notes"]), 3000)
            self.assertLess(export_seconds, 30.0, f"导出过慢：{export_seconds:.1f}s")

            reset_db()  # 清空后回灌
            started = time.perf_counter()
            outcome = data_io.import_json(path)
            import_seconds = time.perf_counter() - started
            self.assertEqual(outcome["imported"], 3000)
            from sqlalchemy import func, select

            with session_scope() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(Note)), 3000)
            self.assertLess(import_seconds, 60.0, f"导入过慢：{import_seconds:.1f}s")
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
