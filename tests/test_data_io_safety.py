"""导入导出边界：未来时间戳钳制 + CSV 公式注入防护。"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.db.models import Account, Note, Transaction
from app.db.repo import create
from app.db.session import session_scope
from app.services.data_io import export_csv, import_json

from tests.helpers import reset_db


class FutureTimestampImportTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-io-"))

    def _payload(self, note_id: str, updated_at: str) -> dict:
        return {
            "tables": {
                "notes": [
                    {
                        "id": note_id,
                        "owner_id": "local",
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": updated_at,
                        "is_deleted": False,
                        "sync_status": "clean",
                        "sync_seq": 0,
                        "title": "导入的笔记",
                        "content": "",
                        "tags": "[]",
                    }
                ]
            }
        }

    def test_future_updated_at_is_clamped(self) -> None:
        far_future = (datetime.utcnow() + timedelta(days=3)).isoformat(timespec="seconds")
        path = self.root / "future.json"
        path.write_text(json.dumps(self._payload("note-future", far_future)), encoding="utf-8")

        stats = import_json(path)

        with session_scope() as session:
            note = session.get(Note, "note-future")
        self.assertIsNotNone(note)
        self.assertLess(note.updated_at, datetime.utcnow() + timedelta(minutes=6))
        self.assertTrue(
            any("在未来" in message for message in stats["errors"]),
            f"应提示钳制：{stats['errors']}",
        )

    def test_normal_timestamp_is_kept(self) -> None:
        recent = (datetime.utcnow() - timedelta(hours=1)).isoformat(timespec="seconds")
        path = self.root / "normal.json"
        path.write_text(json.dumps(self._payload("note-normal", recent)), encoding="utf-8")

        stats = import_json(path)

        with session_scope() as session:
            note = session.get(Note, "note-normal")
        self.assertEqual(note.updated_at.strftime("%Y-%m-%d %H:%M:%S"), recent.replace("T", " "))
        self.assertFalse(any("在未来" in message for message in stats["errors"]))


class CsvInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-csv-"))

    def _read_cells(self, path: Path) -> list[dict]:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_dangerous_text_is_escaped(self) -> None:
        payloads = [
            "=cmd|' /C calc'!A0",
            "+1+1",
            "-1+1",
            "@SUM(1,1)",
            " =2+2",       # 前导空白变体
            "\t=2+2",      # 制表符变体
            "\r=2+2",
            "\n=2+2",
        ]
        with session_scope() as session:
            for index, title in enumerate(payloads):
                create(session, Note, title=title, content="", tags="[]")
        path = export_csv("notes", self.root / "notes.csv")
        cells = [row["title"] for row in self._read_cells(path)]
        for raw, cell in zip(payloads, cells):
            self.assertTrue(
                cell.startswith("'"), f"{raw!r} 未加防护，导出为 {cell!r}"
            )
            self.assertEqual(cell[1:], raw, "防护只加前导单引号，不能改写原文")

    def test_negative_numbers_stay_numeric(self) -> None:
        with session_scope() as session:
            create(session, Account, name="信用卡", kind="信用卡", initial_balance=-1234.56)
            create(session, Transaction, kind="expense", amount=12.34, category="餐饮",
                   account_id="acc-1", date=datetime.utcnow().date(), note="-1+1")
        rows = self._read_cells(export_csv("accounts", self.root / "accounts.csv"))
        self.assertEqual(float(rows[0]["initial_balance"]), -1234.56)

        rows = self._read_cells(export_csv("transactions", self.root / "tx.csv"))
        self.assertEqual(float(rows[0]["amount"]), 12.34)
        self.assertTrue(rows[0]["note"].startswith("'"), "文本备注里的 -1+1 仍要防护")


if __name__ == "__main__":
    unittest.main()
