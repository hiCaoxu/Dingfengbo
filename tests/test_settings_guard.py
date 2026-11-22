"""设置文件损坏时禁止回写：后台保存不能把整份配置覆盖成默认值。"""

from __future__ import annotations

import json
import unittest

from app import config


class SettingsGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = config.SETTINGS_PATH
        self._saved = self.path.read_text(encoding="utf-8") if self.path.exists() else None
        config._SETTINGS_CACHE = None
        config._LOAD_FAILED = False

    def tearDown(self) -> None:
        if self._saved is None:
            self.path.unlink(missing_ok=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self._saved, encoding="utf-8")
        config._SETTINGS_CACHE = None
        config._LOAD_FAILED = False

    def test_unreadable_settings_refuse_to_save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ broken json", encoding="utf-8")
        config._SETTINGS_CACHE = None

        config.load_settings()  # 回退默认值，但不允许回写

        with self.assertRaises(config.SettingsUnreadable):
            config.save_settings({"sync_cursors": {}})
        self.assertEqual(
            self.path.read_text(encoding="utf-8"), "{ broken json", "原文件必须原样保留"
        )

    def test_missing_file_allows_save(self) -> None:
        self.path.unlink(missing_ok=True)
        config._SETTINGS_CACHE = None
        config.save_settings({"last_backup_date": "2026-11-22"})
        self.assertIn("2026-11-22", self.path.read_text(encoding="utf-8"))

    def test_valid_file_saves_normally(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"theme_mode": "dark"}), encoding="utf-8")
        config._SETTINGS_CACHE = None
        config.save_settings({"theme_mode": "light"})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["theme_mode"], "light")

    def test_non_object_top_level_counts_as_broken(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        config._SETTINGS_CACHE = None
        config.load_settings()
        with self.assertRaises(config.SettingsUnreadable):
            config.save_settings({"theme_mode": "light"})


if __name__ == "__main__":
    unittest.main()
