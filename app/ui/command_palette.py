"""全局命令面板（Ctrl+K）：跨模块搜索 + 快捷命令。

交互：输入即搜（150ms 防抖 + 后台线程查询），回车执行第一条，
点击结果直接跳到对应模块并打开编辑弹窗。命令区始终排在结果前面，
保证「新增待办」「立即同步」这类高频操作零输入成本。
"""

from __future__ import annotations

import logging
import threading

import flet as ft

from .. import theme as th
from ..icons import ico
from ..services.ai.client import ai_configured
from ..services.search import Hit, search
from ..services.sync import sync_client
from . import forms
from .widgets import dismiss, open_dialog

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.15
MAX_ROWS = 24


class _Click:
    """回车时伪造的事件对象：处理函数只用到回调本身，不读事件字段。"""

    control = None
    data = None


def _row(icon: str, title: str, subtitle: str, on_click, tag: str = "") -> ft.Container:
    controls: list[ft.Control] = [
        ft.Icon(ico(icon), size=16, color=th.PRIMARY),
        ft.Column(
            [
                ft.Text(title, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold",
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(subtitle, size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
            ],
            spacing=2,
            expand=True,
            tight=True,
        ),
    ]
    if tag:
        controls.append(
            ft.Container(
                ft.Text(tag, size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                bgcolor=th.PRIMARY_LIGHT,
                border_radius=8,
                padding=ft.Padding.symmetric(vertical=2, horizontal=6),
            )
        )
    return ft.Container(
        content=ft.Row(controls, spacing=10),
        padding=ft.Padding.symmetric(vertical=6, horizontal=8),
        border_radius=8,
        on_click=on_click,
        ink=True,
    )


def open_command_palette(
    page: ft.Page,
    navigate,
    refresh,
    on_open_note=None,
    on_open_diary=None,
    on_sync=None,
) -> ft.AlertDialog:
    """打开命令面板。``navigate/refresh`` 沿用主界面回调，避免重复实现路由。"""
    results = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=360)
    status = ft.Text("输入关键字，或直接选择下面的命令", size=th.FONT_CAPTION,
                     color=th.TEXT_MUTED)
    query_field = ft.TextField(
        hint_text="搜索任务 / 笔记 / 日记 / 收支 / 娱乐，或输入命令…",
        autofocus=True,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        text_style=ft.TextStyle(size=13, color=th.TEXT),
        content_padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )

    def close() -> None:
        dismiss(page, dialog)

    def go(module: str) -> None:
        close()
        navigate(module)

    # ---------------------------------------------------------------- 命令
    def new_todo(_=None) -> None:
        close()
        forms.open_todo_dialog(page, None, refresh)

    def new_note(_=None) -> None:
        close()
        forms.open_note_dialog(page, None, refresh)

    def new_transaction(_=None) -> None:
        close()
        forms.open_transaction_dialog(page, "expense", None, refresh)

    def open_settings(_=None) -> None:
        close()
        forms.open_settings_dialog(page, refresh, refresh)

    def open_backup(_=None) -> None:
        close()
        from .backup_center import open_backup_center

        open_backup_center(page, refresh)

    def sync_now(_=None) -> None:
        close()
        if on_sync is not None:
            on_sync()
        else:
            page.run_thread(lambda: sync_client.sync_once())

    def ask_ai(_=None) -> None:
        go("ai")

    def open_report(_=None) -> None:
        go("reports")

    def open_habits(_=None) -> None:
        go("todo")

    def open_timeline(_=None) -> None:
        go("timeline")

    def open_help(_=None) -> None:
        go("help")

    def open_data_io(_=None) -> None:
        from .data_io_dialog import open_data_io_dialog

        close()
        open_data_io_dialog(page, refresh)

    def quick_expense(_=None) -> None:
        from .budget_dialogs import open_quick_parse_dialog

        close()
        open_quick_parse_dialog(page, refresh)

    commands: list[tuple[str, str, str, object]] = [
        ("ADD_TASK_OUTLINED", "新增任务", "打开任务录入弹窗", new_todo),
        ("NOTE_ADD_OUTLINED", "新增笔记", "新建一条 Markdown 笔记", new_note),
        ("PAYMENTS_OUTLINED", "记一笔", "快速记录收支", new_transaction),
        ("BOLT_OUTLINED", "一句话记账", "输入「午饭花了 32 元」直接生成收支", quick_expense),
        ("CHECK_CIRCLE_OUTLINE", "任务与习惯", "月历、四象限、任务列表与习惯打卡", open_habits),
        ("TIMELINE_OUTLINED", "生活时间轴", "跨模块时间线与标签", open_timeline),
        ("SYNC_OUTLINED", "立即同步", "把本地改动推到服务端并拉取远端数据", sync_now),
        ("AUTO_AWESOME_OUTLINED", "问 AI", "打开 AI 助手", ask_ai),
        ("INSIGHTS_OUTLINED", "查看回顾报告", "周/月/年统计与 AI 总结", open_report),
        ("BACKUP_OUTLINED", "备份恢复中心", "查看备份、恢复、导出", open_backup),
        ("DOWNLOAD_OUTLINED", "数据导入导出", "Markdown / CSV / JSON", open_data_io),
        ("HELP_OUTLINE", "帮助与上手", "各模块能做什么、快捷键与常见问题", open_help),
        ("SETTINGS_OUTLINED", "打开设置", "同步、AI、外观与数据目录", open_settings),
    ]

    def command_row(icon: str, label: str, description: str, action) -> ft.Container:
        def run(_=None, fn=action) -> None:
            fn()

        return _row(icon, label, description, run, "命令")

    # ---------------------------------------------------------------- 结果
    def open_hit(hit: Hit) -> None:
        close()
        try:
            if hit.module == "todo":
                forms.open_todo_dialog(page, hit.payload, refresh)
            elif hit.module == "finance":
                forms.open_transaction_dialog(page, hit.payload.kind, hit.payload, refresh)
            elif hit.module == "fun":
                forms.open_entertainment_dialog(page, hit.payload, refresh)
            elif hit.module == "notes" and on_open_note is not None:
                on_open_note(hit.payload)
            elif hit.module == "diary" and on_open_diary is not None:
                on_open_diary(hit.payload.date)
            else:
                navigate(hit.module)
        except Exception:
            logger.exception("命令面板打开记录失败")
            navigate(hit.module)

    def render_rows(grouped: dict[str, list[Hit]], text: str) -> None:
        controls: list[ft.Control] = []
        lowered = (text or "").strip().lower()
        matched_commands = [
            item for item in commands
            if not lowered or lowered in item[1].lower() or lowered in item[2].lower()
        ]
        if matched_commands and not grouped:
            for icon, label, description, action in matched_commands[:6]:
                controls.append(command_row(icon, label, description, action))
        for table, hits in grouped.items():
            if not hits:
                continue
            controls.append(
                ft.Container(
                    ft.Text(hits[0].label, size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                            weight="bold"),
                    padding=ft.Padding.only(left=6, top=6, bottom=2),
                )
            )
            for hit in hits:
                controls.append(
                    _row(
                        {
                            "todo": "CHECK_CALENDAR_OUTLINED",
                            "notes": "DESCRIPTION_OUTLINED",
                            "diary": "AUTO_STORIES_OUTLINED",
                            "finance": "ACCOUNT_BALANCE_WALLET_OUTLINED",
                            "fun": "MOVIE_OUTLINED",
                        }.get(hit.module, "SEARCH_OUTLINED"),
                        hit.title,
                        " · ".join(x for x in (hit.subtitle, hit.snippet) if x),
                        lambda e, item=hit: open_hit(item),
                        hit.label,
                    )
                )
        if not controls:
            controls.append(
                ft.Container(
                    ft.Text("没有匹配结果，试试「任务 今天 高优先级」这类过滤",
                            size=th.FONT_CAPTION,
                            color=th.TEXT_MUTED),
                    padding=ft.Padding.all(12),
                )
            )
        results.controls = controls[:MAX_ROWS]
        page.update()

    pending = {"token": 0}

    def run_search() -> None:
        text = query_field.value or ""
        pending["token"] += 1
        token = pending["token"]

        def worker() -> None:
            try:
                grouped = search(text)
            except Exception:
                logger.exception("命令面板搜索失败")
                grouped = {}

            async def apply() -> None:
                if token != pending["token"]:
                    return
                try:
                    render_rows(grouped, text)
                    if grouped:
                        total = sum(len(items) for items in grouped.values())
                        status.value = f"找到 {total} 条结果 · Enter 打开第一条"
                    else:
                        status.value = "输入关键字，或直接选择下面的命令"
                except Exception:
                    logger.debug("命令面板渲染失败，可能已关闭", exc_info=True)

            try:
                page.run_task(apply)
            except Exception:
                logger.debug("命令面板结果回 UI 线程失败", exc_info=True)

        page.run_thread(worker)

    timer = {"value": None}

    def on_change(_) -> None:
        if timer["value"] is not None:
            timer["value"].cancel()
        timer["value"] = threading.Timer(DEBOUNCE_SECONDS, run_search)
        timer["value"].daemon = True
        timer["value"].start()

    def on_submit(_) -> None:
        if results.controls:
            for control in results.controls:
                handler = getattr(control, "on_click", None)
                if handler is not None:
                    handler(_Click())
                    return

    query_field.on_change = on_change
    query_field.on_submit = on_submit

    body = ft.Column(
        [
            query_field,
            ft.Container(
                ft.Text(
                    "支持自然语言过滤：任务 今天 高优先级 / 笔记 AI / 日记 本月",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                ),
                padding=ft.Padding.only(left=4, top=2),
            ),
            results,
            status,
        ],
        spacing=8,
        tight=True,
    )
    dialog = ft.AlertDialog(
        bgcolor=th.CARD,
        title=ft.Row(
            [
                ft.Icon(ico("SEARCH_OUTLINED"), size=18, color=th.PRIMARY),
                ft.Text("搜索与命令", size=th.FONT_SECTION_TITLE, color=th.TEXT,
                        weight="bold"),
                ft.Container(expand=True),
                ft.Text("Ctrl+K", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
            ],
            spacing=8,
        ),
        content=ft.Container(body, width=620),
        actions=[ft.TextButton("关闭", on_click=lambda e: close())],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=12),
    )
    open_dialog(page, dialog)
    render_rows({}, "")
    try:
        page.run_task(query_field.focus)
    except Exception:
        logger.debug("命令面板聚焦失败", exc_info=True)
    return dialog


def ai_ready() -> bool:
    return ai_configured()
