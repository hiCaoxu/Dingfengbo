from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import Account, Note, Todo, Transaction
from app.db.repo import (
    apply_remote,
    create,
    mark_clean,
    mark_rejected,
    serialize,
    sync_queue_stats,
    update,
)
from app.services.finance import (
    account_balances,
    month_summary,
    six_month_trend,
    total_assets,
)


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

    def test_finance_summaries_stay_correct_after_sql_aggregation(self) -> None:
        account = create(self.session, Account, name="钱包", initial_balance=100)
        rows = [
            ("income", 300, "工资", date(2026, 6, 1)),
            ("expense", 80, "餐饮", date(2026, 6, 2)),
            ("expense", 20, "交通", date(2026, 6, 3)),
            ("expense", 50, "餐饮", date(2026, 3, 3)),
        ]
        for kind, amount, category, day in rows:
            create(
                self.session,
                Transaction,
                kind=kind,
                amount=amount,
                category=category,
                account_id=account.id,
                date=day,
                note="",
            )

        summary = month_summary(self.session, 2026, 6)
        self.assertEqual(summary["income"], 300)
        self.assertEqual(summary["expense"], 100)
        self.assertEqual(summary["balance"], 200)
        self.assertEqual(summary["categories"], {"餐饮": 80, "交通": 20})

        trend = six_month_trend(self.session, date(2026, 6, 15))
        self.assertEqual([item["label"] for item in trend], ["1月", "2月", "3月", "4月", "5月", "6月"])
        self.assertEqual(trend[-1]["income"], 300)
        self.assertEqual(trend[-1]["expense"], 100)
        self.assertEqual(trend[2]["expense"], 50)

        self.assertEqual(account_balances(self.session)[account.id], 250)

    def test_sync_queue_stats_splits_by_status_across_tables(self) -> None:
        note = create(self.session, Note, title="已同步", content="", tags="[]")
        todo = create(self.session, Todo, title="被拒")
        create(self.session, Todo, title="待同步")

        mark_clean(self.session, [serialize(note)])
        mark_rejected(self.session, [{"table": "todos", "id": todo.id}])
        stats = sync_queue_stats(self.session)

        self.assertEqual(stats["clean"], 1)
        self.assertEqual(stats["error"], 1)
        self.assertEqual(stats["pending"], 1)

    def test_rejected_record_leaves_pending_queue_until_edited(self) -> None:
        note = create(self.session, Note, title="坏记录", content="", tags="[]")
        rejected = [{"table": "notes", "id": note.id, "reason": "updated_at 格式错误"}]

        self.assertEqual(mark_rejected(self.session, rejected), 1)
        self.session.refresh(note)
        self.assertEqual(note.sync_status, "error")
        self.assertEqual(sync_queue_stats(self.session)["error"], 1)
        self.assertEqual(mark_rejected(self.session, rejected), 0)

        # 用户重新编辑后 mark_dirty 把记录复活为 pending，自愈路径天然存在。
        update(self.session, note, title="修好了")
        self.session.refresh(note)
        self.assertEqual(note.sync_status, "pending")


if __name__ == "__main__":
    unittest.main()
