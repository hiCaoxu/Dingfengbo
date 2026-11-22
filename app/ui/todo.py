"""任务：左侧月历 + 四象限，中间任务列表，右侧习惯打卡。

待办与习惯合并为「任务」模块：日期与优先级决定时间视角，四象限负责取舍，
右侧习惯强调「每天要做的事」，三者共用同一份状态与刷新链路。
"""

from __future__ import annotations

from datetime import date
from uuid import NAMESPACE_URL, uuid5

import flet as ft
from sqlalchemy import select

from ..db.models import Todo, utcnow
from ..db.repo import create, update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.dates import lunar_md
from ..services.reminders import describe as describe_reminders
from ..services.sync import mark_pending_write
from ..services.habits import overview as habits_overview
from ..services.todo_rules import describe as describe_rule
from ..services.todo_rules import next_occurrence
from ..services.todo_views import (
    QUADRANTS,
    QUADRANT_HINTS,
    QUADRANT_LABELS,
    WEEK_LABELS,
    counts_by_date,
    group_by_quadrant,
    month_grid,
    quadrant_counts,
    quadrant_of,
    shift_month,
)
from .. import theme as th
from .async_utils import background_section
from .forms import open_todo_dialog, tags_text
from .habits import render_panel as render_habits_panel
from .pomodoro import open_pomodoro_dialog
from .state import state
from .widgets import (
    card,
    delete_with_confirm,
    divider,
    empty,
    icon_button,
    page_header,
    primary_button,
    tag_chip,
)

_state = state.todo

LEFT_WIDTH = 288
RIGHT_WIDTH = 272
# 每个象限格子里最多列出的任务条数，其余折成「还有 N 项」
QUADRANT_PREVIEW_LIMIT = 3


def _quadrant_color(key: str) -> str:
    """象限配色随主题切换（不能在导入时把颜色钉死）。"""
    return {
        "q1": th.DANGER,
        "q2": th.PRIMARY,
        "q3": th.ACCENT,
        "q4": th.TEXT_MUTED,
    }.get(key, th.TEXT_MUTED)


def _all_tags(items) -> list[str]:
    tags: set[str] = set()
    for item in items:
        tags.update(tags_text(item.tags).split(", "))
    return sorted(t for t in tags if t)


def _occurrence_id(todo_id: str, due: date) -> str:
    """循环任务下一期的确定性 id：同一 (任务, 到期日) 全库只可能有一行。

    没有它时「完成 → 取消完成 → 再完成」会给同一个到期日再建一行，
    列表里出现两条一模一样的任务。
    """
    return str(uuid5(NAMESPACE_URL, f"todo-recur:{todo_id}:{due.isoformat()}"))


def toggle(page: ft.Page, todo_id: str, refresh) -> None:
    with session_scope() as session:
        item = session.get(Todo, todo_id)
        if item is None:
            return
        if not item.completed and item.repeat_rule not in ("none",) and not item.is_template:
            due = item.due_date or date.today()
            following = next_occurrence(
                due, item.repeat_rule, item.repeat_config, bool(item.lunar_reminder)
            )
            if following:
                occurrence_id = _occurrence_id(item.id, following)
                existing = session.get(Todo, occurrence_id)
                if existing is None:
                    create(
                        session, Todo,
                        id=occurrence_id,
                        title=item.title, note=item.note, due_date=following,
                        due_time=item.due_time, tags=item.tags,
                        lunar_reminder=lunar_md(following) if item.lunar_reminder else "",
                        repeat_rule=item.repeat_rule,
                        repeat_config=item.repeat_config,
                        remind_days=item.remind_days,
                        remind_days_before=item.remind_days_before,
                        remind_time=item.remind_time,
                        priority=item.priority, quadrant=item.quadrant,
                        completed=False, is_template=False,
                    )
                elif existing.is_deleted:
                    # 上一期被删过：恢复它，而不是再插一行同 id
                    update(session, existing, is_deleted=False, completed=False,
                           completed_at=None, is_template=False)
            update(session, item, completed=True, completed_at=utcnow(), is_template=True)
        else:
            un_completing = bool(item.completed)
            update(
                session, item,
                completed=not item.completed,
                completed_at=None if un_completing else utcnow(),
                # 取消完成必须把「循环模板」标记一起复位：否则这一行会从「未完成」
                # 视图消失，却仍在「全部」里显示为未完成——状态自相矛盾。
                is_template=False if un_completing else item.is_template,
            )
    mark_pending_write()
    refresh()


