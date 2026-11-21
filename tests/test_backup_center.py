"""备份中心的列表、校验、恢复与完整数据导出。"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from app.db.backup import (
    backup_database,
    export_full_data,
    human_size,
    list_backups,
    restore_database,
    verify_backup,
)


class BackupCenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-center-"))
        self.db = self.root / "app.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
        self.dest = self.root / "backups"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_list_backups_parses_metadata(self) -> None:
        path = backup_database(db_path=self.db, dest_dir=self.dest, keep=0, label="manual")
        items = list_backups(self.dest)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["path"], path)
        self.assertEqual(item["label"], "manual")
        self.assertGreater(item["size"], 0)
        self.assertIsInstance(item["created"], type(item["created"]))

    def test_list_backups_missing_dir_returns_empty(self) -> None:
        self.assertEqual(list_backups(self.root / "nope"), [])

    def test_rapid_backups_never_collide(self) -> None:
        # Windows 的 datetime.now() 分辨率可能只有毫秒级：连续备份不能互相覆盖。
        paths = [
            backup_database(db_path=self.db, dest_dir=self.dest, keep=0) for _ in range(20)
        ]
        names = {path.name for path in paths}
        self.assertEqual(len(names), 20)
        self.assertEqual(len(list_backups(self.dest)), 20)

    def test_verify_detects_valid_file(self) -> None:
        path = backup_database(db_path=self.db, dest_dir=self.dest, keep=0)
        ok, message = verify_backup(path)
        self.assertTrue(ok)
        self.assertIn("校验通过", message)

    def test_verify_missing_file(self) -> None:
        ok, message = verify_backup(self.root / "missing.db")
        self.assertFalse(ok)
        self.assertIn("不存在", message)

    def test_verify_corrupted_file(self) -> None:
        broken = self.root / "broken.db"
        broken.write_bytes(b"not a sqlite database")
        ok, _message = verify_backup(broken)
        self.assertFalse(ok)

    def test_restore_replaces_database_and_keeps_safety_copy(self) -> None:
        path = backup_database(db_path=self.db, dest_dir=self.dest, keep=0, label="manual")
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO t VALUES (2)")
        result = restore_database(path, db_path=self.db)
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("SELECT x FROM t").fetchall()
        self.assertEqual(rows, [(1,)])
        self.assertIsNotNone(result["safety"])
        self.assertTrue(Path(result["safety"]).exists())

    def test_restore_rejects_bad_backup(self) -> None:
        broken = self.root / "broken.db"
        broken.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            restore_database(broken, db_path=self.db)

    def test_export_full_data_contains_db_and_settings(self) -> None:
        from app.config import save_settings

        save_settings({"user_name": "测试"})
        target = export_full_data(self.root / "export.zip", db_path=self.db)
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
        self.assertIn("dingfengbo.db", names)
        self.assertIn("settings.json", names)

    def test_human_size(self) -> None:
        self.assertEqual(human_size(512), "512B")
        self.assertIn("KB", human_size(2048))
        self.assertTrue(human_size(5 * 1024 * 1024).endswith("MB"))


if __name__ == "__main__":
    unittest.main()
