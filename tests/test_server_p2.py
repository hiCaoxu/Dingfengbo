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
            url=SimpleNamespace(port=port, scheme="http"),
        )

    def test_configured_public_url_wins(self) -> None:
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": "https://df.example"}):
            self.assertEqual(
                self.server._public_base(self._request("evil.example")), "https://df.example"
            )

    def test_public_host_is_echoed(self) -> None:
        """公网 IP/域名必须原样回显：不回显就会生成 http://127.0.0.1:8000 这种
        「只有本机能打开」的图片地址（真实踩过的坑）。"""
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": ""}):
            base = self.server._public_base(self._request("43.142.92.115:8000"))
        self.assertEqual(base, "http://43.142.92.115:8000")

    def test_loopback_host_is_echoed(self) -> None:
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": ""}):
            base = self.server._public_base(self._request("127.0.0.1:8000"))
        self.assertEqual(base, "http://127.0.0.1:8000")

    def test_malformed_host_falls_back_to_relative(self) -> None:
        """冒号/空白/`user@` 这类非法 Host 不回显，返回相对地址由客户端补全。"""
        import os

        from unittest import mock

        with mock.patch.dict(os.environ, {"DINGFENGBO_SERVER_PUBLIC_URL": ""}):
            for host in ("bad host", "user@evil.com", "javascript:alert(1)", ""):
                self.assertEqual(self.server._public_base(self._request(host)), "", host)


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


class UploadSizeGuardTests(unittest.TestCase):
    """ASGI 层体积护栏：在 multipart 解析/落盘之前就拒绝超大请求体。

    旧实现只在 handler 里查 Content-Length，而 FastAPI 解析 UploadFile 时早已
    `await request.form()` 把整包 spool 到临时文件——磁盘先被写满才轮到检查。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = _boot_server(cls.temp_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.engine.dispose()
        _close_log_handlers()
        cls.temp_dir.cleanup()

    def _run(self, path: str, headers: list[tuple[bytes, bytes]], chunks: list[bytes]):
        """直接驱动中间件：返回 (start_message, body, app_called)。"""
        import asyncio

        called = {"value": False}

        async def app(scope, receive, send):
            called["value"] = True
            # 真实 handler 会先把请求体读干净（request.form()），这里照做，
            # 否则中间件的 receive 包装永远不会被调用。
            while True:
                message = await receive()
                if message.get("type") != "http.request" or not message.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        guard = self.server._UploadSizeGuard(app)
        sent: list[dict] = []
        queue = list(chunks)

        async def receive():
            if queue:
                return {"type": "http.request", "body": queue.pop(0), "more_body": bool(queue)}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "path": path, "headers": headers}
        asyncio.run(guard(scope, receive, send))
        start = next((m for m in sent if m["type"] == "http.response.start"), {})
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return start, body, called["value"]

    def test_declared_oversize_is_rejected_before_parsing(self) -> None:
        too_big = str(13 * 1024 * 1024).encode()
        start, body, called = self._run(
            "/api/upload", [(b"content-length", too_big)], [b"x"]
        )
        self.assertEqual(start.get("status"), 413)
        self.assertFalse(called, "超限请求不应进入应用（否则已经被 spool 落盘）")
        self.assertIn("12MB", body.decode("utf-8"))

    def test_chunked_oversize_is_rejected_midway(self) -> None:
        """不声明长度时会被读一部分，但必须在超过上限后立刻中断并回 413。"""
        start, body, called = self._run(
            "/api/upload", [], [b"x" * (7 * 1024 * 1024), b"x" * (7 * 1024 * 1024)]
        )
        self.assertEqual(start.get("status"), 413)
        self.assertIn("12MB", body.decode("utf-8"))
        # 应用被进入是正常的（multipart 解析从第一块就开始了）；关键是它没能读完。

    def test_other_paths_are_untouched(self) -> None:
        too_big = str(13 * 1024 * 1024).encode()
        start, body, called = self._run(
            "/api/health", [(b"content-length", too_big)], [b"{}"]
        )
        self.assertTrue(called)
        self.assertEqual(start.get("status"), 200)

    def test_sync_push_over_8mb_is_rejected(self) -> None:
        too_big = str(9 * 1024 * 1024).encode()
        start, body, called = self._run(
            "/api/sync/push", [(b"content-length", too_big)], [b"{}"]
        )
        self.assertEqual(start.get("status"), 413)
        self.assertFalse(called)
        self.assertIn("8MB", body.decode("utf-8"))
