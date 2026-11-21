"""本地备份：快照一致性与保留策略。"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from app.db.backup import backup_database, maybe_daily_backup, restore_database


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


class RestoreResetsCursorsTests(unittest.TestCase):
    """回归：恢复备份后必须重置同步游标。

    游标是「已同步到服务端某序列号」的进度，库被换成另一份后游标还停在旧高位，
    pull 只会请求比游标更新的变更，云端更旧的记录永远拉不回来——2026-11-21 的
    「同步一直成功但数据全空」就是这条链路。
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-restore-"))
        self.backup = self.root / "dingfengbo-20261121-131418-519581-auto.db"
        with sqlite3.connect(self.backup) as conn:
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.execute("INSERT INTO t VALUES (7)")
        self.target = self.root / "app.db"
        with sqlite3.connect(self.target) as conn:
            conn.execute("CREATE TABLE t(x INTEGER)")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_restore(self):
        with mock.patch(
            "app.db.backup.backup_database", return_value=self.root / "safety.db"
        ), mock.patch("app.db.backup.save_settings") as saver, mock.patch(
            "app.db.session.engine", mock.MagicMock()
        ):
            payload = restore_database(self.backup, db_path=self.target)
        return payload, saver

    def test_restore_replaces_file_and_clears_cursors(self) -> None:
        payload, saver = self._run_restore()
        self.assertEqual(payload["restored"], self.target)
        with sqlite3.connect(self.target) as conn:
            self.assertEqual(conn.execute("SELECT x FROM t").fetchall(), [(7,)])
        saver.assert_called_once_with({"sync_cursors": {}})

    def test_invalid_backup_leaves_cursors_alone(self) -> None:
        broken = self.root / "broken.db"
        broken.write_bytes(b"not a database")
        with mock.patch("app.db.backup.save_settings") as saver:
            with self.assertRaises(ValueError):
                restore_database(broken, db_path=self.target)
        saver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
