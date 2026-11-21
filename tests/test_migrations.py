from __future__ import annotations

import unittest
from uuid import UUID

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema, sequence_value
from app.db.models import Account, Note, Transaction


class SyncSequenceInitializationTests(unittest.TestCase):
    """升级存量库时全局序列必须顶到存量最大值，否则存量客户端永久漏拉。"""

    INSERT_NOTE = """
        INSERT INTO notes (id, owner_id, created_at, updated_at, is_deleted,
                           sync_status, sync_seq, title, content, tags)
        VALUES (:id, 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00',
                0, :status, :seq, :title, '', '[]')
    """

    def test_counter_is_anchored_to_existing_rows(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        initialize_schema(engine)
        with engine.begin() as connection:
            connection.execute(
                text(self.INSERT_NOTE),
                {"id": "legacy", "status": "clean", "seq": 150, "title": "存量笔记"},
            )
            # 模拟升级场景：计数器被重置为 0，而存量行带着旧的表内序列号。
            connection.execute(text("UPDATE sync_sequences SET value = 0 WHERE id = 1"))

        initialize_schema(engine)
        with engine.connect() as connection:
            self.assertEqual(sequence_value(connection), 150)

        engine.dispose()

    def test_missing_sequence_rows_are_allocated_above_existing_max(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        initialize_schema(engine)
        with engine.begin() as connection:
            connection.execute(
                text(self.INSERT_NOTE),
                {"id": "legacy", "status": "clean", "seq": 150, "title": "存量笔记"},
            )
            connection.execute(
                text(self.INSERT_NOTE),
                {"id": "missing", "status": "pending", "seq": 0, "title": "缺号笔记"},
            )
            connection.execute(text("UPDATE sync_sequences SET value = 0 WHERE id = 1"))

        initialize_schema(engine)
        with engine.connect() as connection:
            allocated = connection.scalar(
                text("SELECT sync_seq FROM notes WHERE id = 'missing'")
            )
            self.assertGreater(allocated, 150)
            self.assertEqual(sequence_value(connection), allocated)

        engine.dispose()



class UuidMigrationTests(unittest.TestCase):
    def test_legacy_ids_and_transaction_references_are_migrated(self) -> None:
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
                CREATE TABLE notes (
                    id INTEGER PRIMARY KEY, owner_id VARCHAR(64), created_at DATETIME,
                    updated_at DATETIME, is_deleted BOOLEAN, sync_status VARCHAR(16),
                    title VARCHAR(200), content TEXT, tags TEXT
                )
            """))
            connection.execute(text("CREATE INDEX ix_accounts_owner_id ON accounts(owner_id)"))
            connection.execute(text("""
                INSERT INTO accounts VALUES
                    (1, 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 0, 'pending', 'A', '现金', 1000),
                    (2, 'local', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 0, 'pending', 'B', '现金', 0)
            """))
            connection.execute(text("""
                INSERT INTO transactions VALUES
                    (1, 'local', '2026-01-02 00:00:00', '2026-01-02 00:00:00', 0, 'pending',
                     'transfer', 100, '转账', 2, 1, '2026-01-02', '')
            """))
            connection.execute(text("""
                INSERT INTO notes VALUES
                    (1, 'local', '2026-01-02 00:00:00', '2026-01-02 00:00:00', 0, 'pending',
                     '旧笔记', '内容', '[]')
            """))

        initialize_schema(engine)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            accounts = {account.name: account for account in session.scalars(select(Account))}
            transaction = session.scalar(select(Transaction))
            note = session.scalar(select(Note))
            self.assertIsNotNone(transaction)
            self.assertIsNotNone(note)
            self.assertEqual(transaction.account_id, accounts["B"].id)
            self.assertEqual(transaction.to_account_id, accounts["A"].id)
            self.assertEqual(note.title, "旧笔记")
            UUID(note.id)
            UUID(transaction.id)
            for account in accounts.values():
                UUID(account.id)
            migrated_note_id = note.id

        initialize_schema(engine)
        with Session() as session:
            self.assertEqual(session.scalar(select(Note)).id, migrated_note_id)
        self.assertFalse(any(name.startswith("_legacy_uuid_") for name in inspect(engine).get_table_names()))
        engine.dispose()

    def test_deterministic_uuid_keeps_synced_record_identity_across_devices(self) -> None:
        def legacy_note_engine():
            local = create_engine("sqlite:///:memory:")
            with local.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE notes (
                        id INTEGER PRIMARY KEY, owner_id VARCHAR(64), created_at DATETIME,
                        updated_at DATETIME, is_deleted BOOLEAN, sync_status VARCHAR(16),
                        title VARCHAR(200), content TEXT, tags TEXT
                    )
                """))
                connection.execute(text("""
                    INSERT INTO notes VALUES
                        (1, 'local', '2026-01-02 00:00:00', '2026-01-02 00:00:00', 0, 'pending',
                         '已同步', '内容', '[]')
                """))
            initialize_schema(local)
            return local

        engine_a = legacy_note_engine()
        engine_b = legacy_note_engine()
        Session = sessionmaker(bind=engine_a)
        with Session() as session:
            note_a = session.scalar(select(Note))
        Session = sessionmaker(bind=engine_b)
        with Session() as session:
            note_b = session.scalar(select(Note))

        self.assertEqual(note_a.id, note_b.id)
        UUID(note_a.id)
        engine_a.dispose()
        engine_b.dispose()


if __name__ == "__main__":
    unittest.main()
