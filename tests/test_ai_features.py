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

class ProviderPayloadTests(unittest.TestCase):
    """OpenAI 兼容 Provider：带上 max_tokens，且复用同一个 httpx.Client。"""

    def setUp(self) -> None:
        from app.services.ai import openai_compat as mod

        self.mod = mod
        mod._CLIENT = None
        mod._CLIENT_TIMEOUT = None

    def tearDown(self) -> None:
        client = self.mod._CLIENT
        self.mod._CLIENT = None
        self.mod._CLIENT_TIMEOUT = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def _fake_client_factory(self, captured: list, created: list):
        from unittest import mock

        def factory(timeout=None, **kwargs):
            created.append(timeout)
            client = mock.Mock()

            def post(url, headers=None, json=None):
                captured.append(json)
                response = mock.Mock()
                response.raise_for_status = lambda: None
                response.json = lambda: {"choices": [{"message": {"content": "好的"}}]}
                return response

            client.post = post
            return client

        return factory

    def test_payload_has_max_tokens_and_client_is_reused(self) -> None:
        from unittest import mock

        captured: list[dict] = []
        created: list = []
        provider = self.mod.OpenAICompatProvider("https://api.test/v1", "key", "model")
        with mock.patch.object(
            self.mod.httpx, "Client", side_effect=self._fake_client_factory(captured, created)
        ):
            self.assertEqual(provider.chat([{"role": "user", "content": "一"}]), "好的")
            self.assertEqual(provider.chat([{"role": "user", "content": "二"}]), "好的")

        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0]["max_tokens"], self.mod.MAX_TOKENS)
        self.assertEqual(len(created), 1, "连续两次提问应复用同一个连接池客户端")
