"""首页：日历卡、本月统计、待办、随机笔记、年度热力图与快捷入口。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from random import choice

import flet as ft
from sqlalchemy import select

from ..db.models import Account, Diary, Diet, Exercise, Note, Sleep, Todo, Transaction, Weight
from ..db.session import session_scope
from ..icons import QUICK, ico
from ..services.dates import day_of_year, iso_week, local_date_from_utc, lunar_text, shift_month, weekday_cn
from ..services.diary import ensure_diary
from ..services.finance import account_balance, money, month_summary, total_assets
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
_HOME_STATE = {"selected_date": date.today(), "year": date.today().year}


def _calendar_card(selected: date, set_date, width: int | None = None, height: int | None = None) -> ft.Container:
    header = ft.Container(
        content=ft.Row(
            [
                ft.Text(
                    f"{selected.year}年{selected.month}月",
                    size=14, color="#FFFFFF", weight="bold",
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        bgcolor=PRIMARY,
        border_radius=ft.BorderRadius(
            top_left=CARD_RADIUS, top_right=CARD_RADIUS, bottom_left=0, bottom_right=0
        ),
        padding=ft.Padding.symmetric(vertical=6, horizontal=10),
    )
    body = ft.Column(
        [
            ft.Text(str(selected.day), size=60, color=TEXT, weight="bold", text_align=ft.TextAlign.CENTER),
            ft.Text(
                f"第{day_of_year(selected)}天 · 第{iso_week(selected)}周",
                size=11, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                f"{lunar_text(selected).split(' ')[0]} {weekday_cn(selected)}",
                size=13, color=TEXT, text_align=ft.TextAlign.CENTER,
            ),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        expand=True,
        spacing=6,
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


def _activity_counts() -> dict[date, int]:
    counts: dict[date, int] = defaultdict(int)
    with session_scope() as session:
        for obj in session.scalars(select(Diary).where(Diary.is_deleted.is_(False))):
            counts[obj.date] += 1
        for obj in session.scalars(select(Todo).where(Todo.completed.is_(True), Todo.is_deleted.is_(False))):
            if obj.completed_at:
                counts[local_date_from_utc(obj.completed_at)] += 1
        for model in (Exercise, Diet, Weight, Sleep, Transaction):
            for obj in session.scalars(select(model).where(model.is_deleted.is_(False))):
                counts[obj.date] += 1
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
    counts = _activity_counts()
    end = date(year, 12, 31)
    start = date(year - 1, 1, 1) if year > datetime.now().year else date(year, 1, 1)
    if year == datetime.now().year:
        start = shift_month(date.today().year, date.today().month, -11)[0:2]
        start = date(start[0], start[1], 1)
    anchor = start - timedelta(days=start.weekday())
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
                ft.TextButton(f"{year + 1}", on_click=lambda e: set_year(year + 1)),
            ],
            spacing=2,
        ),
    )


def build(page: ft.Page, refresh, on_open_diary, on_open_note) -> ft.Control:
    today = date.today()

    def set_date(value: date):
        _HOME_STATE["selected_date"] = value
        refresh()

    def set_year(value: int):
        _HOME_STATE["year"] = value
        refresh()

    with session_scope() as session:
        summary = month_summary(session, today.year, today.month)
        assets = total_assets(session)
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        todo_today = list(
            session.scalars(
                select(Todo).where(
                    Todo.is_deleted.is_(False), Todo.completed.is_(False),
                    Todo.due_date <= today,
                ).order_by(Todo.due_date, Todo.priority)
            )
        )[:5]
        todo_week = list(
            session.scalars(
                select(Todo).where(
                    Todo.is_deleted.is_(False), Todo.completed.is_(False),
                    Todo.due_date >= week_start, Todo.due_date <= week_end,
                ).order_by(Todo.due_date)
            )
        )[:5]
        notes = list(session.scalars(select(Note).where(Note.is_deleted.is_(False))))
    note = choice(notes) if notes else None

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
                on_click=lambda e, k=key: open_quick_action(page, k, refresh),
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
                            _heatmap_card(_HOME_STATE["year"], open_day, set_year, height=220),
                            ft.Row(
                                [
                                    _stat_card("本月收入", money(summary["income"]), height=130),
                                    _stat_card("本月支出", money(summary["expense"]), accent=True, height=130),
                                    _stat_card("总资产", money(assets), height=130),
                                ],
                                spacing=12,
                            ),
                        ],
                        spacing=8,
                        expand=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    _calendar_card(_HOME_STATE["selected_date"], set_date, width=280, height=358),
                ],
                spacing=12,
            ),
            ft.Row(
                [
                    card(todo_rows(todo_today, "今天没有待办"), title="今日待办", expand=1, height=180),
                    card(todo_rows(todo_week, "本周没有待办"), title="本周待办", expand=1, height=180),
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
