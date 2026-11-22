"""日记：月历视图（默认）、日期检索、自动建与自动保存。"""

from __future__ import annotations

import logging
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
from . import autosave
from .state import state
from .uploads import upload_button
from .widgets import (
    card,
    confirm,
    dismiss,
    divider,
    empty,
    icon_button,
    modal,
    page_header,
    primary_button,
)

logger = logging.getLogger(__name__)

_state = state.diary
_EDITOR_LOCK = threading.Lock()
# 正文里的 Markdown 图片：![](...)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")

# 自动保存：正文原先只在 on_blur 落库，而同步完成 / 切主题 / 切日期都会整页重建，
# 控件被替换时 on_blur 不保证触发，未失焦的文字就既没落库也没了。这里按停顿落库，
# 具体调度交给 autosave 状态机（版本化快照 + 串行写库，避免 Timer 与 UI 互相清 pending）。
_AUTOSAVE_DEBOUNCE_MS = 700


def flush_pending_save() -> None:
    """立即把尚未写盘的正文落库（重建编辑区 / 切换日期 / 失焦前调用）。"""
    autosave.flush_now()


def _diary_writer(_key: str, snapshot: dict) -> None:
    save_content(snapshot["id"], snapshot["value"], snapshot.get("day"))


def _schedule_save(diary_id: str, value: str, day: date | None = None) -> None:
    # 快照在 UI 线程里取好：Timer 触发时控件可能已被重建。
    autosave.schedule(
        "diary",
        {"id": diary_id, "value": value, "day": day},
        _diary_writer,
        _AUTOSAVE_DEBOUNCE_MS,
    )


def set_selected(day: date) -> None:
    _state["selected"] = day


def save_content(diary_id: str, value: str, day: date | None = None) -> str:
    """写正文；当天还没有日记行时按「首次真实输入才建」的规则创建。

    旧流程是「打开某天就 ensure_diary」：只是翻看日期也会留下一行空日记，还会同步到
    其他设备、进检索与热力图。现在浏览只读，写入（含插图）才建行；空内容不建。
    """
    written = False
    with session_scope() as session:
        item = session.get(Diary, diary_id) if diary_id else None
        if item is None and (value or "").strip() and day is not None:
            item = ensure_diary(session, day)
        if item is not None:
            update(session, item, content=value)
            diary_id = str(item.id)
            written = True
    if written:
        mark_pending_write()
    return diary_id


def _editor_data(day: date) -> dict:
    """读当天日记；**不存在时不创建**，只返回一份未落库的草稿。

    后台线程执行，避免写库卡住 UI。草稿的 id 为空串，第一次真实输入时由
    :func:`save_content` 建行（``ensure_diary`` 幂等，不会重复插入）。
    """
    with _EDITOR_LOCK:
        # 先落库待保存的正文，再读库：否则重建读到的还是上一次写盘的内容。
        flush_pending_save()
        with session_scope() as session:
            diary = session.scalar(
                select(Diary).where(
                    Diary.owner_id == "local",
                    Diary.date == day,
                    Diary.is_deleted.is_(False),
                )
            )
            if diary is not None:
                return {"id": diary.id, "day": day, "title": diary.title,
                        "content": diary.content}
            return {"id": "", "day": day, "title": diary_title(day), "content": ""}


