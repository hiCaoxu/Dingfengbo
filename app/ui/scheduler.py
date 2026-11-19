"""同步调度：启动一次、每 5 分钟一次、写后 10 秒一次。

Flet 0.86 已移除 ft.Timer，改用 threading.Timer + page.run_task 回到 UI 线程。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import flet as ft

from ..services.sync import sync_client

_page: ft.Page | None = None
_refresh_status: Callable[[], None] | None = None
_refresh_content: Callable[[], None] | None = None
_interval_timer: threading.Timer | None = None
_pending_timer: threading.Timer | None = None


def bind(
    page: ft.Page,
    refresh_status: Callable[[], None],
    refresh_content: Callable[[], None],
) -> None:
    global _page, _refresh_status, _refresh_content, _interval_timer
    _page = page
    _refresh_status = refresh_status
    _refresh_content = refresh_content
    _run_sync_async()
    _start_interval()


def _start_interval() -> None:
    global _interval_timer
    _interval_timer = threading.Timer(300, _on_interval)
    _interval_timer.daemon = True
    _interval_timer.start()


def _on_interval() -> None:
    _run_sync_async()
    _start_interval()


def _run_sync_async() -> None:
    def worker() -> None:
        sync_client.sync_once()
        if _page is not None:
            try:
                _page.run_task(_after_sync)
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()


async def _after_sync() -> None:
    if _refresh_status:
        _refresh_status()
    if sync_client.status.startswith("已同步") and _refresh_content:
        _refresh_content()
    elif _page is not None:
        try:
            _page.update()
        except Exception:
            pass


def schedule_sync(delay_ms: int = 10_000) -> None:
    """写后触发；短时间内的多次写入合并为一次。"""
    global _pending_timer
    if _page is None:
        return
    if _pending_timer is not None:
        _pending_timer.cancel()
    _pending_timer = threading.Timer(max(1, delay_ms // 1000), _run_sync_async)
    _pending_timer.daemon = True
    _pending_timer.start()
