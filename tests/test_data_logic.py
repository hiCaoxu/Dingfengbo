from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import Account, Note, Transaction
from app.db.repo import apply_remote, create, mark_clean, serialize, update
from app.services.finance import total_assets


class DataLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.Session()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_remote_owner_is_normalized_without_changing_identity(self) -> None:
        note = create(self.session, Note, title="本地", content="", tags="[]")
        remote = serialize(note)
        remote["data"].update(
            owner_id="token-user",
            title="远端更新",
            updated_at=(note.updated_at + timedelta(seconds=1)).isoformat(),
        )

        accepted = apply_remote(self.session, [remote], owner_id="local")

        self.assertEqual(len(accepted), 1)
        self.assertEqual(note.id, remote["data"]["id"])
        self.assertEqual(note.owner_id, "local")
        self.assertEqual(note.title, "远端更新")
        self.assertEqual(note.sync_status, "clean")

    def test_mark_clean_only_accepts_uploaded_version(self) -> None:
        note = create(self.session, Note, title="v1", content="", tags="[]")
        note.updated_at = datetime(2026, 1, 1)
        self.session.flush()
        uploaded = serialize(note)
        update(self.session, note, title="v2")
        edited_time = note.updated_at

        mark_clean(self.session, [uploaded])
        self.session.refresh(note)
        self.assertEqual(note.sync_status, "pending")
        self.assertEqual(note.title, "v2")

        mark_clean(self.session, [serialize(note)])
        self.session.refresh(note)
        self.assertEqual(note.sync_status, "clean")
        self.assertEqual(note.updated_at, edited_time)

    def test_transfer_between_uuid_accounts_preserves_assets(self) -> None:
        source = create(self.session, Account, name="A", initial_balance=1000)
        target = create(self.session, Account, name="B", initial_balance=0)
        create(
            self.session,
            Transaction,
            kind="transfer",
            amount=100,
            category="转账",
            account_id=source.id,
            to_account_id=target.id,
            date=date.today(),
            note="",
        )

        self.assertEqual(total_assets(self.session), 1000)
        self.assertNotEqual(source.id, target.id)


if __name__ == "__main__":
    unittest.main()
