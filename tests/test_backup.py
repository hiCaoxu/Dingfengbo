"""本地备份：快照一致性与保留策略。"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from app.db.backup import backup_database, maybe_daily_backup


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-backup-"))
        self.db = self.root / "app.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.execute("INSERT INTO t VALUES (42)")
        self.dest = self.root / "backups"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_snapshot_is_readable_and_keeps_data(self) -> None:
        path = backup_database(db_path=self.db, dest_dir=self.dest, label="manual")
        self.assertTrue(path.exists())
        self.assertIn("-manual.db", path.name)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute("SELECT x FROM t").fetchall(), [(42,)])

    def test_retention_keeps_only_newest_copies(self) -> None:
        paths = [
            backup_database(db_path=self.db, dest_dir=self.dest, keep=2) for _ in range(4)
        ]
        remaining = {item.name for item in self.dest.glob("dingfengbo-*.db")}
        self.assertEqual(remaining, {paths[-1].name, paths[-2].name})

    def test_missing_source_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            backup_database(db_path=self.root / "nope.db", dest_dir=self.dest)

    def test_daily_backup_runs_once_per_day(self) -> None:
        with mock.patch("app.db.backup.get_setting", return_value=""), mock.patch(
            "app.db.backup.save_settings"
        ) as saver:
            first = maybe_daily_backup(
                db_path=self.db, dest_dir=self.dest, today=date(2026, 11, 21)
            )
            self.assertIsNotNone(first)
            saver.assert_called_once_with({"last_backup_date": "2026-11-21"})

        with mock.patch(
            "app.db.backup.get_setting", return_value="2026-11-21"
        ), mock.patch("app.db.backup.save_settings") as saver:
            self.assertIsNone(
                maybe_daily_backup(
                    db_path=self.db, dest_dir=self.dest, today=date(2026, 11, 21)
                )
            )
            saver.assert_not_called()

    def test_daily_backup_failure_does_not_raise(self) -> None:
        with mock.patch("app.db.backup.get_setting", return_value=""), mock.patch(
            "app.db.backup.save_settings"
        ) as saver:
            self.assertIsNone(
                maybe_daily_backup(
                    db_path=self.root / "missing.db",
                    dest_dir=self.dest,
                    today=date(2026, 11, 21),
                )
            )
            saver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
