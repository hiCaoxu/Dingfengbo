"""手机端同步调度：启动一次、每 5 分钟、写入后 10 秒。

不做桌面那套每分钟提醒。提醒依赖进程常驻，安卓上不可靠。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import flet as ft

from ..services.sync import auto_sync_paused, set_after_write, sync_client

logger = logging.getLogger(__name__)

_page: ft.Page | None = None
_on_status: Callable[[], None] | None = None
_interval_timer: threading.Timer | None = None
_pending_timer: threading.Timer | None = None

INTERVAL_SECONDS = 300
WRITE_DELAY_SECONDS = 10


def bind(page: ft.Page, on_status: Callable[[], None]) -> None:
    """绑到当前页面，并注册写后同步。"""
    global _page, _on_status
    _page = page
    _on_status = on_status
    set_after_write(schedule_sync)
    _run_sync_async()
    _start_interval()


def schedule_sync() -> None:
    """多次写入合并成一次同步。"""
    global _pending_timer
    if _page is None or auto_sync_paused():
        return
    if _pending_timer is not None:
        _pending_timer.cancel()
    _pending_timer = threading.Timer(WRITE_DELAY_SECONDS, _run_sync_async)
    _pending_timer.daemon = True
    _pending_timer.start()


def _start_interval() -> None:
    global _interval_timer
    _interval_timer = threading.Timer(INTERVAL_SECONDS, _on_interval)
    _interval_timer.daemon = True
    _interval_timer.start()


def _on_interval() -> None:
    _run_sync_async()
    _start_interval()


def _run_sync_async() -> None:
    if auto_sync_paused():
        logger.debug("自动同步已暂停，跳过本次")
        return

    def worker() -> None:
        try:
            sync_client.sync_once()
        except Exception:
            logger.exception("自动同步失败")
        page = _page
        if page is None:
            return
        try:
            page.run_task(_after_sync)
        except Exception:
            logger.exception("同步结果回界面失败")

    threading.Thread(target=worker, daemon=True).start()


async def _after_sync() -> None:
    if _on_status is not None:
        _on_status()
