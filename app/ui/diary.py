"""日记：月历视图（默认）、日期检索、自动建与自动保存。"""

from __future__ import annotations

import re
import threading
from datetime import date

import flet as ft
from sqlalchemy import func, or_, select

from ..db.models import Diary
from ..db.repo import update
from ..db.session import session_scope
from ..icons import ACTIONS
from ..services import life
from ..services.dates import diary_title, iso_week, month_grid, shift_month
from ..services.diary import ensure_diary
from ..services.sync import mark_pending_write
from .. import theme as th
from .async_utils import background_section
from .state import state
from .uploads import upload_button
from .widgets import (
    card,
    dismiss,
    divider,
    empty,
    icon_button,
    modal,
    primary_button,
    section_header,
)

_state = state.diary
_EDITOR_LOCK = threading.Lock()
# 正文里的 Markdown 图片：![](...)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


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


def _editor(page: ft.Page, data: dict, refresh) -> ft.Control:
    diary_id, title, content = data["id"], data["title"], data["content"]
    has_diary = bool((content or "").strip())
    # 正文占满卡片剩余高度（不再用 min_lines 撑），和卡片同高
    field = ft.TextField(
        value=content,
        multiline=True,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=13, color=th.TEXT, height=1.6),
        on_blur=lambda e: save_content(diary_id, e.control.value),
        expand=True,
    )
    def insert_image(url: str) -> None:
        text = field.value or ""
        separator = "\n" if text and not text.endswith("\n") else ""
        field.value = f"{text}{separator}![]({url})\n"
        save_content(diary_id, field.value)
        # 重建卡片，让下方配图区立刻出现新图片
        refresh()

    images = _IMAGE_RE.findall(content or "")
    image_strip = (
        ft.Row(
            [
                ft.Image(src=url, width=76, height=76, fit=ft.BoxFit.COVER, border_radius=6)
                for url in images
            ],
            spacing=6,
            run_spacing=6,
            wrap=True,
        )
        if images
        else None
    )

    return card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(title, size=14, color=th.TEXT, weight="bold"),
                        ft.Container(width=7, height=7, bgcolor=th.PRIMARY if has_diary else th.DIVIDER, border_radius=4),
                        ft.Text("有日记" if has_diary else "暂无日记", size=11, color=th.PRIMARY if has_diary else th.TEXT_MUTED),
                        ft.Container(expand=True),
                        upload_button(page, insert_image, "插入图片", "选择要插入的图片"),
                    ],
                    spacing=6,
                ),
                ft.Text("光标离开正文即自动保存", size=10, color=th.TEXT_MUTED),
                divider(),
                field,
                *([image_strip] if image_strip else []),
            ],
            spacing=10,
            expand=True,
        ),
        title="日记编辑",
        expand=2,
    )


def _life_panel(page: ft.Page, refresh, events: list[dict]) -> ft.Control:
    """人生大事：只做新增 / 编辑 / 删除，按日期倒序展示，宽度是编辑区的一半。"""

    def form_dialog(event: dict | None = None) -> None:
        """新增与编辑共用同一个对话框；``event`` 为 None 表示新增。"""
        editing = event is not None
        target = event.get("target_date") if event else None

        title_field = ft.TextField(
            value=event["title"] if event else "",
            label="事件（15 字以内）",
            autofocus=True,
            max_length=life.MAX_TITLE,
            border_color=th.DIVIDER,
            focused_border_color=th.PRIMARY,
            color=th.TEXT,
            text_style=ft.TextStyle(size=12, color=th.TEXT),
            on_submit=lambda e: submit(),
        )
        date_field = ft.TextField(
            value=target.isoformat() if target else "",
            label="日期（YYYY-MM-DD，可留空）",
            hint_text="2026-11-21",
            border_color=th.DIVIDER,
            focused_border_color=th.PRIMARY,
            color=th.TEXT,
            text_style=ft.TextStyle(size=12, color=th.TEXT),
            on_submit=lambda e: submit(),
        )
        error_text = ft.Text("", size=10, color=th.DANGER)

        def submit() -> None:
            title = (title_field.value or "").strip()
            if not title:
                error_text.value = "事件不能为空"
                page.update()
                return
            try:
                day = life.parse_target_date(date_field.value or "")
            except ValueError as exc:
                error_text.value = str(exc)
                page.update()
                return
            saved = (
                life.update_event(event["id"], title, day)
                if editing
                else life.add_event(title, day)
            )
            if saved:
                dismiss(page, dialog)
                refresh()

        dialog = modal(
            page,
            "编辑人生大事" if editing else "新增人生大事",
            ft.Column([title_field, date_field, error_text], spacing=10, tight=True),
            [
                ft.TextButton("取消", on_click=lambda e: dismiss(page, dialog)),
                primary_button("保存" if editing else "添加", lambda e: submit()),
            ],
            width=420,
        )

    def row(event: dict) -> ft.Control:
        target = event.get("target_date")
        return ft.Row(
            [
                ft.Container(
                    content=ft.Text(
                        target.isoformat() if target else "未定",
                        size=10,
                        color=th.TEXT_MUTED,
                    ),
                    width=74,
                ),
                # 不设 max_lines / overflow：标题完整换行显示，不出现省略号
                ft.Text(event["title"], size=12, color=th.TEXT, expand=True),
                icon_button(ACTIONS["edit"], lambda e, ev=event: form_dialog(ev), "编辑"),
                icon_button(
                    ACTIONS["delete"],
                    lambda e, i=event["id"]: (life.delete_event(i), refresh()),
                    "删除",
                ),
            ],
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    body = (
        ft.Column([row(event) for event in events], spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
        if events
        else empty("还没有目标，先写下第一件")
    )
    return card(
        ft.Column([divider(), body], spacing=6, expand=True),
        title="人生大事",
        trailing=icon_button(ACTIONS["add"], lambda e: form_dialog(), "新增人生大事"),
        expand=1,
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
    """日记页一次性取齐：当前视图数据 + 当前选中日期的编辑内容 + 人生大事。"""
    view = _state["view"]
    return {
        "view": view,
        "main": _month_query() if view == "月历" else _list_query(),
        "editor": _editor_data(_state["selected"]),
        "life": life.list_events(),
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
            ft.Row(
                [
                    main,
                    _editor(page, payload["editor"], refresh),
                    _life_panel(page, refresh, payload.get("life") or []),
                ],
                spacing=14,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ],
        spacing=12,
        expand=True,
    )
