"""敏感字段落盘保护：DPAPI 优先，base64 必须显式允许、损坏值不崩溃。"""

from __future__ import annotations

import base64
import unittest

from app import config
from app.config import (
    InsufficientSecretProtection,
    _deobfuscate_token,
    _obfuscate_token,
)


class SecretStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dpapi = config._DPAPI_AVAILABLE

    def tearDown(self) -> None:
        config._DPAPI_AVAILABLE = self._dpapi

    def test_roundtrip_does_not_store_plaintext(self) -> None:
        token = "sync-token-abc-123"
        stored = _obfuscate_token(token, allow_weak=True)
        self.assertNotIn(token, stored)
        self.assertTrue(stored.startswith(("dpapi:", "b64:")))
        self.assertEqual(_deobfuscate_token(stored), token)

    def test_refuses_weak_storage_when_dpapi_missing(self) -> None:
        """没有 DPAPI 时默认拒绝保存：base64 等同明文，不该静默落盘。"""
        config._DPAPI_AVAILABLE = False
        with self.assertRaises(InsufficientSecretProtection):
            _obfuscate_token("sync-token")

    def test_allows_weak_storage_when_explicitly_opted_in(self) -> None:
        config._DPAPI_AVAILABLE = False
        stored = _obfuscate_token("sync-token", allow_weak=True)
        self.assertTrue(stored.startswith("b64:"))
        self.assertEqual(_deobfuscate_token(stored), "sync-token")

    def test_legacy_base64_value_still_readable(self) -> None:
        legacy = "b64:" + base64.urlsafe_b64encode(b"old-token").decode("ascii")
        self.assertEqual(_deobfuscate_token(legacy), "old-token")

    def test_plaintext_value_passes_through(self) -> None:
        self.assertEqual(_deobfuscate_token("raw-token"), "raw-token")

    def test_non_utf8_base64_payload_is_returned_as_is(self) -> None:
        # base64 解码成功但不是合法 UTF-8：保留原值，让用户能看出配置已损坏
        payload = base64.urlsafe_b64encode(b"\xff\xfe").decode("ascii")
        self.assertEqual(_deobfuscate_token("b64:" + payload), "b64:" + payload)

    def test_undecryptable_dpapi_value_degrades_to_empty(self) -> None:
        """换了 Windows 用户/机器后无法解密：按「未配置」处理，而不是把密文塞回输入框。"""
        self.assertEqual(_deobfuscate_token("dpapi:***"), "")


if __name__ == "__main__":
    unittest.main()
