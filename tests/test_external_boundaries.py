"""外部数据与网络边界：草稿账户校验、时间格式、AI 地址校验、图片 SSRF 过滤。"""

from __future__ import annotations

import unittest
from datetime import date

import flet as ft

from app.db.models import Account, Transaction
from app.db.session import session_scope
from app.services.ai.client import AIConfigError, _validate_base_url
from app.services.ai.drafts import Draft, _parse_time, save
from app.services.covers import is_safe_remote_url
from app.ui.notes import render_blocks

from tests.helpers import reset_db


class DraftTimeTests(unittest.TestCase):
    def test_valid_times_are_normalized(self) -> None:
        self.assertEqual(_parse_time("9:05"), "09:05")
        self.assertEqual(_parse_time("23:59"), "23:59")

    def test_invalid_times_are_cleared(self) -> None:
        for raw in ("下午3点", "25:00", "12:60", "12:3", "2026-01-01T09:00", ""):
            self.assertEqual(_parse_time(raw), "", raw)


class DraftAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def _transaction_draft(self, account_id: str) -> Draft:
        # valid 是「没有 reason」的派生属性，不能当构造参数传
        return Draft(
            "transaction",
            fields={
                "kind": "expense",
                "amount": 12.5,
                "category": "餐饮",
                "account_id": account_id,
                "date": date.today(),
                "note": "",
            },
            raw={},
        )

    def test_unknown_account_is_rejected(self) -> None:
        draft = self._transaction_draft("no-such-account")
        saved = save([draft])
        self.assertEqual(saved, [])
        self.assertIn("账户不存在", draft.reason)
        with session_scope() as session:
            self.assertEqual(session.query(Transaction).count(), 0)

    def test_deleted_account_is_rejected(self) -> None:
        from app.db.repo import create, soft_delete

        with session_scope() as session:
            account = create(session, Account, name="旧卡", kind="银行卡")
            account_id = account.id
            soft_delete(session, account)
        draft = self._transaction_draft(account_id)
        saved = save([draft])
        self.assertEqual(saved, [])
        self.assertIn("账户不存在", draft.reason)

    def test_existing_account_is_used(self) -> None:
        from app.db.repo import create

        with session_scope() as session:
            account = create(session, Account, name="现金", kind="银行卡")
            account_id = account.id
        draft = self._transaction_draft(account_id)
        saved = save([draft])
        self.assertEqual(len(saved), 1)
        with session_scope() as session:
            tx = session.query(Transaction).one()
        self.assertEqual(tx.account_id, account_id)


class AIBaseUrlTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self) -> None:
        for raw in ("ftp://example.com", "file:///etc/passwd", "example.com/v1"):
            with self.assertRaises(AIConfigError, msg=raw):
                _validate_base_url(raw)

    def test_accepts_https(self) -> None:
        self.assertEqual(_validate_base_url("https://api.deepseek.com/v1"),
                         "https://api.deepseek.com/v1")

    def test_private_host_is_logged_but_allowed(self) -> None:
        with self.assertLogs("app.services.ai.client", level="WARNING") as logs:
            _validate_base_url("http://127.0.0.1:8080/v1")
        self.assertTrue(any("内网" in line for line in logs.output))


class MarkdownImageSafetyTests(unittest.TestCase):
    def test_unsafe_image_is_blocked(self) -> None:
        blocks = [{"type": "image", "src": "http://127.0.0.1/secret.png", "alt": "内网图"}]
        control = render_blocks(blocks)
        texts = []

        def walk(node):
            if isinstance(node, ft.Text):
                texts.append(node.value or "")
            for name in ("controls", "content"):
                value = getattr(node, name, None)
                if isinstance(value, list):
                    for child in value:
                        walk(child)
                elif value is not None:
                    walk(value)

        walk(control)
        self.assertTrue(any("已拦截" in text for text in texts), texts)

    def test_public_image_renders(self) -> None:
        blocks = [{"type": "image", "src": "https://example.com/a.png", "alt": "图"}]
        control = render_blocks(blocks)
        self.assertIsNotNone(control)


class CoverFailClosedTests(unittest.TestCase):
    def test_loopback_and_private_are_blocked(self) -> None:
        self.assertFalse(is_safe_remote_url("http://127.0.0.1/a.png"))
        self.assertFalse(is_safe_remote_url("http://192.168.1.10/a.png"))
        self.assertFalse(is_safe_remote_url("file:///etc/passwd"))

    def test_unresolvable_host_is_blocked(self) -> None:
        """DNS 解析失败必须 fail-closed，否则等于绕过内网校验。"""
        self.assertFalse(is_safe_remote_url("http://no-such-host.invalid/a.png"))


if __name__ == "__main__":
    unittest.main()
