from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import Todo
from app.services.ai.context import TABLES, TABLE_BY_LABEL, parse_refs, serialize_row


class ParseRefsTests(unittest.TestCase):
    def test_parses_known_table_label(self) -> None:
        self.assertEqual(parse_refs("@待办 帮我分解"), ["todos"])

    def test_parses_multiple_and_dedups(self) -> None:
        self.assertEqual(parse_refs("@待办 @笔记 @待办"), ["todos", "notes"])

    def test_ignores_unknown_labels(self) -> None:
        self.assertEqual(parse_refs("@不知道 @待办"), ["todos"])

    def test_no_refs_returns_empty(self) -> None:
        self.assertEqual(parse_refs("随便聊聊"), [])


class TablesTests(unittest.TestCase):
    def test_tables_have_unique_names_and_labels(self) -> None:
        names = [n for n, _l, _m in TABLES]
        labels = [l for _n, l, _m in TABLES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertGreaterEqual(len(TABLES), 10)

    def test_label_maps_to_name(self) -> None:
        self.assertEqual(TABLE_BY_LABEL["待办"], "todos")
        self.assertEqual(TABLE_BY_LABEL["收支"], "transactions")


class SerializeRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_serialize_skips_sync_metadata_and_includes_business_fields(self) -> None:
        with self.Session() as session:
            todo = Todo(title="买菜", priority="high", note="周末", completed=False)
            session.add(todo)
            session.flush()
            text = serialize_row(todo)
        self.assertIn("标题=买菜", text)
        self.assertIn("优先级=high", text)
        # 同步元数据字段应被跳过
        self.assertNotIn("sync_status", text)
        self.assertNotIn("owner_id", text)


if __name__ == "__main__":
    unittest.main()
