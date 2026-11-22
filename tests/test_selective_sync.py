"""选择性同步语义：None=全部、空集=一个都不选（旧实现两者混用）。"""

from __future__ import annotations

import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.services.sync import sync_client
from tests.test_server_sync import _boot_server, note_change


class SelectedTablesTests(unittest.TestCase):
    def test_default_means_all(self) -> None:
        self.assertIsNone(sync_client.selected_tables({}))
        self.assertIsNone(sync_client.selected_tables({"sync_all_tables": True}))

    def test_legacy_config_means_all(self) -> None:
        """老配置文件没有 sync_all_tables，只有旧的 sync_tables=[]，必须仍表示全部。"""
        self.assertIsNone(sync_client.selected_tables({"sync_tables": []}))

    def test_empty_selection_means_nothing(self) -> None:
        self.assertEqual(
            sync_client.selected_tables({"sync_all_tables": False, "sync_tables": []}),
            set(),
        )

    def test_explicit_list(self) -> None:
        self.assertEqual(
            sync_client.selected_tables(
                {"sync_all_tables": False, "sync_tables": ["notes", "todos", ""]}
            ),
            {"notes", "todos"},
        )

    def test_bad_type_falls_back_to_all(self) -> None:
        self.assertIsNone(
            sync_client.selected_tables(
                {"sync_all_tables": False, "sync_tables": "notes"}
            )
        )


class ServerEmptyWhitelistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        initialize_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._original = self.server.SessionLocal
        self.server.SessionLocal = self.Session

    def tearDown(self) -> None:
        self.server.SessionLocal = self._original
        self.engine.dispose()

    def test_empty_whitelist_returns_nothing(self) -> None:
        self.server.push(
            self.server.PushRequest(changes=[note_change("笔记", __import__("datetime").datetime(2026, 3, 1))]),
            "Bearer empty-token",
        )
        payload = self.server.pull(
            self.server.PullRequest(tables=[]), "Bearer empty-token"
        )
        self.assertEqual(payload["changes"], [], "一个都不勾选时应返回空页")
        self.assertEqual(payload["cursors"], {})
        self.assertFalse(payload["has_more"])


if __name__ == "__main__":
    unittest.main()
