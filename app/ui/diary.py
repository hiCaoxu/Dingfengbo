"""日记：月历视图（默认）、日期检索、自动建与自动保存。"""

from __future__ import annotations

import threading
from datetime import date

import flet as ft
from sqlalchemy import func, or_, select

from ..db.models import Diary
from ..db.repo import update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.dates import diary_title, iso_week, month_grid, shift_month
from ..services.diary import ensure_diary
from ..services.sync import mark_pending_write
from .. import theme as th
from .async_utils import background_section
from .state import state
from .widgets import card, divider, empty, icon_button, primary_button, section_header

_state = state.diary
_EDITOR_LOCK = threading.Lock()


def set_selected(day: date) -> None:
    _state["selected"] = day


def save_content(diary_id: str, value: str) -> None:
    with session_scope() as session:
        item = session.get(Diary, diary_id)
        if item:
            update(session, item, content=value)
    mark_pending_write()


def _editor_data(day: date) -> dict:
    """取（必要时创建）当天日记，后台线程执行，避免写库卡住 UI。

    ``ensure_diary`` 是「先查后建」，挪到后台后两次刷新可能并发；用模块级锁把
    建库路径串行化，避免同一天插入两次触发 (owner_id, date) 唯一约束。
    """
    with _EDITOR_LOCK:
        with session_scope() as session:
            diary = ensure_diary(session, day)
            return {"id": diary.id, "title": diary.title, "content": diary.content}


def _editor(data: dict, refresh) -> ft.Control:
    diary_id, title, content = data["id"], data["title"], data["content"]
    has_diary = bool((content or "").strip())
    field = ft.TextField(
        value=content,
        multiline=True,
        min_lines=14,
        max_lines=24,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=13, color=th.TEXT, height=1.6),
        on_blur=lambda e: save_content(diary_id, e.control.value),
    )
    return card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(title, size=14, color=th.TEXT, weight="bold"),
                        ft.Container(width=7, height=7, bgcolor=th.PRIMARY if has_diary else th.DIVIDER, border_radius=4),
                        ft.Text("有日记" if has_diary else "暂无日记", size=11, color=th.PRIMARY if has_diary else th.TEXT_MUTED),
                    ],
                    spacing=6,
                ),
                ft.Text("光标离开正文即自动保存", size=10, color=th.TEXT_MUTED),
                divider(),
                field,
            ],
            spacing=10,
        ),
        title="日记编辑",
        expand=True,
    )


def _month_query() -> tuple[int, int, set[date], list[date]]:
    """月历视图的数据查询（后台线程执行）：只取日期与正文长度，不加载 content。"""
    year, month = _state["year"], _state["month"]
    selected = _state["selected"]
    start = date(year, month, 1)
    end = date(*shift_month(year, month, 1), 1)
    history_start = date(selected.year - 5, 1, 1)
    history_end = date(selected.year, 1, 1)
    with session_scope() as session:
        month_rows = session.execute(
            select(Diary.date, func.length(Diary.content)).where(
                Diary.is_deleted.is_(False),
                Diary.date >= start,
                Diary.date < end,
            )
        ).all()
        history_rows = session.scalars(
            select(Diary.date).where(
                Diary.is_deleted.is_(False),
                Diary.date >= history_start,
                Diary.date < history_end,
                func.strftime("%m-%d", Diary.date) == selected.strftime("%m-%d"),
                func.length(Diary.content) > 0,
            ).order_by(Diary.date.desc())
        ).all()
    has_content = {day for day, length in month_rows if (length or 0) > 0}
    return year, month, has_content, list(history_rows)


