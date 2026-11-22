"""日记唯一约束：全表 UNIQUE → 仅活跃行唯一，删除后同日可重建。"""

from __future__ import annotations

import unittest
from datetime import date

from app.db.migrations import initialize_schema
from app.db.models import Diary
from app.db.repo import create, soft_delete
from app.db.session import engine, session_scope
from app.services.dates import diary_title
from app.services.diary import ensure_diary

from tests.helpers import reset_db

_OLD_SCHEMA = """
CREATE TABLE diaries (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    sync_status VARCHAR(16) NOT NULL,
    sync_seq INTEGER NOT NULL,
    date DATE NOT NULL,
    title VARCHAR(120) NOT NULL,
    content TEXT,
    UNIQUE (owner_id, date)
)
"""

_OLD_SCHEMA_NO_UNIQUE = """
CREATE TABLE diaries (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_id VARCHAR(36) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    sync_status VARCHAR(16) NOT NULL,
    sync_seq INTEGER NOT NULL,
    date DATE NOT NULL,
    title VARCHAR(120) NOT NULL,
    content TEXT
)
"""


class DiaryUniqueMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def _downgrade_to_full_unique(self) -> None:
        """把库退成「全表唯一约束 + 旧命名索引」的真实旧 schema。

        必须连索引一起还原：SQLite 的 RENAME 会把索引名字带走，迁移若不清旧索引，
        新建表时 CREATE INDEX 会撞名（真实库升级路径踩到过）。
        """
        with engine.begin() as connection:
            connection.exec_driver_sql('DROP INDEX IF EXISTS "uq_diaries_owner_date_active"')
            connection.exec_driver_sql('DROP TABLE "diaries"')
            connection.exec_driver_sql(_OLD_SCHEMA)
            for ddl in (
                'CREATE INDEX "ix_diaries_date" ON "diaries"(date)',
                'CREATE INDEX "ix_diaries_updated_at" ON "diaries"(updated_at)',
                'CREATE INDEX "ix_diaries_owner_id" ON "diaries"(owner_id)',
                'CREATE INDEX "ix_diaries_owner_sync_seq" ON "diaries"(owner_id, sync_seq)',
            ):
                connection.exec_driver_sql(ddl)
            # 旧约束下同一 (owner, date) 只能有一行，先建一行活跃 + 一行墓碑，
            # 再现造两条重复活跃行是不可能的（约束挡住），因此用不同 date 的墓碑验证共存。
            for rid, is_deleted, stamp in (
                ("diary-old", 0, "2026-03-01 10:00:00"),
                ("diary-tomb", 1, "2026-03-02 09:00:00"),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO diaries (id, owner_id, created_at, updated_at, is_deleted,"
                    " sync_status, sync_seq, date, title, content) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rid, "local", stamp, stamp, is_deleted, "clean", 1,
                     "2026-03-01" if rid == "diary-old" else "2026-03-02", "标题", ""),
                )
            connection.exec_driver_sql("PRAGMA user_version = 4")

    def test_full_unique_constraint_is_replaced(self) -> None:
        self._downgrade_to_full_unique()
        initialize_schema(engine)
        with engine.connect() as connection:
            indexes = {
                row[1]: {"unique": int(row[2]), "origin": str(row[3]), "partial": int(row[4])}
                for row in connection.exec_driver_sql('PRAGMA index_list("diaries")')
            }
        self.assertIn("uq_diaries_owner_date_active", indexes)
        self.assertEqual(indexes["uq_diaries_owner_date_active"]["partial"], 1)
        self.assertFalse(
            any(item["origin"] == "u" and item["unique"] for item in indexes.values()),
            "旧的全表 UNIQUE 约束必须被移除",
        )

    def test_duplicate_active_rows_are_collapsed(self) -> None:
        """重建前必须先把重复活跃行收敛成一行，否则新唯一索引建不起来。

        真实旧库有全表唯一约束（不可能有重复），这里直接构造一份无约束的同构表，
        验证去重 SQL 本身的语义：保留 updated_at 最新的那一行，其余转墓碑。
        """
        from app.db.migrations import _DEDUPE_ACTIVE_DIARIES

        with engine.begin() as connection:
            connection.exec_driver_sql('DROP INDEX IF EXISTS "uq_diaries_owner_date_active"')
            connection.exec_driver_sql('DROP TABLE "diaries"')
            connection.exec_driver_sql(_OLD_SCHEMA_NO_UNIQUE)
            for rid, stamp in (("dup-a", "2026-04-01 08:00:00"), ("dup-b", "2026-04-01 09:00:00")):
                connection.exec_driver_sql(
                    "INSERT INTO diaries (id, owner_id, created_at, updated_at, is_deleted,"
                    " sync_status, sync_seq, date, title, content) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rid, "local", stamp, stamp, 0, "pending", 2, "2026-04-01", "重复", ""),
                )

        with engine.begin() as connection:
            connection.exec_driver_sql(_DEDUPE_ACTIVE_DIARIES)
        with session_scope() as session:
            rows = list(session.query(Diary).filter(Diary.date == date(2026, 4, 1)))
        active = [row for row in rows if not row.is_deleted]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].id, "dup-b", "保留 updated_at 最新的那一行")

    def test_delete_then_recreate_same_day(self) -> None:
        with session_scope() as session:
            diary = create(session, Diary, date=date(2026, 5, 1), title="第一篇", content="正文")
            diary_id = diary.id
        with session_scope() as session:
            soft_delete(session, session.get(Diary, diary_id))
        # 墓碑还在，同一天重建必须成功（旧全表约束下这里会 IntegrityError）
        with session_scope() as session:
            rebuilt = ensure_diary(session, date(2026, 5, 1))
            self.assertFalse(rebuilt.is_deleted)
            self.assertEqual(rebuilt.title, diary_title(date(2026, 5, 1)))
            self.assertEqual(rebuilt.sync_status, "pending")

    def test_ensure_diary_reuses_tombstone(self) -> None:
        with session_scope() as session:
            diary = create(session, Diary, date=date(2026, 6, 1), title="旧标题", content="旧正文")
            diary_id = diary.id
        with session_scope() as session:
            soft_delete(session, session.get(Diary, diary_id))
        with session_scope() as session:
            again = ensure_diary(session, date(2026, 6, 1))
            self.assertEqual(again.id, diary_id, "应复用墓碑而不是新建一行")
            self.assertEqual(again.content, "")


if __name__ == "__main__":
    unittest.main()
