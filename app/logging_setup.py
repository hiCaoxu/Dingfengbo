"""全项目日志配置：滚动文件 + 控制台，供桌面端与服务端共用。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


_HANDLER_MARK = "_dingfengbo_handler"


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
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        logs_dir / "dingfengbo.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARK, True)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARK, True)
    root.addHandler(console_handler)
