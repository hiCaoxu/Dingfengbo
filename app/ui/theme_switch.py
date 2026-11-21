"""主题切换入口：把主界面的 ``apply_theme`` 暴露给设置页等任意弹窗。

主界面负责重建导航/头部与刷新页面，弹窗只需要「请求切换」，
不必把 apply_theme 一路作为参数透传。
"""

from __future__ import annotations

import logging

from .. import theme as th
from ..config import save_settings

logger = logging.getLogger(__name__)

_applier = None


def bind(applier) -> None:
    global _applier
    _applier = applier


def apply(mode: str) -> str:
    """切换主题；主界面未绑定回调时至少持久化设置（下次启动生效）。"""
    mode = th.set_mode(mode)
    save_settings({"theme_mode": mode})
    if _applier is not None:
        try:
            _applier(mode)
        except Exception:
            logger.exception("应用主题失败")
    return mode


def current() -> str:
    return th.current_mode()