def _month_view(refresh, data) -> ft.Control:
    year, month, has_content, history = data
    grid = month_grid(year, month)
    cells: list[ft.Control] = []
    for week in grid:
        cells.append(
            ft.Container(
                content=ft.Text(f"{iso_week(week[0])}周", size=10, color=th.TEXT_MUTED),
                width=38,
                alignment=ft.Alignment.CENTER,
            )
        )
        for day in week:
            in_month = day.month == month and day.year == year
            has_diary = day in has_content
            selected = day == _state["selected"]
            body = ft.Column(
                [
                    ft.Text(str(day.day), size=11, color=th.TEXT if in_month else th.TEXT_MUTED,
                            weight="bold" if selected else "normal"),
                    ft.Container(
                        width=5, height=5, bgcolor=th.PRIMARY, border_radius=3,
                        visible=has_diary,
                    ),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
            cells.append(
                ft.Container(
                    content=body,
                    bgcolor=th.PRIMARY_LIGHT if selected else ("#FFFFFF" if in_month else "#F7F4EF"),
                    border=ft.Border.all(1, th.DIVIDER),
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
    history_section = ft.Column(
        [
            ft.Text("历史上的今天", size=11, color=th.TEXT_MUTED, weight="bold"),
            *(
                [
                    ft.TextButton(
                        f"{day.year}年{day.month}月{day.day}日",
                        on_click=lambda e, dd=day: (_state.__setitem__("selected", dd), refresh()),
                    )
                    for day in history
                ]
                if history
                else [ft.Text("暂无", size=10, color=th.TEXT_MUTED)]
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
                        ft.Text(f"{year}年{month}月", size=13, color=th.TEXT, weight="bold"),
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


def _list_query() -> tuple[str, list]:
    """列表视图的数据查询（后台线程执行）：检索下推 SQL，正文只取前 80 字。"""
    query = (_state["query"] or "").strip()
    conditions = [Diary.is_deleted.is_(False), func.length(Diary.content) > 0]
    if query:
        conditions.append(
            or_(
                func.strftime("%Y-%m-%d", Diary.date).like(f"%{query}%"),
                Diary.title.like(f"%{query}%"),
            )
        )
    with session_scope() as session:
        shown = session.execute(
            select(Diary.date, Diary.title, func.substr(Diary.content, 1, 80))
            .where(*conditions)
            .order_by(Diary.date.desc())
        ).all()
    return query, list(shown)


def _list_view(refresh, data) -> ft.Control:
    query, shown = data
    field = ft.TextField(
        label="按日期检索 YYYY-MM-DD",
        value=query,
        width=240,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
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
                            title=ft.Text(title, size=12, color=th.TEXT),
                            subtitle=ft.Text(
                                (snippet or "").replace("\n", " ")[:60],
                                size=10, color=th.TEXT_MUTED,
                            ),
                            on_click=lambda e, day=day: (
                                _state.__setitem__("selected", day), refresh()
                            ),
                        )
                        for day, title, snippet in shown
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


def _load_page() -> dict:
    """日记页一次性取齐：当前视图数据 + 当前选中日期的编辑内容。"""
    view = _state["view"]
    return {
        "view": view,
        "main": _month_query() if view == "月历" else _list_query(),
        "editor": _editor_data(_state["selected"]),
    }


def build(page: ft.Page, refresh) -> ft.Control:
    # 整页数据（含当天日记的按需创建）统一在后台线程完成。
    return background_section(
        page, _load_page, lambda payload: _render(page, refresh, payload)
    )


def _render(page: ft.Page, refresh, payload) -> ft.Control:
    view = payload["view"]
    main = (
        _month_view(refresh, payload["main"])
        if view == "月历"
        else _list_view(refresh, payload["main"])
    )
    tabs = ft.Row(
        [
            ft.TextButton("月历视图" if view != "月历" else "● 月历视图",
                          on_click=lambda e: (_state.__setitem__("view", "月历"), refresh())),
            ft.TextButton("列表视图" if view != "列表" else "● 列表视图",
                          on_click=lambda e: (_state.__setitem__("view", "列表"), refresh())),
        ],
        spacing=6,
    )
    return ft.Column(
        [
            section_header("日记", tabs),
            ft.Row([main, _editor(payload["editor"], refresh)], spacing=14, expand=True),
        ],
        spacing=12,
        expand=True,
    )
