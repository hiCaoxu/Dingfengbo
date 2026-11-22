"""图片地址规范化：跨设备可访问、网络图片转存。

背景（真实故障）：服务端曾把上传结果拼成 ``http://127.0.0.1:8000/files/…``，
这条记录同步到别的设备后封面全是灰图。修法分三层：
1. 服务端回显客户端拨号用的 Host（只剩格式白名单）；
2. 客户端再用「本机配置的同步服务地址」兜底补全（老版本服务端也能救）；
3. 网络图片可转存到自己的同步服务器，避免外链图床防盗链/过期。
"""

from __future__ import annotations

import unittest
from unittest import mock

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class AbsoluteUrlTests(unittest.TestCase):
    def test_relative_path_uses_server_base(self) -> None:
        from app.services.images import absolute_url

        self.assertEqual(
            absolute_url("/files/a.jpg", "http://43.142.92.115:8000"),
            "http://43.142.92.115:8000/files/a.jpg",
        )

    def test_loopback_is_replaced_when_server_is_public(self) -> None:
        from app.services.images import absolute_url

        self.assertEqual(
            absolute_url("http://127.0.0.1:8000/files/a.jpg", "http://43.142.92.115:8000"),
            "http://43.142.92.115:8000/files/a.jpg",
        )
        self.assertEqual(
            absolute_url("http://localhost:8000/files/a.jpg", "http://43.142.92.115:8000"),
            "http://43.142.92.115:8000/files/a.jpg",
        )
        self.assertEqual(
            absolute_url("http://192.168.1.9:8000/files/a.jpg", "http://43.142.92.115:8000"),
            "http://43.142.92.115:8000/files/a.jpg",
        )

    def test_loopback_is_kept_when_server_is_also_local(self) -> None:
        from app.services.images import absolute_url

        self.assertEqual(
            absolute_url("http://127.0.0.1:8000/files/a.jpg", "http://127.0.0.1:8000"),
            "http://127.0.0.1:8000/files/a.jpg",
        )

    def test_public_url_is_untouched(self) -> None:
        from app.services.images import absolute_url

        url = "http://43.142.92.115:8000/files/a.jpg"
        self.assertEqual(absolute_url(url, "http://43.142.92.115:8000"), url)

    def test_foreign_host_is_kept(self) -> None:
        from app.services.images import absolute_url

        url = "https://img.example.com/poster.jpg"
        self.assertEqual(absolute_url(url, "http://43.142.92.115:8000"), url)

    def test_empty(self) -> None:
        from app.services.images import absolute_url

        self.assertEqual(absolute_url("", "http://43.142.92.115:8000"), "")


class ImportRemoteTests(unittest.TestCase):
    """网络图片转存：下载（复用 covers 的 SSRF 防护）→ 上传 → 返回服务器地址。"""

    def test_success_uploads_downloaded_bytes(self) -> None:
        from app.services import images

        captured = {}

        def fake_fetch(url, timeout=10.0):
            captured["url"] = url
            return PNG, ".png"

        def fake_upload(data, filename="image.jpg"):
            captured["bytes"] = data
            captured["filename"] = filename
            return "http://43.142.92.115:8000/files/abc.png"

        with mock.patch("app.services.covers.fetch_image_bytes", fake_fetch), mock.patch.object(
            images, "upload_bytes", fake_upload
        ), mock.patch.object(images, "load_settings", lambda: {"server_url": "http://43.142.92.115:8000"}):
            result = images.import_remote("https://img.example.com/poster.png")

        self.assertEqual(result, "http://43.142.92.115:8000/files/abc.png")
        self.assertEqual(captured["bytes"], PNG)
        self.assertEqual(captured["filename"], "poster.png")

    def test_download_failure_becomes_user_message(self) -> None:
        from app.services import images

        def boom(url, timeout=10.0):
            raise ValueError("链接内容不是图片（JPG/PNG/GIF/WEBP/BMP）")

        with mock.patch("app.services.covers.fetch_image_bytes", boom), mock.patch.object(
            images, "load_settings", lambda: {"server_url": "http://43.142.92.115:8000"}
        ):
            with self.assertRaises(images.UploadError) as ctx:
                images.import_remote("https://img.example.com/a.html")
        self.assertIn("不是图片", str(ctx.exception))

    def test_non_http_source_is_rejected(self) -> None:
        from app.services import images

        with mock.patch.object(
            images, "load_settings", lambda: {"server_url": "http://43.142.92.115:8000"}
        ):
            with self.assertRaises(images.UploadError):
                images.import_remote("C:/pics/a.jpg")


class FetchImageBytesTests(unittest.TestCase):
    """covers.fetch_image_bytes：只放行真图片。"""

    def test_non_image_payload_is_rejected(self) -> None:
        from app.services import covers

        class _Response:
            status_code = 200

            def __init__(self) -> None:
                # httpx 的 headers 是属性（不是方法），这里要照抄这个形态
                self.headers = {"content-type": "text/html"}

            def raise_for_status(self) -> None:
                return None

            def iter_bytes(self):
                yield b"<html>blocked</html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def stream(self, *a, **k):
                return _Response()

        with mock.patch.object(covers.httpx, "Client", _Client), mock.patch.object(
            covers, "is_safe_remote_url", lambda url: True
        ):
            with self.assertRaises(ValueError) as ctx:
                covers.fetch_image_bytes("https://img.example.com/a.html")
        self.assertIn("不是图片", str(ctx.exception))

    def test_private_address_is_rejected_before_any_request(self) -> None:
        from app.services import covers

        with self.assertRaises(ValueError) as ctx:
            covers.fetch_image_bytes("http://127.0.0.1:8000/files/a.png")
        self.assertIn("内网", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
