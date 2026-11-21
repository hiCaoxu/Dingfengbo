"""AI 草稿写回：JSON 解析、校验、落库与撤销；任务提示词。"""

from __future__ import annotations

import json
import unittest
from datetime import date

from sqlalchemy import func, select

from app.db.models import Note, Todo, Transaction
from app.db.session import session_scope
from app.services.ai.drafts import Draft, extract, save, strip_json_blocks, undo
from app.services.ai.tasks import TASKS, build_request

from tests.helpers import reset_db, seed


class ExtractTests(unittest.TestCase):
    def test_parses_fenced_json(self) -> None:
        text = (
            "下面是草稿：\n```json\n"
            '{"drafts": [{"type": "todo", "title": "交周报", "due_date": "2026-11-22",'
            ' "priority": "high"}]}\n```'
        )
        drafts = extract(text, today=date(2026, 11, 21))
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertTrue(draft.valid)
        self.assertEqual(draft.table, "todos")
        self.assertEqual(draft.fields["title"], "交周报")
        self.assertEqual(draft.fields["due_date"], date(2026, 11, 22))
        self.assertEqual(draft.fields["priority"], "high")

    def test_relative_dates(self) -> None:
        text = '{"drafts": [{"type": "todo", "title": "跑步", "due_date": "明天"}]}'
        draft = extract(text, today=date(2026, 11, 21))[0]
        self.assertEqual(draft.fields["due_date"], date(2026, 11, 22))

    def test_invalid_items_are_kept_with_reason(self) -> None:
        text = '{"drafts": [{"type": "todo"}, {"type": "mystery"}]}'
        drafts = extract(text)
        self.assertEqual(len(drafts), 1)  # 不支持的类型直接丢弃，缺字段的保留并标注原因
        self.assertFalse(drafts[0].valid)

    def test_missing_title_is_invalid(self) -> None:
        text = '{"drafts": [{"type": "todo", "note": "无标题"}]}'
        draft = extract(text)[0]
        self.assertFalse(draft.valid)
        self.assertIn("标题", draft.reason)

    def test_transaction_requires_amount(self) -> None:
        bad = extract('{"drafts": [{"type": "transaction", "amount": 0}]}')[0]
        self.assertFalse(bad.valid)
        good = extract(
            '{"drafts": [{"type": "transaction", "amount": 32.5, "category": "餐饮", "date": "2026-11-21"}]}'
        )[0]
        self.assertTrue(good.valid)
        self.assertEqual(good.fields["amount"], 32.5)

    def test_note_and_entertainment(self) -> None:
        text = json.dumps(
            {
                "drafts": [
                    {"type": "note", "title": "读书笔记", "content": "内容", "tags": ["阅读"]},
                    {"type": "entertainment", "title": "沙丘", "kind": "电影"},
                ]
            },
            ensure_ascii=False,
        )
        drafts = extract(text)
        self.assertEqual(len(drafts), 2)
        self.assertIn("阅读", drafts[0].fields["tags"])
        self.assertEqual(drafts[1].fields["status"], "想看")

    def test_strip_json_blocks(self) -> None:
        text = "说明文字\n```json\n{\"drafts\": []}\n```\n结尾"
        stripped = strip_json_blocks(text)
        self.assertNotIn("```", stripped)
        self.assertIn("说明文字", stripped)


class SaveUndoTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def test_save_then_undo(self) -> None:
        drafts = [
            Draft("todo", {"title": "AI 待办", "note": "", "due_date": None,
                           "due_time": "", "priority": "", "tags": "[]", "completed": False}),
            Draft("note", {"title": "AI 生成笔记", "content": "内容", "tags": "[]"}),
            Draft("transaction", {"kind": "expense", "amount": 12.0, "category": "餐饮",
                                  "date": date.today(), "note": "咖啡", "account_id": ""}),
        ]
        saved = save(drafts)
        self.assertEqual(len(saved), 3)
        with session_scope() as session:
            self.assertGreaterEqual(
                session.scalar(select(func.count()).select_from(Todo)), 4
            )
            self.assertGreaterEqual(
                session.scalar(select(func.count()).select_from(Note)), 2
            )
            tx = session.scalar(
                select(Transaction).where(Transaction.note == "咖啡")
            )
            self.assertIsNotNone(tx)
            self.assertEqual(tx.account_id, self.ids["account_id"])

        removed = undo(saved)
        self.assertEqual(removed, 3)
        with session_scope() as session:
            # 撤销走软删除（保留恢复窗口，并同步删除状态到其他设备）
            self.assertTrue(
                session.scalar(select(Todo).where(Todo.title == "AI 待办")).is_deleted
            )
            self.assertTrue(
                session.scalar(select(Note).where(Note.title == "AI 生成笔记")).is_deleted
            )

    def test_invalid_drafts_are_skipped(self) -> None:
        saved = save([Draft("todo", raw={}, reason="缺少标题")])
        self.assertEqual(saved, [])


class TaskRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        seed()

    def test_all_tasks_build_requests(self) -> None:
        for task in TASKS:
            request = build_request(task.key)
            self.assertTrue(request.content.strip())
            self.assertIn("定风波", request.system)

    def test_draft_tasks_use_json_system_prompt(self) -> None:
        request = build_request("breakdown")
        self.assertTrue(request.draft)
        self.assertIn("json", request.system)
        self.assertEqual(request.refs, ["todos"])

    def test_report_task_includes_local_stats(self) -> None:
        request = build_request("weekly_report")
        self.assertIn("回顾", request.content)
        self.assertIn("待办", request.content)

    def test_unknown_task_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_request("nope")


if __name__ == "__main__":
    unittest.main()
