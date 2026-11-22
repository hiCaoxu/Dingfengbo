"""娱乐封面：地址清洗、本地缓存与失败记忆。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services import covers


class NormalizeTests(unittest.TestCase):
    def test_strips_wrapping_quotes_from_explorer_copy(self) -> None:
        raw = '"C:\\Users\\hicao\\Pictures\\封面.jpg"'
        self.assertEqual(
            covers.normalize(raw), r"C:\Users\hicao\Pictures\封面.jpg"
        )

    def test_handles_file_uri_and_protocol_relative(self) -> None:
        self.assertTrue(covers.normalize("file:///C:/a/b.jpg").endswith("a\\b.jpg"))
        self.assertEqual(
            covers.normalize("//cdn.example.com/a.jpg"), "https://cdn.example.com/a.jpg"
        )
        self.assertEqual(
            covers.normalize("www.example.com/a.jpg"), "https://www.example.com/a.jpg"
        )

    def test_blank_and_plain_text(self) -> None:
        self.assertEqual(covers.normalize("   "), "")
        self.assertEqual(covers.normalize("  随便写的  "), "随便写的")


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-covers-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_local_file(self) -> None:
        image = self.root / "cover.png"
        image.write_bytes(b"png")
        self.assertEqual(covers.resolve_cover(str(image)), str(image))

    def test_missing_local_file_returns_empty(self) -> None:
        self.assertEqual(covers.resolve_cover(str(self.root / "nope.jpg")), "")

    def test_empty_source(self) -> None:
        self.assertEqual(covers.resolve_cover(""), "")


class _FakeResponse:
    status_code = 200
    content = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    headers = {"content-type": "image/png"}

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield self.content


class _FakeClient:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        type(self).calls += 1

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def stream(self, method, url, headers=None):  # noqa: ARG002
        return _FakeResponse()


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dfb-cover-cache-"))
        covers._failed.clear()
        _FakeClient.calls = 0

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        covers._failed.clear()

    def test_downloads_once_then_hits_cache(self) -> None:
        url = "https://example.com/cover.jpg"
        with mock.patch.object(covers.httpx, "Client", _FakeClient):
            first = covers.cached_cover(url, self.root)
            self.assertIsNotNone(first)
            self.assertTrue(first.exists())
            calls = _FakeClient.calls

            second = covers.cached_cover(url, self.root)
            self.assertEqual(second, first)
            self.assertEqual(_FakeClient.calls, calls)  # 命中缓存，不再请求

    def test_failure_is_remembered(self) -> None:
        class _Boom(_FakeClient):
            def stream(self, method, url, headers=None):  # noqa: ARG002
                raise RuntimeError("网络不可达")

        url = "https://example.com/broken.jpg"
        with mock.patch.object(covers.httpx, "Client", _Boom):
            self.assertIsNone(covers.cached_cover(url, self.root))
            calls = _Boom.calls
            self.assertIsNone(covers.cached_cover(url, self.root))
            self.assertEqual(_Boom.calls, calls)  # 失败后被记忆，不再重试

    def test_prefetch_ignores_non_urls(self) -> None:
        covers.prefetch(["", "C:\\local.jpg", "   "])  # 不应抛异常


if __name__ == "__main__":
    unittest.main()
