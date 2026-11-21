"""后台调度：同步（启动/每 5 分钟/写后 10 秒）与待办到点提醒（每 1 分钟）。

Flet 0.86 已移除 ft.Timer，统一用 threading.Timer + page.run_task 回到 UI 线程。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import flet as ft

from ..db.session import session_scope
from ..services.notify import notify
from ..services.reminders import collect_due
from ..services.sync import sync_client
from .. import theme as th
from .widgets import snack

logger = logging.getLogger(__name__)

_page: ft.Page | None = None
_refresh_status: Callable[[], None] | None = None
_refresh_content: Callable[[], None] | None = None
_interval_timer: threading.Timer | None = None
_pending_timer: threading.Timer | None = None
_reminder_timer: threading.Timer | None = None

REMINDER_INTERVAL = 60  # 秒：每分钟检查一次到点提醒


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
    _start_reminder_loop(delay=20)  # 启动 20s 后再查，避免和首屏加载抢资源


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
                logger.exception("调度同步结果回 UI 线程失败")

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
            logger.exception("同步后刷新页面失败")


def _start_reminder_loop(delay: int = REMINDER_INTERVAL) -> None:
    global _reminder_timer
    _reminder_timer = threading.Timer(max(1, delay), _reminder_tick)
    _reminder_timer.daemon = True
    _reminder_timer.start()


def _reminder_tick() -> None:
    try:
        with session_scope() as session:
            due = collect_due(session)
    except Exception:
        logger.exception("待办提醒检查失败")
        due = []
    if due:
        for item in due:
            try:
                notify(item["title"], item["body"])
            except Exception:
                logger.exception("系统提醒发送失败：%s", item.get("todo_id"))
        _show_reminder_snack(len(due))
        logger.info("已发送 %d 条待办提醒", len(due))
    _start_reminder_loop()


def _show_reminder_snack(count: int) -> None:
    if _page is None:
        return

    async def apply() -> None:
        try:
            snack(_page, f"有 {count} 条任务到点提醒", th.ACCENT)
        except Exception:
            logger.debug("提醒提示失败，页面可能已销毁", exc_info=True)

    try:
        _page.run_task(apply)
    except Exception:
        logger.debug("提醒提示调度失败，页面可能已销毁", exc_info=True)


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
