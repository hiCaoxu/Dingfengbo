"""服务端 P2：公开地址不信任 Host、静态文件 nosniff、上传临时文件不撞名。"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace

from tests.test_server_sync import _boot_server


def _close_log_handlers() -> None:
    """关闭日志文件句柄：Windows 下临时目录里有打开的文件就删不掉。"""
    import logging

    for handler in logging.getLogger().handlers[:]:
        handler.close()
        logging.getLogger().removeHandler(handler)


class PublicBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        _close_log_handlers()
        cls.temp_dir.cleanup()

    def _request(self, host: str, port: int = 8000):
        return SimpleNamespace(
            headers={"host": host},
            base_url=f"http://{host}/",
            url=SimpleNamespace(port=port),
        )

    def test_configured_public_url_wins(self) -> None:
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": "https://df.example"}):
            self.assertEqual(
                self.server._public_base(self._request("evil.example")), "https://df.example"
            )

    def test_untrusted_host_is_not_echoed(self) -> None:
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": ""}):
            base = self.server._public_base(self._request("evil.example"))
        self.assertNotIn("evil.example", base)

    def test_loopback_host_is_echoed(self) -> None:
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": ""}):
            base = self.server._public_base(self._request("127.0.0.1:8000"))
        self.assertIn("127.0.0.1", base)


class StaticFileHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        _close_log_handlers()
        cls.temp_dir.cleanup()

    def test_files_are_served_with_nosniff(self) -> None:
        from fastapi.testclient import TestClient

        path = self.server.FILES_DIR / "sample.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        # 最小 PNG 头，静态服务不校验内容
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        )
        client = TestClient(self.server.app)
        response = client.get("/files/sample.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertIn("immutable", response.headers.get("cache-control", ""))


class UploadTempNameTests(unittest.TestCase):
    def test_upload_uses_unique_temp_name(self) -> None:
        """上传逻辑不能再用固定的 ``<name>.tmp``（并发同摘要会互相顶掉）。"""
        import inspect

        from tests.test_server_sync import _boot_server as boot

        with tempfile.TemporaryDirectory() as tmp:
            server = boot(tmp)
            source = inspect.getsource(server.upload)
            self.assertIn("uuid4()", source)
            self.assertNotIn('".tmp"', source)
            server.engine.dispose()
            _close_log_handlers()


if __name__ == "__main__":
    unittest.main()
