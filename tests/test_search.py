"""全局搜索：自然语言过滤解析与跨模块检索。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.db.models import Note, Todo
from app.db.session import session_scope
from app.services.search import flatten, parse_query, search

from tests.helpers import reset_db, seed


class ParseQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 11, 21)

    def test_module_date_and_priority(self) -> None:
        query = parse_query("待办 今天 高优先级 报告", today=self.today)
        self.assertEqual(query.tables, ["todos"])
        self.assertEqual(query.priority, "high")
        self.assertEqual(query.keywords, ["报告"])
        self.assertEqual(query.since, self.today)
        self.assertEqual(query.until, self.today)

    def test_multiple_modules(self) -> None:
        query = parse_query("笔记 日记 AI", today=self.today)
        self.assertEqual(query.tables, ["notes", "diaries"])
        self.assertEqual(query.keywords, ["AI"])

    def test_unknown_tokens_stay_keywords(self) -> None:
        query = parse_query("体检报告", today=self.today)
        self.assertEqual(query.tables, [])
        self.assertEqual(query.keywords, ["体检报告"])
        self.assertTrue(query.is_empty is False)

    def test_empty_query(self) -> None:
        self.assertTrue(parse_query("", today=self.today).is_empty)


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()

    def test_groups_by_module(self) -> None:
        grouped = search("周报", today=date.today())
        self.assertIn("todos", grouped)
        self.assertTrue(any("周报" in hit.title for hit in grouped["todos"]))

    def test_searches_note_content(self) -> None:
        grouped = search("大模型", today=date.today())
        self.assertIn("notes", grouped)
        self.assertIn("大模型", grouped["notes"][0].snippet + grouped["notes"][0].title)

    def test_priority_filter_without_keyword(self) -> None:
        grouped = search("待办 高优先级", today=date.today())
        self.assertIn("todos", grouped)
        self.assertTrue(all(hit.payload.priority == "high" for hit in grouped["todos"]))

    def test_date_filter_limits_results(self) -> None:
        today = date.today()
        with session_scope() as session:
            session.add_all(
                [
                    Todo(title="去年体检", due_date=today - timedelta(days=400)),
                    Todo(title="今天体检", due_date=today),
                ]
            )
        grouped = search("待办 今天 体检", today=today)
        titles = [hit.title for hit in grouped.get("todos", [])]
        self.assertIn("今天体检", titles)
        self.assertNotIn("去年体检", titles)

    def test_flatten_sorts_by_score(self) -> None:
        grouped = search("体检", today=date.today())
        hits = flatten(grouped)
        self.assertEqual(hits, sorted(hits, key=lambda hit: -hit.score))


class SearchDataKindsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()

    def test_finds_diary_transaction_and_entertainment(self) -> None:
        self.assertIn("diaries", search("状态", today=date.today()))
        finance = search("餐饮", today=date.today())
        self.assertIn("transactions", finance)
        self.assertIn("餐饮", finance["transactions"][0].title)
        self.assertIn("entertainments", search("电影", today=date.today()))

    def test_special_characters_are_escaped(self) -> None:
        with session_scope() as session:
            session.add(Note(title="100% 完成", content="进度 100% 的笔记", tags="[]"))
            session.add(Note(title="其他笔记", content="无关", tags="[]"))
        grouped = search("100%", today=date.today())
        titles = [hit.title for hit in grouped.get("notes", [])]
        self.assertEqual(titles, ["100% 完成"])


if __name__ == "__main__":
    unittest.main()
