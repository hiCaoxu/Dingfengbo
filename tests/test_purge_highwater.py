"""墓碑清理高水位：离线超窗设备不能把已物理删除的记录推回去。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import PurgeMark, utcnow
from tests.test_server_sync import _boot_server, note_change


class PurgeHighWaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        import logging

        for handler in logging.getLogger().handlers[:]:
            handler.close()
            logging.getLogger().removeHandler(handler)
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._original = self.server.SessionLocal
        self.server.SessionLocal = self.Session

    def tearDown(self) -> None:
        self.server.SessionLocal = self._original
        self.engine.dispose()

    def _mark(self, purged_before: datetime) -> None:
        # 服务端每次 push 后的清理也会写这条水位，这里用 merge 覆盖而不是插入。
        with self.Session() as session:
            session.merge(
                PurgeMark(owner_id="*", purged_before=purged_before,
                          purged_at=utcnow(), purged_count=3)
            )
            session.commit()

    def test_stale_resurrect_is_rejected(self) -> None:
        self._mark(utcnow() - timedelta(days=90))
        old_stamp = utcnow() - timedelta(days=200)
        change = note_change("被清理后又被推回", old_stamp)
        payload = self.server.push(
            self.server.PushRequest(changes=[change]), "Bearer stale-token"
        )
        self.assertEqual(payload["accepted"], [])
        self.assertEqual(len(payload["rejected"]), 1)
        self.assertIn("过期清理", payload["rejected"][0]["reason"])

    def test_recent_record_is_still_accepted(self) -> None:
        self._mark(utcnow() - timedelta(days=90))
        change = note_change("正常新记录", utcnow() - timedelta(minutes=5))
        payload = self.server.push(
            self.server.PushRequest(changes=[change]), "Bearer fresh-token"
        )
        self.assertEqual(len(payload["accepted"]), 1)
        self.assertEqual(payload["rejected"], [])

    def test_existing_record_is_not_rejected(self) -> None:
        """记录还在（只是比较旧）时走正常 LWW，不该被「过期清理」规则误伤。"""
        old_stamp = utcnow() - timedelta(days=200)
        first = note_change("早就存在", old_stamp)
        self.server.push(self.server.PushRequest(changes=[first]), "Bearer keep-token")
        self._mark(utcnow() - timedelta(days=90))
        updated = note_change("旧记录的新编辑", old_stamp + timedelta(seconds=1))
        updated["data"]["id"] = first["data"]["id"]
        payload = self.server.push(
            self.server.PushRequest(changes=[updated]), "Bearer keep-token"
        )
        self.assertEqual(len(payload["accepted"]), 1, "记录存在时不应按过期清理拒绝")

    def test_no_mark_means_no_rejection(self) -> None:
        change = note_change("没有水位标记", utcnow() - timedelta(days=200))
        payload = self.server.push(
            self.server.PushRequest(changes=[change]), "Bearer nomark-token"
        )
        self.assertEqual(len(payload["accepted"]), 1)


if __name__ == "__main__":
    unittest.main()
