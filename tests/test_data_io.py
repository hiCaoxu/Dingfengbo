"""数据导入导出：JSON 往返、CSV 往返、Markdown 落文件。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Todo, Transaction
from app.db.session import session_scope
from app.services.data_io import (
    counts,
    export_csv,
    export_json,
    export_markdown,
    export_pack,
    import_csv,
    import_json,
)

from tests.helpers import reset_db, seed


class JsonTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-io-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_export_then_import_roundtrip(self) -> None:
        target = export_json(self.root / "dump.json")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["app"], "dingfengbo")
        self.assertTrue(payload["tables"]["todos"])

        with session_scope() as session:
            for todo in session.scalars(select(Todo)):
                session.delete(todo)
        outcome = import_json(target)
        self.assertGreater(outcome["imported"], 0)
        with session_scope() as session:
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(Todo)), 3)

    def test_import_missing_tables_raises(self) -> None:
        path = self.root / "bad.json"
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_json(path)

    def test_counts_are_positive(self) -> None:
        snapshot = counts()
        self.assertGreater(snapshot["todos"], 0)
        self.assertGreater(snapshot["transactions"], 0)


class CsvTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-csv-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_transactions_roundtrip(self) -> None:
        path = export_csv("transactions", self.root / "tx.csv")
        self.assertTrue(path.exists())
        with session_scope() as session:
            for row in session.scalars(select(Transaction)):
                session.delete(row)
        outcome = import_csv("transactions", path)
        self.assertEqual(outcome["imported"], 1)
        with session_scope() as session:
            tx = session.scalar(select(Transaction))
            self.assertEqual(float(tx.amount), 32.5)
            self.assertEqual(tx.category, "餐饮")

    def test_todos_skip_blank_title(self) -> None:
        path = self.root / "todos.csv"
        path.write_text(
            "title,note,due_date,priority\n写文档,说明,2026-11-21,high\n,,,\n",
            encoding="utf-8",
        )
        outcome = import_csv("todos", path)
        self.assertEqual(outcome["imported"], 1)
        self.assertEqual(outcome["skipped"], 1)

    def test_unknown_table_raises(self) -> None:
        with self.assertRaises(ValueError):
            import_csv("nope", self.root / "x.csv")


class MarkdownTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()
        self.root = Path(tempfile.mkdtemp(prefix="dfb-md-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_markdown_files_created(self) -> None:
        files = export_markdown(self.root)
        names = {path.name for path in files}
        self.assertIn("任务.md", names)
        self.assertIn("收支.md", names)
        self.assertTrue(any(name.startswith("笔记-") for name in names))
        self.assertTrue(any(name.startswith("日记-") for name in names))

    def test_pack_writes_json_markdown_and_csv(self) -> None:
        result = export_pack(self.root)
        suffixes = {path.suffix for path in result["files"]}
        self.assertIn(".json", suffixes)
        self.assertIn(".md", suffixes)
        self.assertIn(".csv", suffixes)

    def test_markdown_contains_record_titles(self) -> None:
        files = export_markdown(self.root, ["todos"])
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("写周报", content)


if __name__ == "__main__":
    unittest.main()
