"""敏感字段落盘保护：DPAPI 优先、base64 兼容、损坏值不崩溃。"""

from __future__ import annotations

import base64
import unittest

from app.config import _deobfuscate_token, _obfuscate_token


class SecretStorageTests(unittest.TestCase):
    def test_roundtrip_does_not_store_plaintext(self) -> None:
        token = "sync-token-abc-123"
        stored = _obfuscate_token(token)
        self.assertNotIn(token, stored)
        self.assertTrue(stored.startswith(("dpapi:", "b64:")))
        self.assertEqual(_deobfuscate_token(stored), token)

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
