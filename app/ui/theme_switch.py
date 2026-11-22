"""主界面重建入口：把 ``apply_theme`` / ``_render_nav`` 暴露给设置页等任意弹窗。

主界面负责重建导航/头部与刷新页面，弹窗只需要「请求重建」，
不必把回调一路作为参数透传。
"""

from __future__ import annotations


import logging
from .. import theme as th
from ..config import save_settings

logger = logging.getLogger(__name__)

_applier = None
_nav_refresher = None


def bind(applier) -> None:
    global _applier
    _applier = applier


def bind_nav(refresher) -> None:
    """注册左侧导航重建回调（设置里改了模块显隐/排序时用）。"""
    global _nav_refresher
    _nav_refresher = refresher


def refresh_nav() -> None:
    """只重建导航，不整页换主题。"""
    if _nav_refresher is None:
        return
    try:
        _nav_refresher()
    except Exception:
        logger.exception("重建导航失败")


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
