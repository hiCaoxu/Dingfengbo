"""索引迁移：本地表不该有同步复合索引，同步表必须齐全。"""

from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy import create_engine, text

from app.db.migrations import _COMPOSITE_INDEXES, initialize_schema
from app.db.models import SYNC_MODELS

LOCAL_TABLES = ("foods", "sync_sequences", "sync_issues", "sync_conflicts", "todo_reminders")


def _index_names(connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")')}


class IndexMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def _install_zombie_indexes(self) -> None:
        """复刻真实库里的常量表达式索引（带引号的列名命中 SQLite DQS）。"""
        with self.engine.begin() as connection:
            for table in LOCAL_TABLES:
                for template, first, second in _COMPOSITE_INDEXES:
                    connection.exec_driver_sql(
                        f'CREATE INDEX IF NOT EXISTS "{template % table}" '
                        f'ON "{table}" ("{first}", "{second}")'
                    )
            connection.exec_driver_sql("PRAGMA user_version = 5")

    def test_local_tables_have_no_sync_indexes(self) -> None:
        self._install_zombie_indexes()
        with self.engine.connect() as connection:
            # 僵尸索引确实已建出来（否则本测试没有意义）
            self.assertIn("ix_foods_owner_sync_seq", _index_names(connection, "foods"))

        initialize_schema(self.engine)

        with self.engine.connect() as connection:
            for table in LOCAL_TABLES:
                names = _index_names(connection, table)
                for template, _first, _second in _COMPOSITE_INDEXES:
                    self.assertNotIn(template % table, names, f"{table} 仍有同步索引")
            version = int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)
        self.assertGreaterEqual(version, 6)

    def test_sync_tables_have_both_composite_indexes(self) -> None:
        self._install_zombie_indexes()
        initialize_schema(self.engine)
        with self.engine.connect() as connection:
            for model in SYNC_MODELS:
                names = _index_names(connection, model.__tablename__)
                for template, _first, _second in _COMPOSITE_INDEXES:
                    self.assertIn(
                        template % model.__tablename__,
                        names,
                        f"{model.__tablename__} 缺少 {template % model.__tablename__}",
                    )

    def test_zombie_indexes_are_not_recreated(self) -> None:
        """清理后再跑一次初始化，不能又把索引建回去。"""
        self._install_zombie_indexes()
        initialize_schema(self.engine)
        initialize_schema(self.engine)
        with self.engine.connect() as connection:
            self.assertNotIn("ix_foods_owner_sync_seq", _index_names(connection, "foods"))


class ClientSequenceTests(unittest.TestCase):
    def test_client_init_does_not_assign_sequences(self) -> None:
        """客户端启动不逐行补号：sync_seq 由服务端统一分配。"""
        engine = create_engine("sqlite:///:memory:")
        initialize_schema(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO notes (id, owner_id, created_at, updated_at, is_deleted,"
                    " sync_status, sync_seq, title, content, tags)"
                    " VALUES ('n1', 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00',"
                    " 0, 'pending', 0, '标题', '', '[]')"
                )
            )
        initialize_schema(engine, assign_sequences=False)
        with engine.connect() as connection:
            self.assertEqual(
                connection.scalar(text("SELECT sync_seq FROM notes WHERE id = 'n1'")), 0
            )
        engine.dispose()

    def test_server_mode_still_assigns(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        initialize_schema(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO notes (id, owner_id, created_at, updated_at, is_deleted,"
                    " sync_status, sync_seq, title, content, tags)"
                    " VALUES ('n2', 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00',"
                    " 0, 'pending', 0, '标题', '', '[]')"
                )
            )
        initialize_schema(engine)
        with engine.connect() as connection:
            self.assertGreater(
                connection.scalar(text("SELECT sync_seq FROM notes WHERE id = 'n2'")), 0
            )
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
