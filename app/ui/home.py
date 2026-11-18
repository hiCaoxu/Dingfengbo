"""首页：日历卡、本月统计、待办、随机笔记、年度热力图与快捷入口。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from random import choice

import flet as ft
from sqlalchemy import select

from ..db.models import Account, Diary, Diet, Exercise, Note, Sleep, Todo, Transaction, Weight
from ..db.session import session_scope
from ..icons import ACTIONS, QUICK, ico
from ..services.dates import day_of_year, iso_week, lunar_text, shift_month, weekday_cn
from ..services.diary import ensure_diary
from ..services.finance import account_balance, money, month_summary, total_assets
from ..services.sync import mark_pending_write
from ..theme import (
    ACCENT,
    CARD,
    DIVIDER,
    HEATMAP_LEVELS,
    PRIMARY,
    PRIMARY_LIGHT,
    TEXT,
    TEXT_MUTED,
    card_border,
)
from .forms import open_quick_action
from .widgets import card, divider, empty, icon_button, primary_button, section_header, tag_chip

QUICK_ITEMS = [
    ("todo", "新增待办"),
    ("note", "新增笔记"),
    ("finance", "新增收支"),
    ("exercise", "记录锻炼"),
    ("diet", "记录饮食"),
    ("weight", "记录体重"),
    ("sleep", "记录睡眠"),
]


def _calendar_card(month: date, refresh, set_month) -> ft.Container:
    def move(delta):
        year, mon = shift_month(month.year, month.month, delta)
        set_month(date(year, mon, 1))

    body = ft.Column(
        [
            ft.Text(str(month.day), size=58, color=TEXT, weight="bold", text_align=ft.TextAlign.CENTER),
            ft.Text(f"第 {day_of_year(month)} 天 · 第 {iso_week(month)} 周", size=12, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER),
            ft.Text(f"{lunar_text(month)}  {weekday_cn(month)}", size=12, color=ACCENT, text_align=ft.TextAlign.CENTER),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=6,
    )
    return card(
        body,
        title=f"{month.year}年{month.month}月",
        trailing=ft.Row(
            [
                icon_button(ACTIONS["chevron_left"], lambda e: move(-1), "上一月"),
                icon_button(ACTIONS["chevron_right"], lambda e: move(1), "下一月"),
                ft.TextButton("今天", on_click=lambda e: set_month(date.today().replace(day=1))),
            ],
            spacing=0,
        ),
    )


def _stat_card(title: str, value: str, accent: bool = False) -> ft.Container:
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
    )


def _activity_counts() -> dict[date, int]:
    counts: dict[date, int] = defaultdict(int)
    with session_scope() as session:
        for obj in session.scalars(select(Diary).where(Diary.is_deleted.is_(False))):
            counts[obj.date] += 1
        for obj in session.scalars(select(Todo).where(Todo.completed.is_(True), Todo.is_deleted.is_(False))):
            if obj.completed_at:
                counts[obj.completed_at.date()] += 1
        for model in (Exercise, Diet, Weight, Sleep, Transaction):
            for obj in session.scalars(select(model).where(model.is_deleted.is_(False))):
                counts[obj.date] += 1
    return counts


def _heatmap_cell(day: date, count: int, on_open) -> ft.Container:
    level = 0 if count == 0 else min(5, max(1, (count + 1) // 2))
    return ft.Container(
        width=13,
        height=13,
        bgcolor=HEATMAP_LEVELS[level],
        border_radius=3,
        tooltip=f"{day.isoformat()} · {count} 条",
        on_click=lambda e, d=day: on_open(d),
    )


def _heatmap_card(year: int, on_open, set_year) -> ft.Container:
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
        rows.append(
            ft.Row(
                [_heatmap_cell(week[weekday], counts.get(week[weekday], 0), on_open) for week in weeks],
                spacing=3,
            )
        )
    grid = ft.Column(
        [
            ft.Row(
                [
                    ft.Container(width=26, height=13),
                    *[
                        ft.Container(
                            content=ft.Text(f"{w[0].month}月", size=9, color=TEXT_MUTED)
                            if w[0].day <= 7
                            else None,
                            width=13,
                        )
                        for w in weeks
                    ],
                ],
                spacing=3,
            ),
            *rows,
        ],
        spacing=3,
    )
    return card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("过去 12 个月活跃度", size=12, color=TEXT_MUTED),
                        ft.Row(
                            [
                                ft.Container(width=10, height=10, bgcolor=c, border_radius=2) for c in HEATMAP_LEVELS
                            ],
                            spacing=3,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(content=grid, width=max(400, len(weeks) * 16)),
                ft.Text("悬停查看日期与条目数，点击直达当日日记", size=10, color=TEXT_MUTED),
            ],
            spacing=10,
        ),
        title="活跃度热力图",
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
    state = {"month": today.replace(day=1), "year": today.year}

    def set_month(value: date):
        state["month"] = value
        refresh()

    def set_year(value: int):
        state["year"] = value
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
                    Todo.due_date == today,
                ).order_by(Todo.priority, Todo.due_date)
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
                        ft.Text(item.title, size=12, color=TEXT, expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(item.due_date.isoformat() if item.due_date else "", size=10, color=TEXT_MUTED),
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
                            width=44, height=44, bgcolor=PRIMARY_LIGHT, border_radius=12,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Text(label, size=11, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER),
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
                    _calendar_card(state["month"], refresh, set_month),
                    _stat_card("本月收入", money(summary["income"])),
                    _stat_card("本月支出", money(summary["expense"]), accent=True),
                    _stat_card("总资产", money(assets)),
                ],
                spacing=14,
                expand=True,
            ),
            ft.Row(
                [
                    card(todo_rows(todo_today, "今天没有待办"), title="今日待办"),
                    card(todo_rows(todo_week, "本周没有待办"), title="本周待办"),
                    card(note_body, title="随机笔记", on_click=open_note if note else None),
                ],
                spacing=14,
            ),
            _heatmap_card(state["year"], open_day, set_year),
            card(quick, title="快捷入口"),
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
