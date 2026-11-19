"""日记：月历视图（默认）、日期检索、自动建与自动保存。"""

from __future__ import annotations

from datetime import date

import flet as ft
from sqlalchemy import select

from ..db.models import Diary
from ..db.repo import update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.dates import diary_title, iso_week, month_grid, shift_month
from ..services.diary import ensure_diary
from ..services.sync import mark_pending_write
from ..theme import ACCENT, DIVIDER, PRIMARY, PRIMARY_LIGHT, TEXT, TEXT_MUTED, card_border
from .widgets import card, divider, empty, icon_button, primary_button, section_header

_state = {
    "view": "月历",
    "year": date.today().year,
    "month": date.today().month,
    "selected": date.today(),
    "query": "",
}


def set_selected(day: date) -> None:
    _state["selected"] = day


def save_content(diary_id: str, value: str) -> None:
    with session_scope() as session:
        item = session.get(Diary, diary_id)
        if item:
            update(session, item, content=value)
    mark_pending_write()


def _editor(day: date, refresh) -> ft.Control:
    with session_scope() as session:
        diary = ensure_diary(session, day)
        diary_id, title, content = diary.id, diary.title, diary.content
    has_diary = bool((content or "").strip())
    field = ft.TextField(
        value=content,
        multiline=True,
        min_lines=14,
        max_lines=24,
        border=ft.InputBorder.NONE,
        color=TEXT,
        text_style=ft.TextStyle(size=13, color=TEXT, height=1.6),
        on_blur=lambda e: save_content(diary_id, e.control.value),
    )
    return card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(title, size=14, color=TEXT, weight="bold"),
                        ft.Container(width=7, height=7, bgcolor=PRIMARY if has_diary else DIVIDER, border_radius=4),
                        ft.Text("有日记" if has_diary else "暂无日记", size=11, color=PRIMARY if has_diary else TEXT_MUTED),
                    ],
                    spacing=6,
                ),
                ft.Text("光标离开正文即自动保存", size=10, color=TEXT_MUTED),
                divider(),
                field,
            ],
            spacing=10,
        ),
        title="日记编辑",
        expand=True,
    )


