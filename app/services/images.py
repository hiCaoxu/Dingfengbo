"""图片：本地压缩后上传到同步服务器，正文/封面只保留服务器链接。

为什么自己压：
- 手机相册单张常 3~8MB，直接传会让同步服务带宽和磁盘都吃不消；
- 记录里用到的图（封面、日记配图）显示尺寸都在千像素以内，压到长边 1600、
  JPEG q82 肉眼几乎无差，体积通常只剩 5%~15%。

上传走同步服务新增的 ``POST /api/upload``（Bearer 同步令牌鉴权），返回
``<server_url>/files/<摘要>.<ext>``；服务端按内容摘要命名，重复上传天然去重。
"""

from __future__ import annotations

import io
import ipaddress
import logging
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps

from ..config import DEFAULT_SERVER_URL, load_settings

logger = logging.getLogger(__name__)

MAX_EDGE = 1600  # 压缩后最长边（像素）
QUALITY = 82  # JPEG 质量
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
# 上传前的原始文件上限：压缩在内存里做，先拒绝明显不可能压到 12MB 的文件。
MAX_SOURCE_BYTES = 60 * 1024 * 1024
UPLOAD_TIMEOUT = 30.0
# 解压炸弹防线：先看像素数再 load()，避免一张几 MB 的高压缩比图片把内存吃光。
MAX_PIXELS = 40_000_000
_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class UploadError(RuntimeError):
    """上传失败：消息面向用户，可直接放进提示条。"""


def server_base() -> str:
    """当前配置的同步服务地址（去掉尾部斜杠）。"""
    settings = load_settings()
    return str(settings.get("server_url") or DEFAULT_SERVER_URL).strip().rstrip("/")


def _is_local_host(host: str) -> bool:
    """是否本机/内网地址（这类地址换台设备就访问不到）。"""
    name = (host or "").lower().strip("[]")
    if not name:
        return True
    if name in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or name.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def absolute_url(url: str, base: str | None = None) -> str:
    """把图片地址规范成「换台设备也能打开」的绝对地址。

    服务端未配置公网地址时会返回相对路径（``/files/x.jpg``），也可能因为反代/旧版本
    返回 ``http://127.0.0.1:8000/...``。这两种都只对当前机器有效，而记录会同步到
    其它设备——所以这里统一用**本机配置的同步服务地址**补全/替换主机部分。

    外部图床（https://img.example.com/...）保持原样：那是用户明确指定的外链。
    """
    text = (url or "").strip()
    if not text:
        return ""
    base = (base if base is not None else server_base()).rstrip("/")
    base_parts = urlsplit(base)
    if text.startswith(("http://", "https://")):
        parts = urlsplit(text)
        if _is_local_host(parts.hostname or "") and not _is_local_host(base_parts.hostname or ""):
            # 服务端报了本机地址（127.0.0.1 / 局域网 IP），但本机实际配置的是公网服务：
            # 换成公网地址，否则这条记录同步出去后别的设备打不开图。
            return urlunsplit((base_parts.scheme or "http", base_parts.netloc, parts.path,
                               parts.query, parts.fragment))
        return text
    if text.startswith("/"):
        return f"{base}{text}" if base else text
    if not base:
        return text
    return f"{base}/{text.lstrip('/')}"


def _filename_for(url: str, suffix: str) -> str:
    name = Path(urlsplit(url).path).name or "image"
    if "." not in name:
        name += suffix or ".jpg"
    return name


def _to_rgb(image: Image.Image) -> Image.Image:
    """把各种模式统一成白底 RGB（透明区域填白，避免 PNG 转 JPEG 变黑块）。"""
    if image.mode == "RGB":
        return image
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def compress(data: bytes) -> bytes:
    """压到长边 MAX_EDGE，转 JPEG；动图或无法识别时原样返回。

    像素超限必须**拒绝**而不是原样上传：压缩后的体积可能很小（大片纯色），
    但解码后上亿像素会同步到每台设备，打开即吃光内存（解压炸弹）。
    """
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        if width * height > MAX_PIXELS:
            logger.warning("图片像素过大（%dx%d），已拒绝", width, height)
            raise UploadError(f"图片尺寸过大（{width}×{height}），请先压缩或裁剪后再上传")
        image.load()
    except Image.DecompressionBombError as exc:
        logger.warning("图片触发解压炸弹保护，已拒绝")
        raise UploadError("图片尺寸异常（疑似解压炸弹），已拒绝") from exc
    except OSError:
        logger.info("图片无法识别，按原文件上传")
        return data
    except UploadError:
        raise
    except Exception:
        logger.info("图片无法识别，按原文件上传")
        return data
    if getattr(image, "is_animated", False):
        return data

    source = ImageOps.exif_transpose(image)
    converted = source.mode != "RGB"
    resized = max(source.size) > MAX_EDGE
    normalized = _to_rgb(source)
    if resized:
        normalized.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)

    buffer = io.BytesIO()
    normalized.save(buffer, format="JPEG", quality=QUALITY, optimize=True, progressive=True)
    compressed = buffer.getvalue()
    # 已缩过或做过模式转换（透明转白底）就必须用新图；否则本来就是小 JPEG，
    # 重新编码反而可能变大，这时保留原图。
    if converted or resized:
        return compressed
    return compressed if len(compressed) < len(data) else data


