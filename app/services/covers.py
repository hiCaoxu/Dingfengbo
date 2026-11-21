"""娱乐封面：地址清洗 + 网络图片本地缓存。

Flet 的 ``Image`` 走 Flutter 默认 UA 直接请求网络，豆瓣等图床有防盗链会返回 403，
封面就渲染不出来；另外从资源管理器「复制文件地址」得到的是带引号的路径，
``Path(...).exists()`` 必然失败。这里统一处理：

1. :func:`normalize` 清洗地址（去首尾引号、``file://``、协议相对 ``//``、``www.`` 补 scheme）；
2. 网络图片由 Python 带 UA/Referer 下载到 ``DATA_DIR/covers`` 缓存，再用本地文件渲染，
   既绕过防盗链，也让封面可离线查看；
3. 下载失败按 URL 记忆 10 分钟，避免每次刷新都卡在超时上。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

COVERS_DIR: Path = DATA_DIR / "covers"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Dingfengbo/1.0"
)
_MAX_BYTES = 8 * 1024 * 1024
_RETRY_AFTER = 600  # 秒：失败后这段时间内不再重试
# 失败记录：封面是在工作线程里并发下载的，读写都要加锁；条目按插入顺序淘汰，
# 否则「看过几千个封面地址」就会把这张表撑成内存泄漏。
_FAILED_MAX = 512
_failed: OrderedDict[str, float] = OrderedDict()
_failed_guard = threading.Lock()


def _recently_failed(url: str) -> bool:
    """近期下载失败过就跳过（失败冷却），避免反复打同一个坏地址。"""
    with _failed_guard:
        at = _failed.get(url)
    return bool(at) and time.time() - at < _RETRY_AFTER


def _mark_failed(url: str) -> None:
    with _failed_guard:
        _failed[url] = time.time()
        _failed.move_to_end(url)
        while len(_failed) > _FAILED_MAX:
            _failed.popitem(last=False)

_WRAPPERS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"), ("《", "》"))


def strip_wrapping(value: str) -> str:
    """去掉首尾成对的引号/书名号（「复制文件地址」会带一层双引号）。"""
    text = (value or "").strip()
    for _ in range(3):
        stripped = False
        for left, right in _WRAPPERS:
            if len(text) >= 2 and text.startswith(left) and text.endswith(right):
                text = text[1:-1].strip()
                stripped = True
        if not stripped:
            break
    return text


def normalize(source: str) -> str:
    """把用户填写的封面地址整理成可用形式（本地路径或 http(s) URL）。"""
    text = strip_wrapping(source)
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("file:///"):
        text = text[8:]
    elif lowered.startswith("file://"):
        text = text[7:]
    if text.startswith("//"):
        text = "https:" + text
    elif lowered.startswith("www."):
        text = "https://" + text
    if os.name == "nt" and re.match(r"^[a-zA-Z]:/", text):
        text = text.replace("/", "\\")
    return os.path.expanduser(text)


def is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _extension(url: str, content_type: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        return suffix
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    return mapping.get(content_type.split(";")[0].strip().lower(), ".jpg")


def _referer(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else ""


def cached_cover(
    url: str, cache_dir: Path | None = None, timeout: float = 8.0
) -> Path | None:
    """下载并缓存网络封面；命中缓存直接返回本地文件。"""
    target_dir = Path(cache_dir or COVERS_DIR)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    if target_dir.exists():
        for existing in target_dir.glob(f"{key}.*"):
            if existing.suffix != ".tmp":
                return existing

    now = time.time()
    if _recently_failed(url):
        return None

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Referer": _referer(url)},
            )
            response.raise_for_status()
            data = response.content
        if not data or len(data) > _MAX_BYTES:
            raise ValueError("封面内容为空或过大")
    except Exception:
        # 只记录域名与状态，避免把带签名的完整 URL 写进日志。
        logger.info("封面下载失败：%s", urlsplit(url).netloc or url)
        _mark_failed(url)
        return None

    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{key}{_extension(url, response.headers.get('content-type', ''))}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError:
        logger.warning("封面写入缓存失败：%s", target)
        return None
    return target


def resolve_cover(source: str, cache_dir: Path | None = None) -> str:
    """返回可直接交给 ``ft.Image`` 的地址；无法解析时返回空串（调用方走占位图）。"""
    text = normalize(source)
    if not text:
        return ""
    if is_url(text):
        path = cached_cover(text, cache_dir)
        return str(path) if path else ""
    path = Path(text)
    if not path.is_absolute():
        path = DATA_DIR / path
    return str(path) if path.exists() else ""


def prefetch(sources: list[str], cache_dir: Path | None = None, workers: int = 4) -> None:
    """在后台线程里并发预热网络封面，避免逐条串行下载拖慢列表。"""
    urls = [normalize(source) for source in sources if source]
    urls = [url for url in urls if is_url(url)]
    if not urls:
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda url: cached_cover(url, cache_dir), urls))
