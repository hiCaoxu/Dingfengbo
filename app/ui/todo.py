"""待办：排序、筛选、增删改、完成与循环实例。"""

from __future__ import annotations

from datetime import date

import flet as ft
from sqlalchemy import select

from ..db.models import Todo, utcnow
from ..db.repo import create, update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.dates import lunar_md
from ..services.reminders import describe as describe_reminders
from ..services.sync import mark_pending_write
from ..services.todo_rules import describe as describe_rule
from ..services.todo_rules import next_occurrence
from ..theme import DANGER, DIVIDER, PRIMARY_LIGHT, TEXT, TEXT_MUTED
from .async_utils import background_section
from .forms import open_todo_dialog, tags_text
from .pomodoro import open_pomodoro_dialog
from .state import state
from .widgets import card, delete_with_confirm, divider, empty, icon_button, primary_button, section_header, tag_chip

_state = state.todo


def _all_tags(items) -> list[str]:
    tags: set[str] = set()
    for item in items:
        tags.update(tags_text(item.tags).split(", "))
    return sorted(t for t in tags if t)


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
                create(
                    session, Todo,
                    title=item.title, note=item.note, due_date=following,
                    due_time=item.due_time, tags=item.tags,
                    lunar_reminder=lunar_md(following) if item.lunar_reminder else "",
                    repeat_rule=item.repeat_rule,
                    repeat_config=item.repeat_config,
                    remind_days=item.remind_days,
                    remind_days_before=item.remind_days_before,
                    remind_time=item.remind_time,
                    priority=item.priority, completed=False, is_template=False,
                )
            update(session, item, completed=True, completed_at=utcnow(), is_template=True)
        else:
            update(session, item, completed=not item.completed,
                   completed_at=utcnow() if not item.completed else None)
    mark_pending_write()
    refresh()


def delete(page: ft.Page, todo_id: str, refresh) -> None:
    delete_with_confirm(page, Todo, todo_id, refresh, "确定删除该待办？")


def _query() -> list[Todo]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Todo)
                .where(Todo.is_deleted.is_(False))
                .order_by(Todo.due_date, Todo.due_time, Todo.id)
            )
        )


def build(page: ft.Page, refresh) -> ft.Control:
    # 查询放后台线程：待办量增长后不再阻塞 UI。
    return background_section(page, _query, lambda items: _render(page, refresh, items))


def _render(page: ft.Page, refresh, items) -> ft.Control:
    tags = _all_tags(items)
    today = date.today()

    def visible():
        result = []
        for item in items:
            if not item.is_template:
                if _state["status"] == "未完成" and item.completed:
                    continue
                if _state["status"] == "已完成" and not item.completed:
                    continue
            elif _state["status"] == "未完成":
                continue
            if _state["tag"] != "全部" and _state["tag"] not in tags_text(item.tags).split(", "):
                continue
            result.append(item)
        overdue = [i for i in result if i.due_date and i.due_date < today and not i.completed]
        normal = [i for i in result if i not in overdue]
        return overdue + normal, set(overdue)

    shown, overdue_ids = visible()

    def row_for(item: Todo) -> ft.Control:
        overdue = item.id in overdue_ids
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
        return ft.Container(
            content=ft.Row(
                [
                    ft.Checkbox(value=item.completed, on_change=lambda e, i=item.id: toggle(page, i, refresh)),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(item.title, size=13, color=DANGER if overdue else (TEXT_MUTED if item.completed else TEXT),
                                            weight="bold"),
                                    ft.Text("循环模板", size=9, color=TEXT_MUTED,
                                            visible=item.is_template),
                                ],
                                spacing=8,
                            ),
                            ft.Text(
                                f"{label}{due}" + (" · " + " · ".join(extras) if extras else ""),
                                size=11, color=DANGER if overdue else TEXT_MUTED,
                            ),
                        ],
                        spacing=4, expand=True,
                    ),
                    icon_button(ACTIONS["edit"], lambda e, i=item: open_todo_dialog(page, i, refresh), "编辑"),
                    icon_button(ACTIONS["delete"], lambda e, i=item.id: delete(page, i, refresh), "删除"),
                ],
                spacing=10,
            ),
            bgcolor="#FFFFFF" if not overdue else "#FDECEA",
            border=ft.Border.all(1, DIVIDER),
            border_radius=10,
            padding=ft.Padding.symmetric(vertical=10, horizontal=12),
        )

    filters = ft.Row(
        [
            ft.Dropdown(
                label="状态",
                value=_state["status"],
                options=[ft.dropdown.Option(v) for v in ["全部", "未完成", "已完成"]],
                width=120,
                on_select=lambda e: (_state.__setitem__("status", e.control.value), refresh()),
            ),
            ft.Dropdown(
                label="标签",
                value=_state["tag"],
                options=[ft.dropdown.Option(v) for v in ["全部", *tags]],
                width=150,
                on_select=lambda e: (_state.__setitem__("tag", e.control.value), refresh()),
            ),
            ft.Row([tag_chip(tag, _state["tag"] == tag,
                             lambda e, t=tag: (_state.__setitem__("tag", t), refresh()))
                    for tag in tags], wrap=True, spacing=6),
        ],
        spacing=10,
        wrap=True,
    )

    return card(
        ft.Column(
            [
                section_header(
                    "全部待办",
                    ft.Row(
                        [
                            primary_button(
                                "番茄钟",
                                lambda e: open_pomodoro_dialog(page, refresh),
                                ACTIONS["timer"],
                            ),
                            primary_button(
                                "新增待办",
                                lambda e: open_todo_dialog(page, None, refresh),
                                ACTIONS["add"],
                            ),
                        ],
                        spacing=8,
                    ),
                ),
                filters,
                divider(),
                ft.Column([row_for(item) for item in shown], spacing=8) if shown else empty("暂无符合条件的待办"),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
