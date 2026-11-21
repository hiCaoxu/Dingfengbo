"""番茄钟：选择待办、倒计时、完成计数与系统提醒。"""

from __future__ import annotations

import logging
import threading
import time

import flet as ft
from sqlalchemy import select

from ..db.models import Todo
from ..db.repo import update
from ..db.session import session_scope
from ..services.notify import notify
from ..services.sync import mark_pending_write
from .. import theme as th
from .widgets import confirm, dismiss, dropdown, modal, primary_button, snack

logger = logging.getLogger(__name__)

# 只保留一份运行中的计时器状态：重复打开弹窗不应并发两个倒计时。
_session = {"active": False, "remaining": 0, "total": 0}


def _fmt(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _record(minutes: int, todo_id: str, title: str) -> None:
    """完成一次番茄：给关联待办累加计数并触发同步。

    计数是「读-加-写」，多端同时完成同一任务的番茄时可能互相覆盖（LWW 只留一份）。
    这里接受这个损失：番茄数只是激励性指标，为它引入独立的计数事件表不值当。
    """
    if not todo_id:
        return
    try:
        with session_scope() as session:
            todo = session.get(Todo, todo_id)
            if todo is not None:
                update(session, todo, pomodoro_count=int(todo.pomodoro_count or 0) + 1)
        mark_pending_write()
    except Exception:
        logger.exception("番茄钟计数写库失败：%s", title or todo_id)


def _run_timer(
    page: ft.Page,
    minutes: int,
    todo_id: str,
    title: str,
    remain_text: ft.Text,
    status_text: ft.Text,
    start_btn: ft.Button,
    stop_btn: ft.TextButton,
    refresh,
) -> None:
    def worker() -> None:
        while _session["active"] and _session["remaining"] > 0:
            time.sleep(1)
            if not _session["active"]:
                break
            _session["remaining"] -= 1
            remaining = _session["remaining"]

            async def apply(r: int = remaining) -> None:
                try:
                    remain_text.value = _fmt(r)
                    page.update()
                except Exception:
                    logger.debug("番茄钟刷新失败", exc_info=True)

            try:
                page.run_task(apply)
            except Exception:
                logger.debug("番茄钟刷新调度失败", exc_info=True)
                break

        finished = _session["active"] and _session["remaining"] <= 0
        _session["active"] = False

        async def done() -> None:
            try:
                if finished:
                    # 写库 + 系统通知都在后台线程：notify 走 PowerShell Toast，
                    # 最坏要等十几秒，放在事件循环里会把整个窗口冻住。
                    def effects() -> None:
                        _record(minutes, todo_id, title)
                        notify(
                            "番茄钟结束",
                            f"完成 {minutes} 分钟专注" + (f"：{title}" if title else ""),
                        )

                    page.run_thread(effects)
                    status_text.value = f"已完成 {minutes} 分钟，休息一下"
                    status_text.color = th.ACCENT
                    remain_text.value = _fmt(minutes * 60)
                else:
                    status_text.value = "已停止"
                    status_text.color = th.TEXT_MUTED
                start_btn.disabled = False
                start_btn.text = "开始"
                stop_btn.disabled = True
                page.update()
                if finished and refresh is not None:
                    refresh()
            except Exception:
                logger.debug("番茄钟收尾失败", exc_info=True)

        try:
            page.run_task(done)
        except Exception:
            logger.debug("番茄钟收尾调度失败", exc_info=True)

    threading.Thread(target=worker, daemon=True).start()


def open_pomodoro_dialog(page: ft.Page, refresh=None):
    if _session["active"]:
        snack(page, "番茄钟正在运行，请先停止当前计时", th.ACCENT)
        return None

    with session_scope() as session:
        todos = list(
            session.scalars(
                select(Todo)
                .where(Todo.is_deleted.is_(False), Todo.completed.is_(False))
                .order_by(Todo.due_date, Todo.due_time, Todo.id)
                .limit(50)
            )
        )

    todo_select = dropdown("关联任务（可选）", [""], "")
    todo_select.options = [ft.dropdown.Option(key="", text="（不关联任务）")] + [
        ft.dropdown.Option(key=item.id, text=item.title[:40]) for item in todos
    ]
    todo_select.width = 380

    duration = dropdown("专注时长", ["25 分钟", "45 分钟", "60 分钟"], "25 分钟", width=150)
    remain_text = ft.Text("25:00", size=44, color=th.PRIMARY, weight="bold")
    status_text = ft.Text("选择任务与时长后点开始", size=11, color=th.TEXT_MUTED)

    def stop(_=None):
        _session["active"] = False
        # 立即恢复按钮状态：计时线程要等下一次 tick 才会发现已停止。
        start_btn.text = "开始"
        start_btn.disabled = False
        stop_btn.disabled = True
        status_text.value = "已停止"
        status_text.color = th.TEXT_MUTED
        page.update()

    def close(_):
        if not _session["active"]:
            dismiss(page, dialog)
            return
        # 正在计时却顺手点了关闭，等于悄悄丢掉一次专注：先问一句。
        elapsed = max(0, _session["total"] - _session["remaining"])
        confirm(
            page,
            f"番茄钟正在计时（已过 {_fmt(elapsed)}），关闭会丢弃本次专注。确定关闭？",
            lambda: (stop(), dismiss(page, dialog)),
        )

    start_btn = primary_button("开始", lambda e: start(e))
    stop_btn = ft.TextButton("停止", on_click=stop, disabled=True)

    def start(_):
        if _session["active"]:
            return
        minutes = int((duration.value or "25 分钟").split(" ")[0])
        todo_id = todo_select.value or ""
        title = next((item.title for item in todos if item.id == todo_id), "")
        _session.update(active=True, remaining=minutes * 60, total=minutes * 60)
        remain_text.value = _fmt(minutes * 60)
        status_text.value = "专注中：" + (title or "自由专注")
        status_text.color = th.PRIMARY
        start_btn.disabled = True
        # 运行中把按钮文案改掉，弹窗里一眼能看出计时没停。
        start_btn.text = "计时中…"
        stop_btn.disabled = False
        page.update()
        _run_timer(
            page,
            minutes,
            todo_id,
            title,
            remain_text,
            status_text,
            start_btn,
            stop_btn,
            refresh,
        )

    dialog = modal(
        page,
        "番茄钟",
        ft.Column(
            [
                todo_select,
                duration,
                ft.Row([remain_text], alignment=ft.MainAxisAlignment.CENTER),
                status_text,
            ],
            spacing=12,
            tight=True,
        ),
        [
            ft.TextButton("关闭", on_click=close),
            stop_btn,
            start_btn,
        ],
        width=520,
    )
    return dialog
