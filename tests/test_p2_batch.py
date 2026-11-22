"""深度评审 P2 批次（安全与正确性）的回归用例。"""

from __future__ import annotations

import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from PIL import Image

from app.db.models import Account, Budget, Transaction
from app.db.repo import create
from app.db.session import session_scope
from sqlalchemy import select

from tests.helpers import reset_db


class SinceTimezoneTests(unittest.TestCase):
    """pull 的 since 允许带时区（旧客户端），不能因为 naive/aware 混比而 500。"""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_server_sync import _boot_server

        cls.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        import logging

        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)
        cls.temp_dir.cleanup()

    def test_aware_since_is_accepted(self) -> None:
        payload = self.server.pull(
            self.server.PullRequest(since="2026-11-22T10:00:00+08:00"), "Bearer tz-token"
        )
        self.assertEqual(payload["changes"], [])
        self.assertIn("server_time", payload)

    def test_still_rejects_garbage(self) -> None:
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self.server.pull(self.server.PullRequest(since="not-a-date"), "Bearer tz-token")
        self.assertEqual(ctx.exception.status_code, 400)


class BudgetSemanticsTests(unittest.TestCase):
    """空 month 表示「长期」；本月视图不再回落到长期总预算（与报告口径一致）。"""

    def setUp(self) -> None:
        reset_db()

    def test_empty_month_creates_standing_budget(self) -> None:
        from app.services import budget as service

        service.upsert_budget("", "", 5000.0)
        with session_scope() as session:
            rows = list(session.scalars(select(Budget)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].month, "", "空 month 应落成长期条目")

    def test_none_month_means_current_month(self) -> None:
        from app.services import budget as service

        service.upsert_budget(None, "", 3000.0)
        with session_scope() as session:
            rows = list(session.scalars(select(Budget)))
        self.assertEqual(rows[0].month, service.month_key())

    def test_overview_does_not_fall_back_to_standing(self) -> None:
        from app.services import budget as service

        service.upsert_budget("", "", 5000.0)  # 只设长期
        data = service.overview(date.today())
        self.assertEqual(data["total_budget"], 0.0, "本月未设预算时不应拿长期额度当分母")
        self.assertEqual(data["standing_budget"], 5000.0, "长期额度应单独暴露")
        self.assertEqual(data["remaining"], 0.0)


class RecurrenceDirtyConfigTests(unittest.TestCase):
    def test_non_iterable_days_does_not_raise(self) -> None:
        from app.services.recurrence import normalize_config

        # normalize_config 接收的是已解析的 dict（repeat_config 由 parse_config 解析）
        for raw in ({"kind": "monthly_days", "days": 15}, {"kind": "monthly_days", "days": True}):
            config = normalize_config(raw)
            self.assertEqual(config.get("month_days"), [])


class ImageGuardTests(unittest.TestCase):
    def test_pixel_bomb_is_rejected(self) -> None:
        from app.services import images

        buffer = io.BytesIO()
        Image.new("RGB", (200, 200), (10, 10, 10)).save(buffer, format="PNG")
        with mock.patch.object(images, "MAX_PIXELS", 1000):
            with self.assertRaises(images.UploadError) as ctx:
                images.compress(buffer.getvalue())
        self.assertIn("尺寸", str(ctx.exception))

    def test_oversized_source_file_is_rejected_before_reading(self) -> None:
        from app.services import images

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.jpg"
            path.write_bytes(b"\xff\xd8\xff" + b"0" * 2048)
            with mock.patch.object(images, "MAX_SOURCE_BYTES", 512):
                with self.assertRaises(images.UploadError) as ctx:
                    images.upload_path(path)
        self.assertIn("过大", str(ctx.exception))


class CoerceInfinityTests(unittest.TestCase):
    """'inf' 只该丢这一列，不该让整行导入失败。"""

    def test_inf_is_dropped_not_raised(self) -> None:
        from sqlalchemy import Integer

        from app.services.data_io import _coerce

        class _Column:
            name = "minutes"
            type = Integer()

        self.assertIsNone(_coerce(_Column(), "inf"))
        self.assertIsNone(_coerce(_Column(), "nan"))


class LegacyMigrationNullAccountTests(unittest.TestCase):
    """旧库的 NULL account_id 不能迁移成字符串 "None"。"""

    def test_null_account_becomes_empty_string(self) -> None:
        from sqlalchemy import create_engine, text

        from app.db.migrations import initialize_schema

        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE accounts (
                    id INTEGER PRIMARY KEY, owner_id VARCHAR(64), created_at DATETIME,
                    updated_at DATETIME, is_deleted BOOLEAN, sync_status VARCHAR(16),
                    name VARCHAR(80), kind VARCHAR(24), initial_balance FLOAT
                )
            """))
            connection.execute(text("""
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY, owner_id VARCHAR(64), created_at DATETIME,
                    updated_at DATETIME, is_deleted BOOLEAN, sync_status VARCHAR(16),
                    kind VARCHAR(16), amount FLOAT, category VARCHAR(40),
                    account_id INTEGER, to_account_id INTEGER, date DATE, note TEXT
                )
            """))
            connection.execute(text("""
                INSERT INTO transactions VALUES
                  (1, 'local', '2026-01-02 00:00:00', '2026-01-02 00:00:00', 0, 'pending',
                   'expense', 100, '餐饮', NULL, NULL, '2026-01-02', '孤儿交易')
            """))

        initialize_schema(engine)
        with engine.connect() as connection:
            stored = connection.execute(
                text("SELECT account_id FROM transactions")
            ).scalar()
        self.assertEqual(stored, "", "NULL account_id 应落成空串，而不是字面量 None")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
