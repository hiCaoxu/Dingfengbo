"""settings.json 读取缓存：保存失效、外部改写失效、缓存结果不外泄改动。"""

from __future__ import annotations

import json
import os
import unittest

from app import config


class SettingsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        config.save_settings({"last_export_dir": ""})
        self.original = config.load_settings().get("last_export_dir", "")

    def tearDown(self) -> None:
        config.save_settings({"last_export_dir": self.original})

    def test_save_invalidates_cache(self) -> None:
        config.save_settings({"last_export_dir": "D:/one"})
        self.assertEqual(config.load_settings()["last_export_dir"], "D:/one")
        config.save_settings({"last_export_dir": "D:/two"})
        self.assertEqual(config.load_settings()["last_export_dir"], "D:/two")

    def test_external_file_edit_is_picked_up(self) -> None:
        config.load_settings()  # 先让缓存热起来
        raw = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
        raw["last_export_dir"] = "D:/外部改写"
        config.SETTINGS_PATH.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 极端情况下写入与上一次读取落在同一时间戳精度内：显式推后 mtime
        stat = config.SETTINGS_PATH.stat()
        os.utime(config.SETTINGS_PATH, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual(config.load_settings()["last_export_dir"], "D:/外部改写")

    def test_cached_value_is_a_copy(self) -> None:
        first = config.load_settings()
        first["module_order"].append("help")
        first["last_export_dir"] = "D:/被就地修改"
        second = config.load_settings()
        self.assertNotIn("help", second["module_order"])
        self.assertNotEqual(second["last_export_dir"], "D:/被就地修改")


if __name__ == "__main__":
    unittest.main()