def _month_view(refresh) -> ft.Control:
    year, month = _state["year"], _state["month"]
    with session_scope() as session:
        diaries = {
            d.date: d
            for d in session.scalars(
                select(Diary).where(Diary.is_deleted.is_(False))
            )
        }
    grid = month_grid(year, month)
    cells: list[ft.Control] = []
    for week in grid:
        cells.append(
            ft.Container(
                content=ft.Text(f"{iso_week(week[0])}周", size=10, color=TEXT_MUTED),
                width=38,
                alignment=ft.Alignment.CENTER,
            )
        )
        for day in week:
            in_month = day.month == month and day.year == year
            diary = diaries.get(day)
            has_diary = bool(diary and (diary.content or "").strip())
            selected = day == _state["selected"]
            body = ft.Column(
                [
                    ft.Text(str(day.day), size=11, color=TEXT if in_month else TEXT_MUTED,
                            weight="bold" if selected else "normal"),
                    ft.Container(
                        width=5, height=5, bgcolor=PRIMARY, border_radius=3,
                        visible=has_diary,
                    ),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
            cells.append(
                ft.Container(
                    content=body,
                    bgcolor=PRIMARY_LIGHT if selected else ("#FFFFFF" if in_month else "#F7F4EF"),
                    border=ft.Border.all(1, DIVIDER),
                    border_radius=4,
                    padding=3,
                    width=36,
                    height=30,
                    opacity=1 if in_month else 0.55,
                    on_click=(
                        lambda e, d=day: (_state.__setitem__("selected", d), refresh())
                    ) if in_month else None,
                )
            )
    selected = _state["selected"]
    history = [
        d for d in diaries.values()
        if d.date.year in range(selected.year - 5, selected.year)
        and d.date.month == selected.month
        and d.date.day == selected.day
        and (d.content or "").strip()
    ]
    history.sort(key=lambda d: d.date, reverse=True)
    history_section = ft.Column(
        [
            ft.Text("历史上的今天", size=11, color=TEXT_MUTED, weight="bold"),
            *(
                [
                    ft.TextButton(
                        f"{d.date.year}年{d.date.month}月{d.date.day}日",
                        on_click=lambda e, dd=d.date: (_state.__setitem__("selected", dd), refresh()),
                    )
                    for d in history
                ]
                if history
                else [ft.Text("暂无", size=10, color=TEXT_MUTED)]
            ),
        ],
        spacing=2,
    )
    return card(
        ft.Column(
            [
                ft.Row(
                    [
                        icon_button(ACTIONS["chevron_left"], lambda e: _shift_month(-1, refresh), "上一月"),
                        ft.Text(f"{year}年{month}月", size=13, color=TEXT, weight="bold"),
                        icon_button(ACTIONS["chevron_right"], lambda e: _shift_month(1, refresh), "下一月"),
                        ft.TextButton("今天", on_click=lambda e: _goto_today(refresh)),
                    ],
                    spacing=4,
                ),
                divider(),
                ft.Row(cells[:8], spacing=4),
                *[ft.Row(cells[i:i + 8], spacing=4) for i in range(8, len(cells), 8)],
                divider(),
                history_section,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        title="月历视图",
        width=360,
    )


def _shift_month(delta: int, refresh) -> None:
    year, month = shift_month(_state["year"], _state["month"], delta)
    _state["year"], _state["month"] = year, month
    refresh()


def _goto_today(refresh) -> None:
    today = date.today()
    _state.update(year=today.year, month=today.month, selected=today)
    refresh()


def _list_view(refresh) -> ft.Control:
    with session_scope() as session:
        diaries = list(
            session.scalars(
                select(Diary).where(Diary.is_deleted.is_(False)).order_by(Diary.date.desc())
            )
        )
    query = _state["query"]
    shown = [
        d for d in diaries
        if (d.content or "").strip()
        and (not query or query in d.date.isoformat() or query in d.title)
    ]
    field = ft.TextField(
        label="按日期检索 YYYY-MM-DD",
        value=query,
        width=240,
        border_color=DIVIDER,
        focused_border_color=PRIMARY,
        on_change=lambda e: (_state.__setitem__("query", e.control.value or ""), refresh()),
    )
    return card(
        ft.Column(
            [
                field,
                divider(),
                ft.Column(
                    [
                        ft.ListTile(
                            title=ft.Text(d.title, size=12, color=TEXT),
                            subtitle=ft.Text((d.content or "").replace("\n", " ")[:60], size=10, color=TEXT_MUTED),
                            on_click=lambda e, day=d.date: (_state.__setitem__("selected", day), refresh()),
                        )
                        for d in shown
                    ],
                    spacing=2,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ) if shown else empty("暂无日记"),
            ],
            spacing=10,
            expand=True,
        ),
        title="日记列表",
        width=360,
    )


def build(page: ft.Page, refresh) -> ft.Control:
    tabs = ft.Row(
        [
            ft.TextButton("月历视图" if _state["view"] != "月历" else "● 月历视图",
                          on_click=lambda e: (_state.__setitem__("view", "月历"), refresh())),
            ft.TextButton("列表视图" if _state["view"] != "列表" else "● 列表视图",
                          on_click=lambda e: (_state.__setitem__("view", "列表"), refresh())),
        ],
        spacing=6,
    )
    main = _month_view(refresh) if _state["view"] == "月历" else _list_view(refresh)
    return ft.Column(
        [
            section_header("日记", tabs),
            ft.Row([main, _editor(_state["selected"], refresh)], spacing=14, expand=True),
        ],
        spacing=12,
        expand=True,
    )