def delete(page: ft.Page, todo_id: str, refresh) -> None:
    delete_with_confirm(page, Todo, todo_id, refresh, "确定删除该任务？")


def _show_completed() -> bool:
    return _state.status in ("已完成", "全部")


def _query() -> dict:
    """一次查回任务与习惯，三栏共用一个后台加载结果。"""
    with session_scope() as session:
        todos = list(
            session.scalars(
                select(Todo)
                .where(Todo.is_deleted.is_(False))
                .order_by(Todo.due_date, Todo.due_time, Todo.id)
            )
        )
    return {"todos": todos, "habits": habits_overview()}


def build(page: ft.Page, refresh) -> ft.Control:
    # 查询放后台线程：任务量增长后不再阻塞 UI。
    return background_section(page, _query, lambda data: _render(page, refresh, data))


def _set_month(page, refresh, delta: int) -> None:
    year, month = shift_month(_state.year, _state.month, delta)
    _state.year, _state.month = year, month
    _state.date = None
    refresh()


def _go_today(page, refresh) -> None:
    today = date.today()
    _state.year, _state.month = today.year, today.month
    _state.date = today.isoformat()
    refresh()


def _toggle_date(page, refresh, day: date) -> None:
    iso = day.isoformat()
    _state.date = None if _state.date == iso else iso
    _state.year, _state.month = day.year, day.month
    refresh()


def _calendar_card(page, refresh, items, today: date) -> ft.Control:
    counts = counts_by_date(items)
    grid = month_grid(_state.year, _state.month)
    selected = _state.date
    month_total = sum(
        count
        for day, count in counts.items()
        if day.year == _state.year and day.month == _state.month
    )
    overdue = sum(
        1 for item in items
        if item.due_date and item.due_date < today and not item.completed and not item.is_template
    )

    def header() -> ft.Row:
        return ft.Row(
            [
                ft.IconButton(
                    icon=ico(ACTIONS["chevron_left"]), icon_size=16,
                    icon_color=th.TEXT_MUTED, tooltip="上一月",
                    on_click=lambda e: _set_month(page, refresh, -1),
                ),
                ft.Text(f"{_state.year} 年 {_state.month} 月", size=th.FONT_CARD_TITLE,
                        color=th.TEXT, weight="bold"),
                ft.IconButton(
                    icon=ico(ACTIONS["chevron_right"]), icon_size=16,
                    icon_color=th.TEXT_MUTED, tooltip="下一月",
                    on_click=lambda e: _set_month(page, refresh, 1),
                ),
                ft.Container(expand=True),
                ft.TextButton("今天", on_click=lambda e: _go_today(page, refresh)),
            ],
            spacing=2,
        )

    def cell(day: date) -> ft.Control:
        in_month = day.month == _state.month
        is_today = day == today
        is_selected = selected == day.isoformat()
        count = counts.get(day, 0)
        color = th.TEXT if in_month else th.DIVIDER
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        f"{day.day}", size=th.FONT_CAPTION,
                        color=th.ON_PRIMARY if is_selected else (th.PRIMARY if is_today else color),
                        weight="bold" if (is_today or is_selected) else None,
                    ),
                    ft.Container(
                        width=5, height=5, border_radius=3,
                        bgcolor=th.ON_PRIMARY if is_selected else (th.PRIMARY if count else "#00000000"),
                    ),
                ],
                spacing=1,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            height=36,
            expand=True,
            bgcolor=th.PRIMARY if is_selected else None,
            border=None if (is_selected or not is_today) else ft.Border.all(1, th.PRIMARY),
            border_radius=6,
            tooltip=f"{day.isoformat()} · {count} 项未完成" if count else day.isoformat(),
            on_click=lambda e, d=day: _toggle_date(page, refresh, d),
        )

    return card(
        ft.Column(
            [
                header(),
                ft.Row(
                    [ft.Text(label, size=th.FONT_CAPTION, color=th.TEXT_MUTED, expand=True,
                             text_align=ft.TextAlign.CENTER, max_lines=1)
                     for label in WEEK_LABELS],
                    spacing=2,
                ),
                ft.Column(
                    [ft.Row([cell(day) for day in week], spacing=2) for week in grid],
                    spacing=2,
                ),
                divider(),
                ft.Row(
                    [
                        ft.Text(f"本月 {month_total} 项", size=th.FONT_CAPTION,
                                color=th.TEXT_MUTED),
                        ft.Text(f"逾期 {overdue}" if overdue else "", size=th.FONT_CAPTION,
                                color=th.DANGER),
                        ft.Container(expand=True),
                        ft.TextButton(
                            "清除日期",
                            on_click=lambda e: _clear_date(page, refresh),
                            visible=bool(selected),
                        ),
                    ],
                    spacing=8,
                ),
            ],
            spacing=6,
        ),
        padding=ft.Padding.symmetric(vertical=12, horizontal=12),
    )


