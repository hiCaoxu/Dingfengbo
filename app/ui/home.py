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
from .. import theme as th
from ..db.repo import create
from .async_utils import RenderToken, background_section
from .forms import open_quick_action
from .widgets import (
    card,
    empty,
    equal_height_row,
    page_header,
    section_header,
    tag_chip,
)

QUICK_ITEMS = [
    ("todo", "新增任务"),
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

# 首页渲染序号：切模块/切月份后，上一轮迟到的查询结果据此丢弃。
_HOME_TOKEN = RenderToken()


def _calendar_card(selected: date, set_date, width: int | None = None, height: int | None = None) -> ft.Container:
    header = ft.Container(
        content=ft.Row(
            [
                ft.Text(
                    f"{selected.year}年{selected.month}月",
                    size=15, color=th.ON_PRIMARY, weight="bold",
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        bgcolor=th.PRIMARY,
        border_radius=ft.BorderRadius(
            top_left=th.CARD_RADIUS, top_right=th.CARD_RADIUS, bottom_left=0, bottom_right=0
        ),
        padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )
    days_left = (date(selected.year, 12, 31) - selected).days
    body = ft.Column(
        [
            ft.Container(expand=True),
            ft.Text(str(selected.day), size=86, color=th.TEXT, weight="bold", text_align=ft.TextAlign.CENTER),
            ft.Text(
                f"第{day_of_year(selected)}天 · 第{iso_week(selected)}周",
                size=13, color=th.TEXT_MUTED, text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                f"{lunar_text(selected).split(' ')[0]} {weekday_cn(selected)}",
                size=18, color=th.TEXT, text_align=ft.TextAlign.CENTER,
            ),
            ft.Container(expand=True),
            ft.Text(
                f"距今年结束还有 {days_left} 天",
                size=th.FONT_CAPTION, color=th.TEXT_MUTED, text_align=ft.TextAlign.CENTER,
                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
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
        bgcolor=th.CARD,
        border=th.card_border(),
        border_radius=th.CARD_RADIUS,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        width=width,
        height=height,
    )


def _stat_card(title: str, value: str, accent: bool = False, width: int | None = None, height: int | None = None) -> ft.Container:
    return card(
        ft.Text(
            value, size=th.FONT_METRIC, color=th.ACCENT if accent else th.TEXT,
            weight="bold", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
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
# 标签字号不得低于 12，因此格子/行高按 12 号字撑开；整行放在横向滚动容器里，
# 窗口变窄时左右滚动，而不是把内容压小或者溢出。
_HEAT_CELL_W = 10
_HEAT_CELL_H = 16
_HEAT_GAP = 2
_HEAT_LABEL_W = 26


def _heatmap_cell(day: date, count: int, on_open) -> ft.Container:
    level = 0 if count == 0 else min(5, max(1, (count + 1) // 2))
    return ft.Container(
        width=_HEAT_CELL_W,
        height=_HEAT_CELL_H,
        bgcolor=th.HEATMAP_LEVELS[level],
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
            content=ft.Text(_WEEKDAY_LABELS[weekday], size=th.FONT_CAPTION,
                            color=th.TEXT_MUTED),
            width=_HEAT_LABEL_W,
            height=_HEAT_CELL_H,
            alignment=ft.Alignment.CENTER_RIGHT,
        )
        cells = ft.Row(
            [_heatmap_cell(week[weekday], counts.get(week[weekday], 0), on_open) for week in weeks],
            spacing=_HEAT_GAP,
        )
        rows.append(ft.Row([label, cells], spacing=4))

    # 12 号字的月份标签放不进 10px 宽的列，改为在标题下方给一行区间说明。
    grid = ft.Column(rows, spacing=_HEAT_GAP)
    legend = ft.Row(
        [
            ft.Text("少", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
            *[
                ft.Container(width=10, height=10, bgcolor=th.HEATMAP_LEVELS[i], border_radius=3)
                for i in range(6)
            ],
            ft.Text("多", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
        ],
        spacing=3,
        alignment=ft.MainAxisAlignment.END,
    )
    return card(
        ft.Column(
            [
                ft.Text(
                    f"{start.year}年{start.month}月起 · 截至 {end.year}年{end.month}月",
                    size=th.FONT_CAPTION,
                    color=th.TEXT_MUTED,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                # 窄窗口下横向滚动，绝不通过压小字号来塞进可视区。
                ft.Row([grid], scroll=ft.ScrollMode.AUTO),
                legend,
            ],
            spacing=8,
        ),
        title="活跃度热力图",
        height=height,
        trailing=ft.Row(
            [
                ft.TextButton(f"{year - 1}", on_click=lambda e: set_year(year - 1)),
                ft.Text(str(year), size=12, color=th.PRIMARY, weight="bold"),
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


def _view_switch(refresh) -> ft.Row:
    """聚焦 / 概览切换：首页默认是行动页，需要时再回看仪表盘。"""
    def set_view(view: str) -> None:
        _HOME_STATE["view"] = view
        refresh()

    return ft.Row(
        [
            ft.Button(
                "今日聚焦",
                bgcolor=th.PRIMARY if _HOME_STATE["view"] == "focus" else th.PRIMARY_LIGHT,
                color=th.ON_PRIMARY if _HOME_STATE["view"] == "focus" else th.TEXT_MUTED,
                elevation=0,
                on_click=lambda e: set_view("focus"),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=14),
                ),
            ),
            ft.Button(
                "数据概览",
                bgcolor=th.PRIMARY if _HOME_STATE["view"] == "overview" else th.PRIMARY_LIGHT,
                color=th.ON_PRIMARY if _HOME_STATE["view"] == "overview" else th.TEXT_MUTED,
                elevation=0,
                on_click=lambda e: set_view("overview"),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=14),
                ),
            ),
        ],
        spacing=6,
    )


def _focus_page(page, refresh, data, on_open_diary, on_open_note, navigate) -> ft.Control:
    from . import focus as focus_ui

    return ft.Column(
        [
            page_header(
                "首页",
                f"今日聚焦 · {data['today'].isoformat()} {weekday_cn(data['today'])}"
                + (f" · {data['weekday_festival']}" if data["weekday_festival"] else ""),
                actions=[_view_switch(refresh)],
            ),
            ft.Container(
                focus_ui.render(page, refresh, data, on_open_diary, on_open_note, navigate),
                expand=True,
            ),
        ],
        spacing=8,
        expand=True,
    )


def build(page: ft.Page, refresh, on_open_diary, on_open_note, navigate=None) -> ft.Control:
    """首页：查询后台化，避免数据量大时阻塞 UI 线程。"""
    today = date.today()
    if navigate is None:
        navigate = lambda key: None
    # 历史状态可能残留未来年份（旧版本允许切换），进来先纠正。
    if _HOME_STATE["year"] > today.year:
        _HOME_STATE["year"] = today.year
    view = _HOME_STATE["view"]
    _HOME_TOKEN.bump()  # 新一轮渲染，上一轮迟到的结果自动作废

    def load():
        if view == "focus":
            from ..services.focus import today_focus

            return {"view": "focus", "focus": today_focus(today)}
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
        return {
            "view": "home",
            "summary": summary,
            "assets": assets,
            "todo_today": todo_today,
            "todo_soon": todo_soon,
            "note": note,
        }

    def render(payload: dict) -> ft.Control:
        if payload.get("view") == "focus":
            return _focus_page(
                page, refresh, payload["focus"], on_open_diary, on_open_note, navigate
            )
        return _render_home(
            page, refresh, on_open_diary, on_open_note,
            payload["summary"], payload["assets"],
            payload["todo_today"], payload["todo_soon"], payload["note"],
        )

    return background_section(page, load, render, "加载首页…", token=_HOME_TOKEN)


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
        rows: list[ft.Control] = []
        for item in items:
            overdue = bool(item.due_date and item.due_date < today)
            due_label = (
                ("已过期 · " if overdue else "")
                + (item.due_date.isoformat() if item.due_date else "")
                + (f" {item.due_time}" if item.due_date and item.due_time else "")
            )
            # 日期放第二行：12 号字放同一行会把标题挤没。
            rows.append(
                ft.Row(
                    [
                        ft.Icon(ico("RADIO_BUTTON_UNCHECKED_OUTLINED"), size=16,
                                color=th.TEXT_MUTED),
                        ft.Column(
                            [
                                ft.Text(
                                    item.title, size=th.FONT_BODY,
                                    color=th.DANGER if overdue else th.TEXT,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.Text(
                                    due_label or "未设置日期", size=th.FONT_CAPTION,
                                    color=th.DANGER if overdue else th.TEXT_MUTED,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=1,
                            tight=True,
                            expand=True,
                        ),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
        return ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO)

    note_body = (
        ft.Column(
            [
                ft.Text(note.title, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold",
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text((note.content or "").replace("\n", " ")[:100] or "（暂无正文）",
                        size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=3,
                        overflow=ft.TextOverflow.ELLIPSIS),
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
                            content=ft.Icon(ico(QUICK[key]), size=22, color=th.PRIMARY),
                            width=36, height=36, bgcolor=th.PRIMARY_LIGHT, border_radius=10,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Text(label, size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                                text_align=ft.TextAlign.CENTER, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
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
            page_header(
                "首页",
                f"数据概览 · {today.isoformat()} {weekday_cn(today)}",
                actions=[_view_switch(refresh)],
            ),
            ft.Row(
                [
                    ft.Column(
                        [
                            # 热力图内容约需 252px，228 会把图例顶出卡片、叠到下方统计卡上。
                            _heatmap_card(_HOME_STATE["year"], open_day, set_year, height=256),
                            equal_height_row(
                                [
                                    _stat_card("本月收入", money(summary["income"]), height=96),
                                    _stat_card("本月支出", money(summary["expense"]), accent=True, height=96),
                                    _stat_card("总资产", money(assets), height=96),
                                ],
                                spacing=12,
                                height=96,
                            ),
                        ],
                        spacing=12,
                        expand=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    # 日历高度 = 热力图 256 + 间距 12 + 统计卡 96，与左列严格对齐
                    _calendar_card(_HOME_STATE["selected_date"], set_date, width=280, height=364),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            equal_height_row(
                [
                    card(todo_rows(todo_today, "今天没有任务"), title="今日任务", expand=1, height=180),
                    card(todo_rows(todo_soon, "最近 7 天没有任务"), title="最近 7 天", expand=1, height=180),
                    card(note_body, title="随机笔记", on_click=open_note if note else None,
                         expand=1, height=180),
                ],
                spacing=12,
                height=180,
            ),
            card(quick, title="快捷入口", padding=ft.Padding.symmetric(vertical=8, horizontal=16)),
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