def _editor(page: ft.Page, data: dict, refresh) -> ft.Control:
    diary_id, title, content = data["id"], data["title"], data["content"]
    # 草稿数据由 _editor_data 提供，一定带 day；这里兜底避免旧调用方 KeyError。
    day = data.get("day") or _state["selected"]
    # 本轮重建发生在最后一个字刚输入、定时器还没触发时：以内存里的最新值为准。
    pending = autosave.pending_snapshot("diary")
    if pending and pending.get("id") == diary_id:
        content = pending.get("value") or content
    has_diary = bool((content or "").strip())
    # 正文占满卡片剩余高度（不再用 min_lines 撑），和卡片同高
    field = ft.TextField(
        value=content,
        multiline=True,
        border=ft.NoInputBorder(),
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=13, color=th.TEXT, height=1.6),
        on_change=lambda e: _schedule_save(diary_id, e.control.value or "", day),
        on_blur=lambda e: (
            _schedule_save(diary_id, e.control.value or "", day),
            autosave.flush_now(),
        ),
        expand=True,
    )
    def insert_image(url: str) -> None:
        text = field.value or ""
        separator = "\n" if text and not text.endswith("\n") else ""
        field.value = f"{text}{separator}![]({url})\n"
        # 先同步待保存缓冲再 flush：否则重建时 _editor 会用缓冲里的旧正文覆盖新图。
        _schedule_save(diary_id, field.value, day)
        flush_pending_save()
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
                        ft.Text(title, size=th.FONT_CARD_TITLE, color=th.TEXT,
                                weight="bold", max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                        ft.Container(width=7, height=7, bgcolor=th.PRIMARY if has_diary else th.DIVIDER, border_radius=4),
                        ft.Text("有日记" if has_diary else "暂无日记", size=th.FONT_CAPTION,
                                color=th.PRIMARY if has_diary else th.TEXT_MUTED),
                        upload_button(page, insert_image, "插入图片", "选择要插入的图片"),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text("输入停顿即自动保存，切页/同步完成也不会丢字", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
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
        error_text = ft.Text("", size=th.FONT_CAPTION, color=th.DANGER)

        def submit() -> None:
            title = (title_field.value or "").strip()
            if not title:
                error_text.value = "事件不能为空"
                page.update()
                return
            try:
                day = life.parse_target_date(date_field.value or "")
            except ValueError as exc:
                logger.debug("_life_panel：忽略 %s", type(exc).__name__)
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
                # 标题在上、日期在下并排成两行：面板只有一百多像素宽，日期再单占一列
                # 会把标题挤成一字一行，这里把整行剩余宽度都给标题
                ft.Column(
                    [
                        # 标题不截断：面板窄，长标题宁可换行也不要出现省略号
                        ft.Text(event["title"], size=th.FONT_BODY, color=th.TEXT),
                        ft.Text(
                            target.isoformat() if target else "未定",
                            size=th.FONT_CAPTION,
                            color=th.TEXT_MUTED,
                            max_lines=1,
                        ),
                    ],
                    spacing=1,
                    expand=True,
                    tight=True,
                ),
                icon_button(ACTIONS["edit"], lambda e, ev=event: form_dialog(ev), "编辑", compact=True),
                icon_button(
                    ACTIONS["delete"],
                    lambda e, i=event["id"], t=event["title"]: confirm(
                        page,
                        f"删除人生大事「{t}」？",
                        lambda: (life.delete_event(i), refresh()),
                    ),
                    "删除",
                    compact=True,
                ),
            ],
            spacing=0,
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
                content=ft.Text(f"{iso_week(week[0])}周", size=th.FONT_CAPTION,
                                color=th.TEXT_MUTED, max_lines=1),
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
                    ft.Text(str(day.day), size=th.FONT_CAPTION,
                            color=th.TEXT if in_month else th.TEXT_MUTED,
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
                    bgcolor=th.PRIMARY_LIGHT if selected else (th.CARD if in_month else th.SUBTLE),
                    border=ft.Border.all(1, th.DIVIDER),
                    border_radius=4,
                    padding=3,
                    width=36,
                    height=32,
                    opacity=1 if in_month else 0.55,
                    on_click=(
                        lambda e, d=day: (_state.__setitem__("selected", d), refresh())
                    ) if in_month else None,
                )
            )
    history_section = ft.Column(
        [
            ft.Text("历史上的今天", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
            *(
                [
                    ft.TextButton(
                        f"{day.year}年{day.month}月{day.day}日",
                        on_click=lambda e, dd=day: (_state.__setitem__("selected", dd), refresh()),
                    )
                    for day in history
                ]
                if history
                else [ft.Text("暂无", size=th.FONT_CAPTION, color=th.TEXT_MUTED)]
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
        # 用 contains(autoescape=True)（与 search 层同一写法）：用户输入的 % / _ 是
        # LIKE 通配符，不转义的话搜 "100%" 会命中所有日记、搜 "_" 直接全表。
        conditions.append(
            or_(
                func.strftime("%Y-%m-%d", Diary.date).contains(query, autoescape=True),
                Diary.title.contains(query, autoescape=True),
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
        hint_text="输入后回车检索",
        # 只记状态、不触发整页重建：以前每敲一个字都会重建整页（含这个输入框），
        # 焦点随之丢失，实际只能「敲一个字→点回输入框」。
        on_change=lambda e: _state.__setitem__("query", e.control.value or ""),
        on_submit=lambda e: refresh(),
    )
    return card(
        ft.Column(
            [
                ft.Row([field, ft.TextButton("检索", on_click=lambda e: refresh())], spacing=6),
                divider(),
                ft.Column(
                    [
                        ft.ListTile(
                            title=ft.Text(title, size=th.FONT_BODY, color=th.TEXT,
                                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            subtitle=ft.Text(
                                (snippet or "").replace("\n", " ")[:60],
                                size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
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
            page_header(
                "日记",
                f"{_state['selected'].isoformat()} · {view}视图 · 共 {len(payload.get('life') or [])} 件人生大事",
                actions=[tabs],
            ),
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
