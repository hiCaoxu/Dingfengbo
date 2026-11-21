"""首页：日历卡、本月统计、待办、随机笔记、年度热力图与快捷入口。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import flet as ft
from sqlalchemy import func, select

from ..db.models import Account, Diary, Diet, Exercise, Note, Sleep, Todo, Transaction, Weight
from ..db.session import session_scope
from ..icons import QUICK, ico
from ..services.dates import day_of_year, iso_week, local_date_from_utc, lunar_text, shift_month, weekday_cn
from ..services.diary import ensure_diary
from ..services.finance import money, month_summary, total_assets
from ..services.sync import mark_pending_write
from ..theme import (
    ACCENT,
    CARD,
    CARD_RADIUS,
    DANGER,
    DIVIDER,
    HEATMAP_LEVELS,
    PRIMARY,
    PRIMARY_LIGHT,
    TEXT,
    TEXT_MUTED,
    card_border,
)
from ..db.repo import create
from .forms import open_quick_action
from .widgets import card, divider, empty, primary_button, section_header, tag_chip

QUICK_ITEMS = [
    ("todo", "新增待办"),
    ("note", "新增笔记"),
    ("finance", "新增收支"),
    ("exercise", "记录锻炼"),
    ("diet", "记录饮食"),
    ("weight", "记录体重"),
    ("sleep", "记录睡眠"),
]

# 首页视图状态需跨 refresh 保留，否则每次重建都会回到今天。
from .state import state

_HOME_STATE = state.home


def _calendar_card(selected: date, set_date, width: int | None = None, height: int | None = None) -> ft.Container:
    header = ft.Container(
        content=ft.Row(
            [
                ft.Text(
                    f"{selected.year}年{selected.month}月",
                    size=15, color="#FFFFFF", weight="bold",
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        bgcolor=PRIMARY,
        border_radius=ft.BorderRadius(
            top_left=CARD_RADIUS, top_right=CARD_RADIUS, bottom_left=0, bottom_right=0
        ),
        padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )
    days_left = (date(selected.year, 12, 31) - selected).days
    body = ft.Column(
        [
            ft.Container(expand=True),
            ft.Text(str(selected.day), size=86, color=TEXT, weight="bold", text_align=ft.TextAlign.CENTER),
            ft.Text(
                f"第{day_of_year(selected)}天 · 第{iso_week(selected)}周",
                size=13, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                f"{lunar_text(selected).split(' ')[0]} {weekday_cn(selected)}",
                size=18, color=TEXT, text_align=ft.TextAlign.CENTER,
            ),
            ft.Container(expand=True),
            ft.Text(
                f"距今年结束还有 {days_left} 天",
                size=11, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER,
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        expand=True,
        spacing=8,
    )
    return ft.Container(
        content=ft.Column(
            [header, body],
            spacing=0,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        bgcolor=CARD,
        border=card_border(),
        border_radius=CARD_RADIUS,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        width=width,
        height=height,
    )


def _stat_card(title: str, value: str, accent: bool = False, width: int | None = None, height: int | None = None) -> ft.Container:
    return card(
        ft.Column(
            [
                ft.Text(value, size=20, color=ACCENT if accent else TEXT, weight="bold"),
                divider(),
                ft.Text("数据来自记账模块", size=10, color=TEXT_MUTED),
            ],
            spacing=8,
        ),
        title=title,
        expand=1,
        width=width,
        height=height,
    )


def _activity_counts(since: date | None = None) -> dict[date, int]:
    """热力图按日计数，聚合下推 SQL；只统计窗口内的记录。"""
    counts: dict[date, int] = defaultdict(int)
    with session_scope() as session:
        for model in (Diary, Exercise, Diet, Weight, Sleep, Transaction):
            stmt = select(model.date, func.count()).where(model.is_deleted.is_(False))
            if since is not None:
                stmt = stmt.where(model.date >= since)
            stmt = stmt.group_by(model.date)
            for day, n in session.execute(stmt):
                counts[day] += n
        # Todo 以完成时间计入，需本地时区换算，保留 Python 侧计数。
        todo_stmt = select(Todo).where(Todo.completed.is_(True), Todo.is_deleted.is_(False))
        for obj in session.scalars(todo_stmt):
            if obj.completed_at:
                day = local_date_from_utc(obj.completed_at)
                if since is None or day >= since:
                    counts[day] += 1
    return counts


_WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]
_HEAT_CELL = 10
_HEAT_GAP = 2
_HEAT_LABEL_W = 18


def _heatmap_cell(day: date, count: int, on_open) -> ft.Container:
    level = 0 if count == 0 else min(5, max(1, (count + 1) // 2))
    return ft.Container(
        width=_HEAT_CELL,
        height=_HEAT_CELL,
        bgcolor=HEATMAP_LEVELS[level],
        border_radius=3,
        tooltip=f"{day.isoformat()} · {count} 条",
        on_click=lambda e, d=day: on_open(d),
    )


def _heatmap_card(year: int, on_open, set_year, height: int | None = None) -> ft.Container:
    today = date.today()
    current_year = today.year
    # 当年只画到今天：未来日期没有数据，画出来只是空行；历史年份取自然年。
    end = today if year == current_year else date(year, 12, 31)
    start = date(year, 1, 1)
    if year == current_year:
        # 当年热力图铺满过去 12 个月；未来年份已在切换按钮上禁用。
        start_year, start_month = shift_month(current_year, today.month, -11)
        start = date(start_year, start_month, 1)
    anchor = start - timedelta(days=start.weekday())
    counts = _activity_counts(since=anchor)
    weeks: list[list[date]] = []
    cursor = anchor
    while cursor <= end:
        weeks.append([cursor + timedelta(days=i) for i in range(7)])
        cursor += timedelta(days=7)
    rows = []
    for weekday in range(7):
        label = ft.Container(
            content=ft.Text(_WEEKDAY_LABELS[weekday], size=9, color=TEXT_MUTED),
            width=_HEAT_LABEL_W,
            height=_HEAT_CELL,
            alignment=ft.Alignment.CENTER_RIGHT,
        )
        cells = ft.Row(
            [_heatmap_cell(week[weekday], counts.get(week[weekday], 0), on_open) for week in weeks],
            spacing=_HEAT_GAP,
        )
        rows.append(ft.Row([label, cells], spacing=4))

    grid = ft.Column(
        [
            ft.Row(
                [
                    ft.Container(width=_HEAT_LABEL_W + 4, height=8),
                    *[
                        ft.Container(
                            content=ft.Text(f"{w[0].month}月", size=8, color=TEXT_MUTED)
                            if w[0].day <= 7
                            else None,
                            width=_HEAT_CELL,
                        )
                        for w in weeks
                    ],
                ],
                spacing=_HEAT_GAP,
            ),
            *rows,
        ],
        spacing=_HEAT_GAP,
    )
    legend = ft.Row(
        [
            ft.Text("少", size=9, color=TEXT_MUTED),
            *[
                ft.Container(width=10, height=10, bgcolor=HEATMAP_LEVELS[i], border_radius=3)
                for i in range(6)
            ],
            ft.Text("多", size=9, color=TEXT_MUTED),
        ],
        spacing=3,
        alignment=ft.MainAxisAlignment.END,
    )
    return card(
        ft.Column(
            [
                grid,
                legend,
            ],
            spacing=8,
        ),
        title="活跃度热力图",
        height=height,
        trailing=ft.Row(
            [
                ft.TextButton(f"{year - 1}", on_click=lambda e: set_year(year - 1)),
                ft.Text(str(year), size=12, color=PRIMARY, weight="bold"),
                # 不展示未来年份：当年时直接不渲染「下一年」入口。
                ft.TextButton(
                    f"{year + 1}",
                    on_click=lambda e: set_year(year + 1),
                    visible=year < current_year,
                ),
            ],
            spacing=2,
        ),
    )


def build(page: ft.Page, refresh, on_open_diary, on_open_note) -> ft.Control:
    """首页：查询后台化，避免数据量大时阻塞 UI 线程。"""
    today = date.today()
    # 历史状态可能残留未来年份（旧版本允许切换），进来先纠正。
    if _HOME_STATE["year"] > today.year:
        _HOME_STATE["year"] = today.year
    holder = ft.Container(
        content=ft.Container(
            content=ft.Text("加载中…", size=12, color=TEXT_MUTED),
            alignment=ft.Alignment.CENTER,
            expand=True,
        ),
        expand=True,
    )

    def load() -> None:
        with session_scope() as session:
            summary = month_summary(session, today.year, today.month)
            assets = total_assets(session)
            soon_end = today + timedelta(days=6)
            todo_today = list(
                session.scalars(
                    select(Todo).where(
                        Todo.is_deleted.is_(False), Todo.completed.is_(False),
                        Todo.due_date <= today,
                    ).order_by(Todo.due_date, Todo.priority)
                )
            )[:5]
            # 最近 7 天：今天起滚动 7 天（而非自然周），避免周一刚过就空掉。
            todo_soon = list(
                session.scalars(
                    select(Todo).where(
                        Todo.is_deleted.is_(False), Todo.completed.is_(False),
                        Todo.due_date >= today, Todo.due_date <= soon_end,
                    ).order_by(Todo.due_date)
                )
            )[:5]
            note = session.scalar(
                select(Note).where(Note.is_deleted.is_(False))
                .order_by(func.random()).limit(1)
            )

        async def apply() -> None:
            holder.content = _render_home(
                page, refresh, on_open_diary, on_open_note,
                summary, assets, todo_today, todo_soon, note,
            )
            page.update()

        page.run_task(apply)

    page.run_thread(load)
    return holder


def _render_home(page, refresh, on_open_diary, on_open_note,
                 summary, assets, todo_today, todo_soon, note) -> ft.Control:
    today = date.today()

    def set_date(value: date):
        _HOME_STATE["selected_date"] = value
        refresh()

    def set_year(value: int):
        _HOME_STATE["year"] = value
        refresh()

    def todo_rows(items, empty_text):
        if not items:
            return empty(empty_text)
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ico("RADIO_BUTTON_UNCHECKED_OUTLINED"), size=16, color=TEXT_MUTED),
                        ft.Text(
                            item.title,
                            size=12,
                            color=DANGER if (item.due_date and item.due_date < today) else TEXT,
                            expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            ("已过期 · " if (item.due_date and item.due_date < today) else "")
                            + (item.due_date.isoformat() if item.due_date else ""),
                            size=10,
                            color=DANGER if (item.due_date and item.due_date < today) else TEXT_MUTED,
                        ),
                    ],
                    spacing=8,
                )
                for item in items
            ],
            spacing=10,
        )

    note_body = (
        ft.Column(
            [
                ft.Text(note.title, size=13, color=TEXT, weight="bold"),
                ft.Text((note.content or "").replace("\n", " ")[:100] or "（暂无正文）", size=11, color=TEXT_MUTED),
            ],
            spacing=6,
        )
        if note
        else empty("暂无笔记，点击快捷入口新增")
    )

    def open_note(_):
        if note:
            on_open_note(note)

    def new_note(_):
        """快捷入口「新增笔记」：建空笔记后直接进入编辑页，省掉弹窗。"""
        with session_scope() as session:
            created = create(session, Note, title="未命名笔记", content="", tags="[]")
        mark_pending_write()
        on_open_note(created)

    def open_day(day: date):
        with session_scope() as session:
            ensure_diary(session, day)
            mark_pending_write()
        on_open_diary(day)

    quick = ft.Row(
        [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Container(
                            content=ft.Icon(ico(QUICK[key]), size=22, color=PRIMARY),
                            width=36, height=36, bgcolor=PRIMARY_LIGHT, border_radius=10,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Text(label, size=10, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6,
                ),
                on_click=lambda e, k=key: (
                    new_note(e) if k == "note" else open_quick_action(page, k, refresh)
                ),
            )
            for key, label in QUICK_ITEMS
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Column(
                        [
                            _heatmap_card(_HOME_STATE["year"], open_day, set_year, height=200),
                            ft.Row(
                                [
                                    _stat_card("本月收入", money(summary["income"]), height=120),
                                    _stat_card("本月支出", money(summary["expense"]), accent=True, height=120),
                                    _stat_card("总资产", money(assets), height=120),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=8,
                        expand=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    _calendar_card(_HOME_STATE["selected_date"], set_date, width=280, height=328),
                ],
                spacing=12,
            ),
            ft.Row(
                [
                    card(todo_rows(todo_today, "今天没有待办"), title="今日待办", expand=1, height=180),
                    card(todo_rows(todo_soon, "最近 7 天没有待办"), title="最近 7 天", expand=1, height=180),
                    card(note_body, title="随机笔记", on_click=open_note if note else None,
                         expand=1, height=180),
                ],
                spacing=12,
            ),
            card(quick, title="快捷入口", padding=ft.Padding.symmetric(vertical=8, horizontal=16)),
        ],
        spacing=8,
        expand=True,
    )
