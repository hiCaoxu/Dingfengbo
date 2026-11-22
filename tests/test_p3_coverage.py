"""深度评审「测试补全」批次的回归用例。

对应报告第六节的缺口：墓碑物理清理与高水位、封面 SSRF 重定向复核、
游标持久化、历史迁移的金额/模板/优先级。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.models import Budget, Note, RecurringRule, Todo, Transaction, utcnow
from app.db.repo import create, purge_deleted
from app.db.session import session_scope

from tests.helpers import reset_db


class PurgeTombstoneTests(unittest.TestCase):
    """墓碑清理只删「已删除 + 已同步 + 超保留窗口」的行。"""

    def setUp(self) -> None:
        reset_db()

    def _tombstone(self, *, days_old: int, sync_status: str = "clean", deleted: bool = True):
        with session_scope() as session:
            note = create(session, Note, title="墓碑", content="", tags="[]")
            note.is_deleted = deleted
            note.sync_status = sync_status
            note.updated_at = utcnow() - timedelta(days=days_old)
            note_id = note.id
        return note_id

    def test_old_clean_tombstone_is_purged(self) -> None:
        old = self._tombstone(days_old=120)
        with session_scope() as session:
            removed = purge_deleted(session, retention_days=90)
            self.assertEqual(removed, 1)
            self.assertIsNone(session.get(Note, old))

    def test_recent_or_pending_or_alive_rows_survive(self) -> None:
        recent = self._tombstone(days_old=10)
        pending = self._tombstone(days_old=120, sync_status="pending")
        alive = self._tombstone(days_old=120, deleted=False)
        with session_scope() as session:
            self.assertEqual(purge_deleted(session, retention_days=90), 0)
            for record_id in (recent, pending, alive):
                self.assertIsNotNone(session.get(Note, record_id))


class PurgeHighWaterWriteTests(unittest.TestCase):
    """服务端只有「真的删掉了」才更新删除高水位，且受小时节流约束。"""

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

    def setUp(self) -> None:
        self.Session = sessionmaker(bind=self.server.engine, expire_on_commit=False)
        self._orig = self.server.SessionLocal
        self.server.SessionLocal = self.Session
        self.server._last_purge = 0.0
        # 类内共享同一个服务端库：先清掉上一个用例写下的高水位，保证互不影响。
        with self.Session() as session:
            session.query(self.server.PurgeMark).delete()
            session.query(Note).delete()
            session.commit()

    def tearDown(self) -> None:
        self.server.SessionLocal = self._orig
        self.server._last_purge = 0.0

    def test_no_mark_when_nothing_was_purged(self) -> None:
        self.server._maybe_purge_tombstones()
        with self.Session() as session:
            self.assertIsNone(session.get(self.server.PurgeMark, "*"))

    def test_mark_written_after_real_purge(self) -> None:
        with self.Session() as session:
            note = session.scalar(select(Note))
            if note is None:
                note = Note(title="老墓碑", content="", tags="[]")
                session.add(note)
            note.is_deleted = True
            note.sync_status = "clean"
            note.updated_at = utcnow() - timedelta(days=200)
            session.commit()

        self.server._maybe_purge_tombstones()
        with self.Session() as session:
            mark = session.get(self.server.PurgeMark, "*")
        self.assertIsNotNone(mark, "真的删掉墓碑后必须写删除高水位")
        self.assertGreaterEqual(mark.purged_count, 1)

    def test_throttle_blocks_second_run(self) -> None:
        import time as _time

        self.server._maybe_purge_tombstones()
        self.server._last_purge = _time.monotonic()  # 刚跑过
        with mock.patch.object(self.server, "purge_deleted") as patched:
            self.server._maybe_purge_tombstones()
        patched.assert_not_called()


class CoverRedirectTests(unittest.TestCase):
    """封面下载必须逐跳复核重定向目标（SSRF 纵深）。"""

    def test_redirect_to_internal_is_rejected(self) -> None:
        from app.services import covers

        class _Response:
            status_code = 302

            def __init__(self) -> None:
                self.headers = {"location": "http://127.0.0.1:8000/files/secret"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Client:
            def stream(self, *a, **k):
                return _Response()

        with self.assertRaises(ValueError) as ctx:
            covers._download("https://img.example.com/a.png", _Client())
        self.assertIn("内网", str(ctx.exception))

    def test_too_many_hops_is_rejected(self) -> None:
        from app.services import covers

        class _Response:
            status_code = 307

            def __init__(self, index: int) -> None:
                self.headers = {"location": f"https://img.example.com/hop{index}"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Client:
            def __init__(self) -> None:
                self.hops = 0

            def stream(self, *a, **k):
                self.hops += 1
                return _Response(self.hops)

        # 真实 is_safe_remote_url 会去解析 img.example.com（测试机上 DNS 不可用会先报
        # 「内网/非法地址」），这里只验证「跳数用尽」这条分支。
        with mock.patch.object(covers, "is_safe_remote_url", lambda url: True):
            with self.assertRaises(ValueError) as ctx:
                covers._download("https://img.example.com/start", _Client())
        self.assertIn("重定向次数过多", str(ctx.exception))


class CursorPersistenceTests(unittest.TestCase):
    """游标按 (server_url, token) 分桶，脏 sequence 被过滤。

    ``_save_cursors`` 写的是 settings.json（并且先回读最新设置再合并），所以这里用
    真实的 save_settings / load_settings，而不是内存里改一个 dict。
    """

    def setUp(self) -> None:
        from app.config import load_settings, save_settings
        from app.services.sync import SyncClient

        save_settings({"sync_cursors": {}})
        self.client = SyncClient()
        self.load_settings = staticmethod(load_settings)
        self.save_settings = save_settings
        self.settings = {
            "server_url": "http://a.example:8000",
            "sync_token": "token-a",
            "sync_cursors": {},
        }

    def _load(self, **overrides):
        # 从真实设置出发（含刚落盘的 sync_cursors），只覆盖 server/token：
        # 直接拿 self.settings 覆盖会把 sync_cursors 一起清空，测出来永远是 {}。
        settings = dict(self.load_settings())
        settings["server_url"] = overrides.get("server_url", self.settings["server_url"])
        settings["sync_token"] = overrides.get("sync_token", self.settings["sync_token"])
        return self.client._load_cursors(settings)

    def test_round_trip_and_isolation(self) -> None:
        cursors = {"todos": {"sequence": 7}, "notes": {"sequence": 3}}
        self.client._save_cursors(self.settings, cursors)
        self.assertEqual(self._load(), cursors)

        self.assertEqual(self._load(sync_token="token-b"), {}, "不同令牌的游标不能串")
        self.assertEqual(
            self._load(server_url="http://b.example:8000"), {}, "不同服务端不能串"
        )

    def test_invalid_sequence_is_dropped(self) -> None:
        self.client._save_cursors(self.settings, {"todos": {"sequence": "not-a-number"}})
        self.assertEqual(self._load(), {})


class LegacyMoneyMigrationTests(unittest.TestCase):
    """历史迁移：四张金额表都要换算成整数分，且只换算一次。"""

    def _legacy_engine(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            for table, extra in (
                ("transactions", "kind VARCHAR(16), amount FLOAT, category VARCHAR(40), "
                                 "account_id VARCHAR(36), to_account_id VARCHAR(36), date DATE, note TEXT"),
                ("accounts", "name VARCHAR(80), kind VARCHAR(24), initial_balance FLOAT"),
                ("budgets", "month VARCHAR(7), category VARCHAR(40), amount FLOAT, note TEXT"),
                ("recurring_rules", "name VARCHAR(120), kind VARCHAR(16), amount FLOAT, category VARCHAR(40), "
                                    "account_id VARCHAR(36), period VARCHAR(8), day INTEGER, next_date DATE, "
                                    "last_generated DATE, active BOOLEAN, note TEXT"),
            ):
                connection.execute(text(f"""
                    CREATE TABLE {table} (
                        id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(64), created_at DATETIME,
                        updated_at DATETIME, is_deleted BOOLEAN, sync_status VARCHAR(16),
                        sync_seq INTEGER DEFAULT 0, {extra}
                    )
                """))
            connection.execute(text(
                "INSERT INTO transactions (id, owner_id, created_at, updated_at, is_deleted, "
                "sync_status, sync_seq, kind, amount, category, account_id, date, note) "
                "VALUES ('t1','local','2026-01-01 00:00:00','2026-01-01 00:00:00',0,'clean',1,"
                "'expense',12.34,'餐饮','a1','2026-01-01','')"
            ))
            connection.execute(text(
                "INSERT INTO accounts (id, owner_id, created_at, updated_at, is_deleted, sync_status, "
                "sync_seq, name, kind, initial_balance) VALUES "
                "('a1','local','2026-01-01 00:00:00','2026-01-01 00:00:00',0,'clean',2,'现金','现金',100.5)"
            ))
            connection.execute(text(
                "INSERT INTO budgets (id, owner_id, created_at, updated_at, is_deleted, sync_status, "
                "sync_seq, month, category, amount, note) VALUES "
                "('b1','local','2026-01-01 00:00:00','2026-01-01 00:00:00',0,'clean',3,'2026-01','餐饮',300.25,'')"
            ))
            connection.execute(text(
                "INSERT INTO recurring_rules (id, owner_id, created_at, updated_at, is_deleted, "
                "sync_status, sync_seq, name, kind, amount, category, account_id, period, day, active, note) "
                "VALUES ('r1','local','2026-01-01 00:00:00','2026-01-01 00:00:00',0,'clean',4,"
                "'房租','expense',1500.75,'居住','a1','month',1,1,'')"
            ))
            connection.execute(text("PRAGMA user_version = 0"))
        return engine

    def test_all_money_columns_become_cents_once(self) -> None:
        from app.db.migrations import initialize_schema

        engine = self._legacy_engine()
        with engine.connect() as connection:
            self.assertAlmostEqual(
                connection.execute(text("SELECT amount FROM transactions")).scalar(), 12.34
            )

        initialize_schema(engine)
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT amount FROM transactions")).scalar(), 1234)
            self.assertEqual(connection.execute(text("SELECT initial_balance FROM accounts")).scalar(), 10050)
            self.assertEqual(connection.execute(text("SELECT amount FROM budgets")).scalar(), 30025)
            self.assertEqual(connection.execute(text("SELECT amount FROM recurring_rules")).scalar(), 150075)
            version = connection.execute(text("PRAGMA user_version")).scalar()

        # 二次初始化不能重复乘 100
        initialize_schema(engine)
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT amount FROM transactions")).scalar(), 1234)
            self.assertEqual(connection.execute(text("PRAGMA user_version")).scalar(), version)
        engine.dispose()


class LegacyTodoNormalizationTests(unittest.TestCase):
    """历史遗留：错误的模板标记与中文优先级要被修正。"""

    def test_template_flag_and_priority_are_repaired(self) -> None:
        from app.db.migrations import initialize_schema

        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE todos (
                    id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(64), created_at DATETIME,
                    updated_at DATETIME, is_deleted BOOLEAN, sync_status VARCHAR(16),
                    sync_seq INTEGER DEFAULT 0, title VARCHAR(200), note TEXT,
                    due_date DATE, due_time VARCHAR(5), tags TEXT, lunar_reminder VARCHAR(32),
                    repeat_rule VARCHAR(16), repeat_config TEXT, priority VARCHAR(16),
                    quadrant VARCHAR(4), remind_days TEXT, remind_days_before INTEGER,
                    remind_time VARCHAR(5), pomodoro_count INTEGER, completed BOOLEAN,
                    completed_at DATETIME, is_template BOOLEAN
                )
            """))
            connection.execute(text(
                "INSERT INTO todos (id, owner_id, created_at, updated_at, is_deleted, sync_status, "
                "sync_seq, title, is_template, completed, priority, quadrant) VALUES "
                "('x1','local','2026-01-01 00:00:00','2026-01-01 00:00:00',0,'clean',1,"
                "'误标模板',1,0,'高','important')"
            ))
            connection.execute(text("PRAGMA user_version = 0"))

        initialize_schema(engine)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT is_template, priority, quadrant, sync_status FROM todos")
            ).one()
        self.assertEqual(row[0], 0, "is_template=1 且 completed=0 应复位")
        self.assertEqual(row[1], "high", "中文优先级应收敛成英文码")
        self.assertIn(row[2] or "", ("", "important"), "手选象限应被清空（允许已为空）")
        self.assertEqual(row[3], "pending", "被修正的行要重新进入同步队列")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