def upload_bytes(data: bytes, filename: str = "image.jpg") -> str:
    """把图片 POST 到同步服务器，返回可长期引用的 URL。"""
    settings = load_settings()
    base = str(settings.get("server_url") or DEFAULT_SERVER_URL).strip().rstrip("/")
    token = str(settings.get("sync_token") or "").strip()
    if not token:
        raise UploadError("未配置同步令牌，图片无法上传到服务器（设置 → 同步）")
    if not base:
        raise UploadError("未配置同步服务地址，图片无法上传")
    payload = compress(data)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise UploadError("图片压缩后仍超过 12MB，请换一张")
    try:
        with httpx.Client(timeout=UPLOAD_TIMEOUT) as client:
            response = client.post(
                f"{base}/api/upload",
                files={"file": (filename, payload, "image/jpeg")},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.RequestError as exc:
        raise UploadError(f"连接同步服务器失败：{exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise UploadError("同步令牌无效，图片上传被拒绝")
    if response.status_code == 404:
        raise UploadError("同步服务端版本过旧（缺少 /api/upload），请重新部署 server")
    if response.status_code == 413:
        raise UploadError("图片过大，服务器拒绝接收")
    if response.status_code >= 400:
        raise UploadError(f"上传失败（HTTP {response.status_code}）")
    try:
        url = str(response.json().get("url") or "")
    except ValueError as exc:
        raise UploadError("服务器返回内容无法解析") from exc
    if not url:
        raise UploadError("服务器没有返回图片链接")
    url = absolute_url(url, base)
    logger.info("图片已上传：%d → %d 字节", len(data), len(payload))
    return url


def upload_path(path: str | Path) -> str:
    """读取本地图片、压缩后上传；路径不存在或不是图片时报 :class:`UploadError`。"""
    file = Path(str(path))
    if not file.exists():
        raise UploadError("找不到选中的图片文件")
    if file.suffix.lower() not in _SUFFIXES:
        raise UploadError("只支持 JPG/PNG/GIF/WEBP/BMP 图片")
    try:
        # 先看原始大小：几个 GB 的“图片”直接读进内存会 OOM，压缩也救不回来。
        if file.stat().st_size > MAX_SOURCE_BYTES:
            raise UploadError(
                f"图片原始体积过大（{file.stat().st_size // (1024 * 1024)}MB），请先压缩后再上传"
            )
        data = file.read_bytes()
    except OSError as exc:
        raise UploadError("读取图片失败，请检查文件权限") from exc
    return upload_bytes(data, file.name)


def import_remote(source: str) -> str:
    """把网络图片转存到同步服务器，返回服务器链接。

    为什么要转存：外链图床会防盗链、会过期，而且同步到其它设备后未必还能访问；
    落一份到自己的同步服务上，封面才是稳定且跨设备可见的。

    下载复用 :mod:`covers` 那套带 SSRF 防护的实现（只允许公网地址、限大小、按魔数
    判断真图），失败时抛 :class:`UploadError` 且消息可直接展示。
    """
    from .covers import fetch_image_bytes  # 延迟导入：covers 也可能用到本模块

    text = absolute_url(source)
    if not text.lower().startswith(("http://", "https://")):
        raise UploadError("不是可访问的网络图片地址")
    try:
        data, suffix = fetch_image_bytes(text)
    except ValueError as exc:
        raise UploadError(str(exc) or "下载网络图片失败") from exc
    except Exception as exc:  # 网络/解析异常统一转成用户可读错误
        logger.warning("转存网络图片失败：%s", type(exc).__name__)
        raise UploadError("下载网络图片失败，请检查链接或稍后重试") from exc
    return upload_bytes(data, _filename_for(text, suffix))
