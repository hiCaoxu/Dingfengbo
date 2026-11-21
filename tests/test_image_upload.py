"""图片压缩上传：压缩参数、失败提示、Markdown 图片块与预览渲染。"""

from __future__ import annotations

import io
import unittest
from unittest import mock

import flet as ft
from PIL import Image

from app.services import images
from app.services.markdown import parse
from app.ui import notes


def _png(size: tuple[int, int], mode: str = "RGB", color=(200, 60, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


class CompressTests(unittest.TestCase):
    def test_long_edge_is_limited_and_output_is_jpeg(self) -> None:
        data = _png((2400, 1200))
        payload = images.compress(data)
        self.assertTrue(payload.startswith(b"\xff\xd8\xff"))
        self.assertLessEqual(max(_open(payload).size), images.MAX_EDGE)
        self.assertLess(len(payload), len(data))

    def test_small_image_is_not_upscaled(self) -> None:
        data = _png((64, 32))
        self.assertEqual(_open(images.compress(data)).size, (64, 32))

    def test_transparent_png_gets_white_background(self) -> None:
        data = _png((40, 40), mode="RGBA", color=(0, 0, 0, 0))
        image = _open(images.compress(data)).convert("RGB")
        corner = image.getpixel((0, 0))
        self.assertTrue(all(channel > 240 for channel in corner), corner)

    def test_animated_gif_is_kept_as_is(self) -> None:
        buffer = io.BytesIO()
        # 三帧颜色不同，否则 Pillow 会把相同帧合并成单帧
        frames = [Image.new("RGB", (8, 8), (index * 60, 0, 0)) for index in range(3)]
        frames[0].save(
            buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0
        )
        data = buffer.getvalue()
        self.assertGreater(getattr(Image.open(io.BytesIO(data)), "n_frames", 1), 1)
        self.assertEqual(images.compress(data), data)

    def test_broken_bytes_pass_through(self) -> None:
        self.assertEqual(images.compress(b"not an image"), b"not an image")


class UploadTests(unittest.TestCase):
    def test_missing_token_raises_before_any_request(self) -> None:
        with mock.patch.object(images, "load_settings", return_value={"server_url": "http://x", "sync_token": ""}):
            with mock.patch.object(images.httpx, "Client") as client:
                with self.assertRaises(images.UploadError):
                    images.upload_bytes(_png((32, 32)))
        client.assert_not_called()

    def test_successful_upload_returns_server_url(self) -> None:
        response = mock.Mock(status_code=200, json=lambda: {"url": "http://h/files/a.jpg"})
        client = mock.Mock()
        client.__enter__ = mock.Mock(return_value=client)
        client.__exit__ = mock.Mock(return_value=False)
        client.post.return_value = response
        settings = {"server_url": "http://h/", "sync_token": "token"}
        with mock.patch.object(images, "load_settings", return_value=settings):
            with mock.patch.object(images.httpx, "Client", return_value=client):
                url = images.upload_bytes(_png((32, 32)), "a.png")
        self.assertEqual(url, "http://h/files/a.jpg")
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "http://h/api/upload")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer token"})

    def test_rejected_token_surfaces_friendly_message(self) -> None:
        response = mock.Mock(status_code=401)
        client = mock.Mock()
        client.__enter__ = mock.Mock(return_value=client)
        client.__exit__ = mock.Mock(return_value=False)
        client.post.return_value = response
        with mock.patch.object(images, "load_settings", return_value={"server_url": "http://h", "sync_token": "t"}):
            with mock.patch.object(images.httpx, "Client", return_value=client):
                with self.assertRaises(images.UploadError) as ctx:
                    images.upload_bytes(b"x")
        self.assertIn("令牌", str(ctx.exception))

    def test_upload_path_rejects_unsupported_suffix(self) -> None:
        with self.assertRaises(images.UploadError):
            images.upload_path("C:/nope/whatever.txt")


class MarkdownImageTests(unittest.TestCase):
    def test_image_block_is_parsed(self) -> None:
        blocks = parse("一段文字\n\n![封面](http://h/files/a.jpg)\n\n结尾")
        kinds = [block["type"] for block in blocks]
        self.assertIn("image", kinds)
        image = next(block for block in blocks if block["type"] == "image")
        self.assertEqual(image["src"], "http://h/files/a.jpg")
        self.assertEqual(image["alt"], "封面")

    def test_preview_renders_ft_image(self) -> None:
        control = notes.render_blocks(parse("![图](http://h/files/a.jpg)"))
        images_ = [c for c in control.controls if isinstance(c, ft.Image)]
        self.assertEqual(len(images_), 1)
        self.assertEqual(images_[0].src, "http://h/files/a.jpg")


if __name__ == "__main__":
    unittest.main()
