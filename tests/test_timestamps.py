"""时间口径：created_at / updated_at 同钟、UTC 存储、展示转本地。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.db.models import Note
from app.db.repo import create, update
from app.db.session import session_scope
from app.services import sync as sync_service
from app.services.ai.context import serialize_row
from app.services.clock import get_clock_offset, set_clock_offset
from app.services.dates import local_datetime_from_utc

from tests.helpers import reset_db


class WriteClockTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self._offset = get_clock_offset()
        set_clock_offset(0.0)

    def tearDown(self) -> None:
        set_clock_offset(self._offset)

    def test_created_and_updated_share_the_aligned_clock(self) -> None:
        """曾经 created_at 走裸 UTC、updated_at 叠了时钟偏移，同一行的更新会早于创建。"""
        set_clock_offset(3600.0)
        with session_scope() as session:
            note = create(session, Note, title="对齐时钟", content="", tags="[]")
            self.assertEqual(note.created_at, note.updated_at)

        set_clock_offset(0.0)
        with session_scope() as session:
            fresh = create(session, Note, title="本地时钟", content="", tags="[]")
        delta = abs((fresh.updated_at - datetime.utcnow()).total_seconds())
        self.assertLess(delta, 5.0, "未叠加偏移时 updated_at 应接近当前 UTC")

    def test_update_keeps_created_at_and_moves_updated_at(self) -> None:
        with session_scope() as session:
            note = create(session, Note, title="编辑", content="初稿", tags="[]")
            note_id = note.id
            created_at = note.created_at
        set_clock_offset(60.0)  # 模拟服务端时钟快 60 秒
        with session_scope() as session:
            update(session, session.get(Note, note_id), content="改稿")
        with session_scope() as session:
            after = session.get(Note, note_id)
            self.assertEqual(after.created_at, created_at)
            self.assertGreater(after.updated_at, created_at + timedelta(seconds=30))


class LocalDisplayTests(unittest.TestCase):
    def test_local_datetime_converts_utc_to_local(self) -> None:
        utc_value = datetime(2026, 1, 1, 0, 0)
        expected = (
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
            .astimezone()
            .replace(tzinfo=None)
        )
        self.assertEqual(local_datetime_from_utc(utc_value), expected)

    def test_aware_datetime_is_accepted(self) -> None:
        aware = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(local_datetime_from_utc(aware), local_datetime_from_utc(aware.replace(tzinfo=None)))

    def test_ai_context_shows_local_time(self) -> None:
        reset_db()
        with session_scope() as session:
            note = create(session, Note, title="上下文", content="正文", tags="[]")
            text = serialize_row(note)
        stored = note.updated_at
        expected = local_datetime_from_utc(stored).strftime("%Y-%m-%d %H:%M")
        self.assertIn(expected, text)


class TimestampMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_legacy_inversion_is_normalized_once(self) -> None:
        """历史库存在 updated_at < created_at；一次性迁移把它们对齐。"""
        from app.db.migrations import initialize_schema
        from app.db.session import engine

        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO notes
                    (id, owner_id, created_at, updated_at, is_deleted, sync_status,
                     sync_seq, title, content, tags)
                VALUES
                    ('legacy-ts', 'local', '2026-03-01 10:00:00',
                     '2026-03-01 09:59:58', 0, 'clean', 0, '倒挂', '', '[]')
                """
            )
            connection.exec_driver_sql("PRAGMA user_version = 1")  # 已迁移金额、未修正时间

        initialize_schema(engine)
        with engine.connect() as connection:
            updated, created = connection.exec_driver_sql(
                "SELECT updated_at, created_at FROM notes WHERE id = 'legacy-ts'"
            ).fetchone()
            version = int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        self.assertEqual(updated, created)
        self.assertGreaterEqual(version, 2)

        # 再跑一次不应改变任何东西
        initialize_schema(engine)
        with engine.connect() as connection:
            again = connection.exec_driver_sql(
                "SELECT updated_at FROM notes WHERE id = 'legacy-ts'"
            ).scalar()
        self.assertEqual(again, created)


class ClockOffsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._offset = get_clock_offset()

    def tearDown(self) -> None:
        set_clock_offset(self._offset)

    def test_offset_discounts_half_the_round_trip(self) -> None:
        """服务端时间在响应前一刻取，扣掉 RTT/2 才接近真实钟差。"""
        server_time = datetime(2026, 1, 1, 12, 0, 0)
        sent_at = server_time - timedelta(seconds=2)  # 往返 2 秒
        payload = {"server_time": server_time.isoformat()}
        with mock.patch.object(sync_service, "utcnow", return_value=server_time):
            sync_service.sync_client._observe_server_time(payload, sent_at)
        # 不扣 RTT 会得到 +2 秒；扣掉一半应为 +1 秒
        self.assertAlmostEqual(get_clock_offset(), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
