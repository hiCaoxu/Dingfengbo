"""历史云端库「列可为 NULL」的修复：迁移补 NOT NULL + 同步入站兜底。"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn

from app.db.migrations import initialize_schema
from app.db.models import Todo, column_fill_value
from app.db.repo import apply_remote


def _permissive_ddl(model) -> str:
    """按模型列生成「全列可空」的建表语句，模拟更早版本创建的云端库。"""
    dialect = sqlite_dialect.dialect()
    columns = ", ".join(
        str(CreateColumn(column).compile(dialect=dialect)).replace(" NOT NULL", "")
        for column in model.__table__.columns
    )
    return f'CREATE TABLE "{model.__tablename__}" ({columns})'


class NullableColumnMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_nullable_columns_are_rebuilt_to_not_null(self) -> None:
        initialize_schema(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE todos")
            connection.exec_driver_sql(_permissive_ddl(Todo))
            connection.exec_driver_sql(
                """
                INSERT INTO todos (id, owner_id, created_at, updated_at, is_deleted,
                                   sync_status, sync_seq, title, note, due_time, tags,
                                   pomodoro_count, completed)
                VALUES ('t1', 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 0,
                        'pending', 0, '旧任务', NULL, NULL, NULL, NULL, 0)
                """
            )
            connection.exec_driver_sql("PRAGMA user_version = 6")

        initialize_schema(self.engine)

        with self.engine.connect() as connection:
            columns = {row[1]: row for row in connection.exec_driver_sql("PRAGMA table_info('todos')")}
            for name in ("note", "due_time", "tags", "pomodoro_count", "title"):
                self.assertEqual(columns[name][3], 1, f"{name} 应为 NOT NULL")
            self.assertEqual(
                int(connection.exec_driver_sql("PRAGMA user_version").scalar()), 7
            )
            row = connection.exec_driver_sql(
                "SELECT title, note, due_time, tags, pomodoro_count FROM todos"
            ).one()
            self.assertEqual(row[0], "旧任务", "原有数据必须保留")
            self.assertEqual(row[1], "")
            self.assertEqual(row[2], "")
            self.assertEqual(row[3], "[]")
            self.assertEqual(row[4], 0)
            # 重建表会把复合索引带走，迁移后必须补回来
            indexes = {item["name"] for item in inspect(connection).get_indexes("todos")}
            self.assertIn("ix_todos_owner_sync_seq", indexes)
            self.assertIn("ix_todos_due_date", indexes)

    def test_fresh_database_is_marked_current(self) -> None:
        initialize_schema(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(
                int(connection.exec_driver_sql("PRAGMA user_version").scalar()), 7
            )

    def test_defaults_are_type_appropriate(self) -> None:
        self.assertEqual(column_fill_value(Todo.__table__.columns["note"]), "")
        self.assertEqual(column_fill_value(Todo.__table__.columns["pomodoro_count"]), 0)
        self.assertEqual(column_fill_value(Todo.__table__.columns["completed"]), False)


class RemoteNullPayloadTests(unittest.TestCase):
    """云端把 NULL 发下来时，本地非空列要兜底而不是 IntegrityError。"""

    def setUp(self) -> None:
        from tests.helpers import reset_db

        reset_db()

    def test_null_fields_are_replaced_with_defaults(self) -> None:
        from app.db.session import session_scope

        payload = {
            "table": "todos",
            "data": {
                "id": "todo-null-1",
                "owner_id": "local",
                "title": "云端旧数据",
                "note": None,
                "tags": None,
                "due_time": None,
                "priority": None,
                "quadrant": None,
                "remind_days": None,
                "remind_days_before": None,
                "remind_time": None,
                "pomodoro_count": None,
                "repeat_rule": None,
                "repeat_config": None,
                "lunar_reminder": None,
                "is_template": None,
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "sync_status": "clean",
                "sync_seq": 5,
            },
        }
        with session_scope() as session:
            applied = apply_remote(session, [payload])
        self.assertEqual(applied[0]["record_id"], "todo-null-1")

        with session_scope() as session:
            todo = session.get(Todo, "todo-null-1")
            self.assertIsNotNone(todo)
            self.assertEqual(todo.note, "")
            self.assertEqual(todo.tags, "[]")
            self.assertEqual(todo.priority, "")
            self.assertEqual(todo.pomodoro_count, 0)


if __name__ == "__main__":
    unittest.main()
