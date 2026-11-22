from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

# ignore_cleanup_errors：进程退出时 SQLite 连接池可能还握着库文件句柄，
# Windows 下会删不掉并打出一大段 PermissionError（测试全过但看起来像失败）。
_TEST_DATA_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["DINGFENGBO_DATA_DIR"] = _TEST_DATA_DIR.name

from app.services.dates import parse_hours
from app.ui.forms import _date, open_quick_action


class FormLogicTests(unittest.TestCase):
    def test_invalid_date_is_not_replaced_with_default(self) -> None:
        default = date(2026, 1, 1)
        self.assertEqual(_date("", default), default)
        self.assertEqual(_date("2026-02-28", default), date(2026, 2, 28))
        with self.assertRaises(ValueError):
            _date("2026-02-30", default)

    def test_invalid_sleep_time_is_rejected(self) -> None:
        self.assertEqual(parse_hours("23:00", "07:00"), 8)
        self.assertEqual(parse_hours("25:00", "07:00"), 0)
        self.assertEqual(parse_hours("23:99", "07:00"), 0)

    def test_finance_quick_action_uses_common_signature(self) -> None:
        with patch("app.ui.forms.open_transaction_dialog") as open_dialog:
            open_quick_action(object(), "finance", None)
        open_dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
