"""全项目日志配置：滚动文件 + 控制台，供桌面端与服务端共用。

约定（新增日志时请遵守）：

- **不写用户内容**：正文、备注、标题一律不进日志；只写表名、主键前 8 位、数量。
- **不写凭据与完整 URL**：API Key、令牌、带签名的图片/接口地址用
  :func:`safe_url` 只保留 ``scheme://host``。
- **级别**：``DEBUG`` 排查细节（默认不输出）；``INFO`` 状态变化（同步、备份、导入）；
  ``WARNING`` 用户可感知的失败或降级；``ERROR`` 只有需要人工介入的异常。
- 级别可用环境变量 ``DINGFENGBO_LOG_LEVEL`` 覆盖（DEBUG/INFO/WARNING/ERROR），
  排查线上问题时不必改代码。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_HANDLER_MARK = "_dingfengbo_handler"

# 单个文件 2MB × 5 份：INFO 级别的日常使用大约能留存两三周，
# 同时不会让打包目录/服务器磁盘被日志吃掉。
MAX_BYTES = 2_000_000
BACKUP_COUNT = 5
DEFAULT_LEVEL = "INFO"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
# 第三方库的 INFO 级噪音：httpx 每条请求打一行、还会带完整 URL（query 里可能有签名）。
# 正常情况下压到 WARNING；只有在 DINGFENGBO_LOG_LEVEL=DEBUG 时才放出来，
# 因为那时用户明确要「一条请求都不漏」。
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "asyncio")


def resolve_level(raw: str | None = None) -> int:
    """解析日志级别：``DINGFENGBO_LOG_LEVEL`` 优先，无法识别时回落到 INFO。

    故意不抛异常：一个拼错的环境变量不该让程序起不来。
    """
    value = (raw if raw is not None else os.environ.get("DINGFENGBO_LOG_LEVEL", "")).strip().upper()
    if value in _LEVELS:
        return getattr(logging, value)
    if value:
        logger.warning("无法识别的日志级别 %r，已按 %s 处理", value, DEFAULT_LEVEL)
    return getattr(logging, DEFAULT_LEVEL)


def safe_url(url: str | None) -> str:
    """URL 的日志安全形式：只保留 ``scheme://host[:port]``。

    查询串里常常是签名/令牌（对象存储、图床尤其如此），路径里可能带用户 id；
    排查只需要知道「打到了哪个服务」，所以统一裁掉。
    """
    text = (url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "<无法解析的地址>"
    if not parts.scheme or not parts.netloc:
        return "<相对地址>"
    # 用 hostname 而不是 netloc：netloc 会把 user:pass@ 一起带出来（凭据泄漏）。
    host = parts.hostname or ""
    if not host:
        return "<无法解析的地址>"
    if ":" in host:  # IPv6 需要方括号才能拼回合法 URL
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def setup_logging(logs_dir: Path) -> None:
    """幂等初始化根 logger；重复调用不会叠加 handler。

    幂等判定只看**本函数装过的** handler：早期实现是 ``if root.handlers: return``，
    只要 uvicorn 之类先注册了任意 handler，我们自己的文件日志就永远建不起来
    （桌面端与打包后的服务端都会静默没有日志文件）。
    """
    root = logging.getLogger()
    if any(getattr(handler, _HANDLER_MARK, False) for handler in root.handlers):
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    root.setLevel(resolve_level())
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        logs_dir / "dingfengbo.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    # 文件始终收 DEBUG 以上：级别由 root 控制，排查时改环境变量即可回看细节。
    file_handler.setLevel(logging.DEBUG)
    setattr(file_handler, _HANDLER_MARK, True)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARK, True)
    root.addHandler(console_handler)

    _quiet_third_party(root.level)


def _quiet_third_party(root_level: int) -> None:
    """按根级别压制第三方库的请求日志（DEBUG 时保持原样）。"""
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(
            logging.DEBUG if root_level <= logging.DEBUG else logging.WARNING
        )
