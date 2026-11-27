"""恢复流程的数据安全：替换失败不毁库、保留源备份、游标重置失败显式报错。"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import DEFAULT_DATA_DIR
from app.db import backup as backup_module
from tests.helpers import assert_disposable_database
from app.db.backup import (
    RestoreError,
    RestoreIncomplete,
    SyncCursorResetError,
    _atomic_replace,
    backup_database,
    reset_sync_cursors,
    restore_database,
)


class RestoreSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-restore-"))
        self.db = self.root / "app.db"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self._make_db(self.db, 42)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _make_db(path: Path, value: int) -> None:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.execute("INSERT INTO t VALUES (?)", (value,))

    @staticmethod
    def _value(path: Path) -> int:
        with sqlite3.connect(path) as conn:
            return int(conn.execute("SELECT x FROM t").fetchone()[0])

    def test_replace_failure_keeps_existing_database(self) -> None:
        """os.replace 失败时绝不退化为「原地覆盖复制」——宁可不恢复，也不能毁库。"""
        incoming = self.root / "incoming.db"
        self._make_db(incoming, 7)
        with mock.patch.object(backup_module.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(RestoreError):
                _atomic_replace(incoming, self.db)
        self.assertEqual(self._value(self.db), 42)
        self.assertTrue(incoming.exists(), "临时文件交给调用方清理，不能在这里删")

    def test_restore_failure_keeps_database_and_cleans_tmp(self) -> None:
        source = backup_database(db_path=self.db, dest_dir=self.backups, label="manual")
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE t SET x = 99")
        with mock.patch.object(backup_module.os, "replace", side_effect=OSError("denied")):
            with self.assertRaises(RestoreError):
                restore_database(source, db_path=self.db)
        self.assertEqual(self._value(self.db), 42, "替换失败后主库必须保持原样")
        self.assertFalse((self.root / "app.db.restore-tmp").exists(), "临时文件要清理")

    def test_restore_keeps_source_backup_with_keep_one(self) -> None:
        """backup_keep=1 且 source 就在备份目录时，恢复前的安全备份不能把 source 清掉。"""
        source = backup_database(db_path=self.db, dest_dir=self.backups, label="manual")
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE t SET x = 99")
        with mock.patch.object(backup_module, "configured_dir", return_value=self.backups), \
             mock.patch.object(backup_module, "configured_keep", return_value=1):
            restore_database(source, db_path=self.db)
        self.assertTrue(source.exists(), "正在恢复的源备份不能被保留策略顺手删除")
        self.assertEqual(self._value(self.db), 99)

    def test_cursor_reset_failure_is_explicit(self) -> None:
        with mock.patch.object(backup_module, "save_settings", side_effect=OSError("read-only")):
            with self.assertRaises(SyncCursorResetError):
                reset_sync_cursors()

    def test_restore_incomplete_when_cursor_reset_fails(self) -> None:
        source = backup_database(db_path=self.db, dest_dir=self.backups, label="manual")
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE t SET x = 99")
        with mock.patch.object(
            backup_module, "reset_sync_cursors", side_effect=SyncCursorResetError("boom")
        ):
            with self.assertRaises(RestoreIncomplete) as ctx:
                restore_database(source, db_path=self.db)
        # 库确实换成了备份内容，但调用方必须收到失败（不能提示成功、不能恢复自动同步）
        self.assertEqual(self._value(self.db), 99)
        self.assertIn("游标", str(ctx.exception))


class DisposableDatabaseGuardTests(unittest.TestCase):
    def test_real_data_dir_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_disposable_database(DEFAULT_DATA_DIR / "dingfengbo.db")

    def test_temp_dir_is_allowed(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="dfb-guard-"))
        try:
            assert_disposable_database(root / "dingfengbo.db")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
