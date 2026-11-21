"""统一时间轴、跨模块标签与记录关联。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.services.timeline import (
    collect,
    create_link,
    delete_link,
    link_targets,
    links_for,
    tag_counts,
)

from tests.helpers import reset_db, seed


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def test_collects_multiple_modules(self) -> None:
        today = date.today()
        items = collect(today - timedelta(days=7), today)
        modules = {item.module for item in items}
        for module in ("todo", "diary", "notes", "finance", "fun", "habit"):
            self.assertIn(module, modules)

    def test_filter_by_module(self) -> None:
        today = date.today()
        items = collect(today - timedelta(days=7), today, modules=["finance"])
        self.assertTrue(items)
        self.assertTrue(all(item.module == "finance" for item in items))

    def test_filter_by_tag(self) -> None:
        today = date.today()
        items = collect(today - timedelta(days=7), today, tag="健康")
        self.assertEqual([item.title for item in items], ["体检预约"])

    def test_sorted_desc_by_date(self) -> None:
        today = date.today()
        items = collect(today - timedelta(days=7), today)
        self.assertEqual([item.day for item in items], sorted((item.day for item in items), reverse=True))

    def test_range_excludes_old_records(self) -> None:
        today = date.today()
        items = collect(today, today, modules=["diary"])
        self.assertTrue(all(item.day == today for item in items))


class TagTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()

    def test_tag_counts_merge_sources(self) -> None:
        tags = {tag: (count, sources) for tag, count, sources in tag_counts(days=30)}
        self.assertIn("工作", tags)
        self.assertIn("待办", tags["工作"][1])
        self.assertIn("笔记", tags["工作"][1])
        self.assertIn("AI", tags)

    def test_empty_database_returns_empty(self) -> None:
        reset_db()
        self.assertEqual(tag_counts(days=1), [])


class LinkTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def test_create_and_read_links(self) -> None:
        link_id = create_link(
            "notes", self.ids["note_id"], "todos", self.ids["todo_id"], "笔记"
        )
        self.assertIsNotNone(link_id)
        links = links_for("notes", self.ids["note_id"])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["table"], "todos")
        self.assertEqual(links[0]["title"], "写周报")
        # 反向查询也应命中
        self.assertEqual(len(links_for("todos", self.ids["todo_id"])), 1)

    def test_duplicate_link_rejected(self) -> None:
        first = create_link("notes", self.ids["note_id"], "todos", self.ids["todo_id"])
        second = create_link("notes", self.ids["note_id"], "todos", self.ids["todo_id"])
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_delete_link(self) -> None:
        link_id = create_link("notes", self.ids["note_id"], "todos", self.ids["todo_id"])
        delete_link(link_id)
        self.assertEqual(links_for("notes", self.ids["note_id"]), [])

    def test_link_targets_search(self) -> None:
        targets = link_targets("周报")
        self.assertTrue(any(row["title"] == "写周报" for row in targets))


if __name__ == "__main__":
    unittest.main()
