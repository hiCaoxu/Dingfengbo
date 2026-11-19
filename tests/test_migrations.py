from __future__ import annotations

import unittest
from uuid import UUID

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import Account, Note, Transaction


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