def _clear_date(page, refresh) -> None:
    _state.date = None
    refresh()


def _quadrant_card(page, refresh, items, today: date) -> ft.Control:
    grouped = group_by_quadrant(items, today)
    counts = quadrant_counts(items, today)

    def cell(key: str) -> ft.Control:
        label, _hint = next(
            (name, hint) for qkey, name, hint in QUADRANTS if qkey == key
        )
        active = _state.quadrant == key
        color = _quadrant_color(key)
        # 到期日近的排在前面，没有日期的排最后，格子里先看到最该做的事
        bucket = sorted(
            grouped[key],
            key=lambda it: (it.due_date is None, it.due_date or today),
        )
        preview = bucket[:QUADRANT_PREVIEW_LIMIT]
        rest = len(bucket) - len(preview)

        def entry(item: Todo) -> ft.Control:
            overdue = bool(item.due_date and item.due_date < today)
            due = f" {item.due_date.strftime('%m/%d')}" if item.due_date else ""
            return ft.Container(
                content=ft.Text(
                    f"{item.title}{due}",
                    size=th.FONT_CAPTION,
                    color=th.DANGER if overdue else th.TEXT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                padding=ft.Padding.symmetric(vertical=1, horizontal=3),
                border_radius=4,
                tooltip=f"完成「{item.title}」",
                on_click=lambda e, i=item.id: toggle(page, i, refresh),
            )

        body: list[ft.Control] = [entry(item) for item in preview]
        if rest:
            body.append(ft.Text(f"还有 {rest} 项", size=th.FONT_CAPTION, color=th.TEXT_MUTED))
        if not bucket:
            body.append(ft.Text("暂无任务", size=th.FONT_CAPTION, color=th.TEXT_MUTED))

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(width=3, height=20, bgcolor=color, border_radius=2),
                            ft.Column(
                                [
                                    ft.Text(label, size=th.FONT_CARD_TITLE, color=th.TEXT,
                                            weight="bold", max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(QUADRANT_HINTS[key], size=th.FONT_CAPTION,
                                            color=th.TEXT_MUTED, max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                            tooltip=QUADRANT_HINTS[key]),
                                ],
                                spacing=0,
                                tight=True,
                                expand=True,
                            ),
                            ft.Text(str(counts[key]), size=16, color=color, weight="bold"),
                        ],
                        spacing=6,
                    ),
                    # 内容多了在格子内部滚动，不让格子被内容撑高
                    ft.Column(body, spacing=2, tight=True, scroll=ft.ScrollMode.AUTO,
                              expand=True),
                ],
                spacing=2,
                tight=True,
                expand=True,
            ),
            padding=ft.Padding.all(9),
            expand=True,
            alignment=ft.Alignment.TOP_LEFT,
            bgcolor=th.PRIMARY_LIGHT if active else th.CARD,
            border=ft.Border.all(2 if active else 1, color if active else th.DIVIDER),
            border_radius=10,
            tooltip=f"只看「{label}」" if not active else "取消象限筛选",
            on_click=lambda e, k=key: _toggle_quadrant(page, refresh, k),
        )

    # expand 让四象限吃掉左侧栏剩下的高度，格子随可用空间变大
    return card(
        ft.Column(
            [
                ft.Text("四象限", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
                ft.Text(
                    "高/中优先级=重要 · 今明到期/逾期=紧急",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Row(
                    [cell("q1"), cell("q2")],
                    spacing=8,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                ft.Row(
                    [cell("q3"), cell("q4")],
                    spacing=8,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            ],
            spacing=8,
            expand=True,
        ),
        padding=ft.Padding.all(12),
        expand=True,
    )


def _toggle_quadrant(page, refresh, key: str) -> None:
    _state.quadrant = "" if _state.quadrant == key else key
    refresh()


def _row_for(page, refresh, item: Todo, today: date, overdue: bool) -> ft.Control:
    due = item.due_date.isoformat() if item.due_date else "无到期日"
    if item.due_time:
        due += f" {item.due_time}"
    label = "已过期 · " if overdue else ""
    extras: list[str] = []
    rule_text = describe_rule(item.repeat_rule, item.repeat_config)
    if rule_text:
        extras.append(rule_text)
    remind_text = describe_reminders(item)
    if remind_text:
        extras.append(remind_text)
    if item.pomodoro_count:
        extras.append(f"番茄 {item.pomodoro_count}")
    if item.priority:
        extras.append(item.priority)
    if tags_text(item.tags):
        extras.append(tags_text(item.tags))
    quadrant = quadrant_of(item, today)
    return ft.Container(
        content=ft.Row(
            [
                ft.Checkbox(
                    value=item.completed,
                    on_change=lambda e, i=item.id: toggle(page, i, refresh),
                ),
                ft.Container(
                    width=6, height=6, border_radius=3,
                    bgcolor=_quadrant_color(quadrant),
                    tooltip=QUADRANT_LABELS[quadrant],
                ),
                # ListView 的条目高度由内容决定（无界）：内层 Column 不能用 expand，
                # 否则 Flet/Flutter 会因「无界高度里出现 flex」整块渲染成灰色。
                # 需要横向撑满时用 Container(expand=True) 包住 Column。
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                item.title + (" · 循环模板" if item.is_template else ""),
                                size=th.FONT_CARD_TITLE,
                                color=th.DANGER if overdue
                                else (th.TEXT_MUTED if item.completed else th.TEXT),
                                weight="bold",
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                f"{label}{due}" + (" · " + " · ".join(extras) if extras else ""),
                                size=th.FONT_CAPTION,
                                color=th.DANGER if overdue else th.TEXT_MUTED,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                    ),
                    expand=True,
                ),
                icon_button(ACTIONS["edit"], lambda e, i=item: open_todo_dialog(page, i, refresh), "编辑"),
                icon_button(ACTIONS["delete"], lambda e, i=item.id: delete(page, i, refresh), "删除"),
            ],
            spacing=8,
        ),
        bgcolor=th.ACCENT_LIGHT if overdue else th.CARD,
        border=ft.Border.all(1, th.DIVIDER),
        border_radius=10,
        padding=ft.Padding.symmetric(vertical=9, horizontal=10),
    )


def _render(page: ft.Page, refresh, data) -> ft.Control:
    items: list[Todo] = data["todos"]
    tags = _all_tags(items)
    today = date.today()
    if _state.year > today.year + 5 or _state.month not in range(1, 13):
        _state.year, _state.month = today.year, today.month

    def matches(item: Todo) -> bool:
        if item.is_template and not _show_completed():
            return False
        if _state.status == "未完成" and item.completed:
            return False
        if _state.status == "已完成" and not item.completed:
            return False
        if _state.tag != "全部" and _state.tag not in tags_text(item.tags).split(", "):
            return False
        if _state.date and (item.due_date is None or item.due_date.isoformat() != _state.date):
            return False
        if _state.quadrant and quadrant_of(item, today) != _state.quadrant:
            return False
        return True

    shown = [item for item in items if matches(item)]
    overdue_ids = {
        item.id for item in shown if item.due_date and item.due_date < today and not item.completed
    }
    shown.sort(key=lambda item: (item.id not in overdue_ids, item.due_date or date.max))

    middle_card = card(
        ft.Column(
            [
                ft.Text("任务列表", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
                ft.Row(
                    [
                        ft.Dropdown(
                            label="状态",
                            value=_state.status,
                            options=[ft.dropdown.Option(v) for v in ["未完成", "已完成", "全部"]],
                            width=104,
                            label_style=ft.TextStyle(size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                            text_style=ft.TextStyle(size=th.FONT_BODY, color=th.TEXT),
                            on_select=lambda e: (_state.__setitem__("status", e.control.value), refresh()),
                        ),
                        ft.Dropdown(
                            label="标签",
                            value=_state.tag,
                            options=[ft.dropdown.Option(v) for v in ["全部", *tags]],
                            width=118,
                            label_style=ft.TextStyle(size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                            text_style=ft.TextStyle(size=th.FONT_BODY, color=th.TEXT),
                            on_select=lambda e: (_state.__setitem__("tag", e.control.value), refresh()),
                        ),
                        ft.Container(expand=True),
                        ft.Text(f"{len(shown)} 项", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                    ],
                    spacing=8,
                    # 这里绝不能加 wrap=True：Wrap 内出现 expand 的间隔器时，
                    # 整列布局会失败，Flet 会把下方列表渲染成一整块灰色空白。
                ),
                ft.Row(
                    [
                        *(
                            [tag_chip(f"{_state.date} ✕", True,
                                      lambda e: _clear_date(page, refresh))]
                            if _state.date else []
                        ),
                        *(
                            [tag_chip(f"{QUADRANT_LABELS[_state.quadrant]} ✕", True,
                                      lambda e: _toggle_quadrant(page, refresh, _state.quadrant))]
                            if _state.quadrant else []
                        ),
                        *(
                            [tag_chip(f"#{tag}", _state.tag == tag,
                                      lambda e, t=tag: (_state.__setitem__("tag", t), refresh()))
                             for tag in tags[:8]]
                        ),
                    ],
                    spacing=6,
                    wrap=True,
                    # 没有任何筛选条件时不渲染这一行，避免空 Wrap 参与布局
                    visible=bool(_state.date or _state.quadrant or tags),
                ),
                divider(),
                (
                    ft.ListView(
                        [_row_for(page, refresh, item, today, item.id in overdue_ids)
                         for item in shown],
                        spacing=8,
                        padding=0,
                        expand=True,
                    )
                    if shown
                    else empty("暂无符合条件的任务")
                ),
            ],
            spacing=10,
            expand=True,
        ),
        expand=True,
    )

    left_column = ft.Column(
        [
            _calendar_card(page, refresh, items, today),
            _quadrant_card(page, refresh, items, today),
        ],
        width=LEFT_WIDTH,
        spacing=12,
        expand=True,
    )
    right_column = ft.Column(
        [render_habits_panel(page, refresh, data["habits"])],
        width=RIGHT_WIDTH,
        spacing=12,
        expand=True,
    )
    # STRETCH 很关键：Row 的 START 对齐会给子项「无界高度」，三栏内部的
    # expand/滚动区因拿不到高度会被 Flet 渲染成灰块。
    columns = ft.Row(
        [left_column, ft.Column([middle_card], expand=True), right_column],
        spacing=12,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    return ft.Column(
        [
            page_header(
                "任务",
                f"{len(shown)} 项在列表中" + (f" · 逾期 {len(overdue_ids)} 项" if overdue_ids else ""),
                actions=[
                    primary_button(
                        "番茄钟",
                        lambda e: open_pomodoro_dialog(page, refresh),
                        ACTIONS["timer"],
                    ),
                    primary_button(
                        "新增任务",
                        lambda e: open_todo_dialog(page, None, refresh),
                        ACTIONS["add"],
                    ),
                ],
            ),
            columns,
        ],
        spacing=10,
        expand=True,
    )
