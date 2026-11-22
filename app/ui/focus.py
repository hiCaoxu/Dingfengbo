"""今日聚焦：首页的「行动视图」。

与原来的仪表盘视图相比，这里回答的是「今天做什么」：今日待办可直接勾选、
日记未写会提示、健康/消费/预算一眼可见、习惯可当场打卡、AI 可生成今日建议。
"""

from __future__ import annotations

import logging
from datetime import date

import flet as ft

from .. import theme as th
from ..icons import ACTIONS, ico
from ..services.finance import money
from ..services.habits import check_in
from .widgets import card, empty, equal_height_row, primary_button, snack

logger = logging.getLogger(__name__)


def _metric(title: str, value: str, hint: str = "", accent: bool = False) -> ft.Control:
    return ft.Column(
        [
            ft.Text(title, size=th.FONT_CAPTION, color=th.TEXT_MUTED),
            ft.Text(value, size=18, color=th.ACCENT if accent else th.TEXT, weight="bold",
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(hint, size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ],
        spacing=2,
        tight=True,
        expand=True,
    )


def _todo_row(page: ft.Page, refresh, item, toggle) -> ft.Control:
    overdue = bool(item.due_date and item.due_date < date.today())
    return ft.Row(
        [
            ft.Checkbox(
                value=item.completed,
                on_change=lambda e, i=item.id: toggle(page, i, refresh),
            ),
            ft.Column(
                [
                    ft.Text(
                        item.title, size=12, color=th.DANGER if overdue else th.TEXT,
                        weight="bold", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        (item.due_date.isoformat() if item.due_date else "无截止日")
                        + (f" {item.due_time}" if item.due_time else "")
                        + (f" · {item.priority}" if item.priority else ""),
                        size=th.FONT_CAPTION,
                        color=th.DANGER if overdue else th.TEXT_MUTED,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=2,
                expand=True,
                tight=True,
            ),
        ],
        spacing=8,
    )


def render(
    page: ft.Page,
    refresh,
    data: dict,
    on_open_diary,
    on_open_note,
    navigate,
) -> ft.Control:
    """渲染今日聚焦视图（数据由首页后台加载后传入）。"""
    from .todo import toggle as toggle_todo
    from .pomodoro import open_pomodoro_dialog

    budget = data["budget"]
    habits = data["habits"]
    pending_habits = data["habits_pending"]

    def open_today_diary(_=None) -> None:
        on_open_diary(date.today())

    def do_check_in(habit_id: str) -> None:
        try:
            check_in(habit_id, date.today(), True)
        except Exception:
            logger.exception("习惯打卡失败")
            snack(page, "打卡失败，详见日志", th.ACCENT)
            return
        refresh()

    def ai_brief(_=None) -> None:
        from .ai_tasks_ui import run_task_dialog

        run_task_dialog(page, "daily_brief")

    todo_body: ft.Control
    if data["todos"]:
        todo_body = ft.Column(
            [_todo_row(page, refresh, item, toggle_todo) for item in data["todos"]],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
        )
    else:
        todo_body = empty("今天没有任务，享受空白")

    todo_card = card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            f"完成 {data['completed_today']} 项 · 未完成 {data['pending_count']} 项"
                            + (f" · 逾期 {data['overdue_count']} 项" if data["overdue_count"] else ""),
                            size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
                        ),
                        ft.Text(f"完成率 {data['completion_rate']}%", size=th.FONT_CAPTION,
                                color=th.PRIMARY, weight="bold"),
                    ]
                ),
                todo_body,
            ],
            spacing=10,
            expand=True,
        ),
        title="今日必做",
        trailing=ft.Row(
            [
                primary_button("番茄钟", lambda e: open_pomodoro_dialog(page, refresh),
                               ACTIONS["timer"]),
                primary_button("去任务", lambda e: navigate("todo"), ACTIONS["add"]),
            ],
            spacing=6,
        ),
        expand=3,
    )

    diary_card = card(
        ft.Column(
            [
                ft.Text(
                    f"连续写作 {data['writing_streak']} 天",
                    size=th.FONT_METRIC, color=th.PRIMARY, weight="bold",
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    "今天的日记已经写了，继续保持" if data["diary_written"]
                    else "今天还没有写日记，趁记忆新鲜记两句",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                    max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                primary_button(
                    "继续写日记" if data["diary_written"] else "写今天的日记",
                    open_today_diary,
                    "edit",
                ),
            ],
            spacing=10,
            expand=True,
        ),
        title="日记",
        expand=1,
    )

    health_card = card(
        ft.Column(
            [
                ft.Row(
                    [
                        _metric("运动", f"{data['exercise_minutes']} 分钟",
                                f"摄入 {data['diet_calories']} 千卡"),
                        _metric("睡眠", f"{data['sleep_hours']:g} 小时",
                                f"质量 {data['sleep_quality']}/5"),
                        _metric("体重", f"{data['weight']:g} kg" if data["weight"] else "—",
                                "最近记录"),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    # 注意：Row 的 vertical_alignment=STRETCH 会让整行在父 Column 里撑满
                    # 剩余高度，把下方「贴底」的 spacer 挤成 0，导致按钮行漂移。这里
                    # 指标列本身等高，用 CENTER 即可，不能再用 STRETCH。
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                ft.Container(expand=True),
                ft.Row(
                    [
                        primary_button("记录锻炼", lambda e: navigate("health"), "FITNESS_CENTER_OUTLINED"),
                        ft.TextButton("查看健康", on_click=lambda e: navigate("health")),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=10,
            expand=True,
        ),
        title="健康摘要",
        expand=1,
    )

    budget_line = (
        f"本月预算 {money(budget['total_budget'])} · 已用 {budget['percent']}%"
        if budget["total_budget"]
        else "还没有设置本月预算"
    )
    finance_card = card(
        ft.Column(
            [
                ft.Text(f"今日支出 {money(data['spent_today'])}", size=th.FONT_METRIC,
                        color=th.ACCENT, weight="bold", max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(budget_line, size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                *[
                    ft.Text(f"⚠ {message}", size=th.FONT_CAPTION, color=th.DANGER,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
                    for message in data["budget_alerts"][:2]
                ],
                ft.Container(expand=True),
                ft.Row(
                    [
                        primary_button("记一笔", lambda e: navigate("finance"), "PAYMENTS_OUTLINED"),
                        ft.TextButton("记账概览", on_click=lambda e: navigate("finance")),
                    ],
                    spacing=4,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=8,
            expand=True,
        ),
        title="消费",
        expand=1,
    )

    habit_controls: list[ft.Control] = []
    for item in habits[:5]:
        done = item["done_today"]
        habit_controls.append(
            ft.Row(
                [
                    ft.Icon(
                        ico("CHECK_CIRCLE_OUTLINE" if done else "RADIO_BUTTON_UNCHECKED_OUTLINED"),
                        size=16,
                        color=th.PRIMARY if done else th.TEXT_MUTED,
                    ),
                    ft.Text(item["name"], size=th.FONT_BODY, color=th.TEXT, expand=True,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(
                        f"连续 {item['streak']} 天 · 本周 {item['week_done']}/{item['target_per_week']}",
                        size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.TextButton(
                        "已打卡" if done else "打卡",
                        on_click=None if done else (lambda e, hid=item["id"]: do_check_in(hid)),
                        disabled=done,
                    ),
                ],
                spacing=6,
            )
        )
    if not habit_controls:
        habit_controls = [empty("还没有习惯，先去「习惯」模块添加")]

    habit_card = card(
        ft.Column(habit_controls, spacing=6, scroll=ft.ScrollMode.AUTO),
        title="习惯打卡",
        trailing=ft.TextButton("管理", on_click=lambda e: navigate("todo")),
        expand=2,
    )

    note = data["note"]
    review_card = card(
        ft.Column(
            [
                ft.Text(note.title if note else "暂无笔记", size=th.FONT_CARD_TITLE,
                        color=th.TEXT, weight="bold", max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(
                    (note.content or "").replace("\n", " ")[:90] if note else "随手记一条，日后就是回顾素材",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=3,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.TextButton(
                    "打开这条笔记", on_click=(lambda e: on_open_note(note)) if note else None,
                    disabled=note is None,
                ),
            ],
            spacing=6,
            expand=True,
        ),
        title="随机回顾",
        expand=2,
    )

    ai_card = card(
        ft.Column(
            [
                ft.Text(
                    "把今天的任务、健康、消费和习惯交给 AI，让它给出今天最值得做的三件事。",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=4,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Container(expand=True),
                primary_button("生成今日建议", ai_brief, "AUTO_AWESOME_OUTLINED"),
            ],
            spacing=10,
            expand=True,
        ),
        title="AI 今日建议",
        expand=1,
    )

    return ft.Column(
        [
            equal_height_row([todo_card, diary_card], spacing=12, height=300),
            equal_height_row([health_card, finance_card, ai_card], spacing=12, height=200),
            equal_height_row([habit_card, review_card], spacing=12, height=210),
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
