"""时间轴模块：跨模块生活时间线、统一标签与记录关联。"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta

import flet as ft

from .. import theme as th
from ..icons import ico
from ..services.dates import weekday_cn
from ..services.timeline import (
    MODULE_META,
    TimelineItem,
    collect,
    create_link,
    delete_link,
    link_targets,
    links_for,
    tag_counts,
)
from .async_utils import background_section
from .state import state
from .widgets import card, empty, modal, page_header, primary_button, snack, tag_chip

logger = logging.getLogger(__name__)

RANGE_OPTIONS = {"近 7 天": 7, "近 30 天": 30, "近 90 天": 90, "近一年": 365}

MODULE_ICONS = {
    "todo": "CHECK_CALENDAR_OUTLINED",
    "diary": "AUTO_STORIES_OUTLINED",
    "notes": "DESCRIPTION_OUTLINED",
    "exercise": "FITNESS_CENTER_OUTLINED",
    "diet": "RESTAURANT_OUTLINED",
    "weight": "MONITOR_HEART_OUTLINED",
    "sleep": "BEDTIME_OUTLINED",
    "fun": "MOVIE_OUTLINED",
    "finance": "ACCOUNT_BALANCE_WALLET_OUTLINED",
    "habit": "CHECK_CIRCLE_OUTLINE",
}


def _load(days: int, module: str, tag: str | None):
    today = date.today()
    start = today - timedelta(days=days - 1)
    modules = None if module == "全部" else [module]
    items = collect(start, today, modules=modules, tag=tag)
    tags = tag_counts(days=days, today=today)
    return {"items": items, "tags": tags, "start": start, "end": today}


def build(page: ft.Page, refresh, navigate, on_open_note, on_open_diary) -> ft.Control:
    days = state.timeline.days
    return background_section(
        page,
        lambda: _load(days, state.timeline.module, state.timeline.tag),
        lambda data: _render(page, refresh, navigate, on_open_note, on_open_diary, data),
        placeholder="正在整理时间线…",
    )


def _open_item(page, navigate, on_open_note, on_open_diary, item: TimelineItem) -> None:
    if item.module == "notes" and on_open_note is not None:
        # 时间轴只有摘要，进入预览前按 id 重新取一次完整记录。
        from ..db.models import Note
        from ..db.session import session_scope

        with session_scope() as session:
            note = session.get(Note, item.record_id)
        if note is not None:
            on_open_note(note)
            return
    if item.module == "diary" and on_open_diary is not None:
        on_open_diary(item.day)
        return
    navigate(MODULE_META.get(item.module, (item.module, item.module))[1])


def open_links_dialog(page: ft.Page, item: TimelineItem, refresh) -> ft.AlertDialog:
    """查看/新增/删除某条记录的跨模块关联。"""
    links_box = ft.Column(spacing=6)
    targets_box = ft.Column(spacing=2, height=180, scroll=ft.ScrollMode.AUTO)
    search_field = ft.TextField(
        hint_text="搜索要关联的任务/笔记/日记/娱乐…",
        dense=True,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        text_style=ft.TextStyle(size=12, color=th.TEXT),
        content_padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )
    selected = {"value": None}

    def render_links() -> None:
        rows = links_for(item.table, item.record_id)
        if not rows:
            links_box.controls = [ft.Text("还没有关联", size=th.FONT_CAPTION,
                                          color=th.TEXT_MUTED)]
            return
        links_box.controls = [
            ft.Row(
                [
                    ft.Text(link["label"], size=th.FONT_CAPTION, color=th.PRIMARY),
                    ft.Text(link["title"], size=th.FONT_BODY, color=th.TEXT, expand=True,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.TextButton(
                        "取消关联",
                        on_click=lambda e, lid=link["id"]: _remove(lid),
                    ),
                ],
                spacing=8,
            )
            for link in rows
        ]

    def _remove(link_id: str) -> None:
        delete_link(link_id)
        render_links()
        page.update()

    def render_targets(rows: list[dict]) -> None:
        controls: list[ft.Control] = []
        for row in rows:
            if row["table"] == item.table and row["record_id"] == item.record_id:
                continue

            def pick(e, target=row) -> None:
                selected["value"] = target
                for control in targets_box.controls:
                    control.bgcolor = (
                        th.PRIMARY_LIGHT
                        if getattr(control, "data", None) == target["record_id"]
                        else None
                    )
                page.update()

            controls.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Text(row["label"], size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                            ft.Text(row["title"], size=th.FONT_BODY, color=th.TEXT, expand=True,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    data=row["record_id"],
                    padding=ft.Padding.symmetric(vertical=5, horizontal=6),
                    border_radius=6,
                    on_click=pick,
                )
            )
        targets_box.controls = controls or [
            ft.Text("没有匹配记录", size=th.FONT_CAPTION, color=th.TEXT_MUTED)
        ]
        page.update()

    def add(_) -> None:
        target = selected["value"]
        if target is None:
            snack(page, "请先选择一条目标记录", th.ACCENT)
            return
        created = create_link(
            item.table, item.record_id, target["table"], target["record_id"], item.label
        )
        if created is None:
            snack(page, "这两条记录已经关联过了", th.TEXT_MUTED)
            return
        render_links()
        page.update()
        snack(page, "已建立关联", th.PRIMARY)

    # 输入即查询会每击键打一次四表 LIKE：加 250ms 防抖，且查询放到后台线程，
    # 结果回 UI 线程前检查弹窗是否已关闭（关闭后丢弃，避免操作已销毁控件）。
    pending: dict = {"timer": None, "closed": False}

    def query_in_background(text: str) -> None:
        try:
            rows = link_targets(text, limit=20)
        except Exception:
            logger.warning("关联候选查询失败", exc_info=True)
            return
        if pending["closed"]:
            return

        async def apply() -> None:
            if pending["closed"]:
                return
            render_targets(rows)

        try:
            page.run_task(apply)
        except Exception:
            logger.debug("关联弹窗已关闭，丢弃候选结果")

    def schedule_query(_=None) -> None:
        timer = pending["timer"]
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(0.25, query_in_background, args=(search_field.value or "",))
        timer.daemon = True
        pending["timer"] = timer
        timer.start()

    search_field.on_change = schedule_query

    render_links()
    query_in_background("")

    dialog = modal(
        page,
        f"关联记录 · {item.title[:24]}",
        ft.Column(
            [
                ft.Text("已有关联", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
                links_box,
                ft.Container(height=1, bgcolor=th.DIVIDER),
                ft.Text("新增关联", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
                search_field,
                targets_box,
            ],
            spacing=8,
            tight=True,
        ),
        [
            ft.TextButton("关闭", on_click=lambda e: _close()),
            primary_button("关联选中记录", add, "LINK_OUTLINED"),
        ],
        width=560,
    )

    def _close() -> None:
        from .widgets import close_modal

        pending["closed"] = True
        timer = pending["timer"]
        if timer is not None:
            timer.cancel()
        close_modal(page, dialog)
        if refresh is not None:
            refresh()

    return dialog


def _render(page, refresh, navigate, on_open_note, on_open_diary, data) -> ft.Control:
    items: list[TimelineItem] = data["items"]
    tags = data["tags"]

    def set_days(value: int):
        state.timeline.days = value
        refresh()

    def set_module(value: str):
        state.timeline.module = value
        refresh()

    def set_tag(value: str | None):
        state.timeline.tag = value
        refresh()

    range_row = ft.Row(
        [
            ft.TextButton(
                label,
                on_click=lambda e, d=days: set_days(d),
            )
            for label, days in RANGE_OPTIONS.items()
        ],
        spacing=2,
    )

    module_chips = ft.Row(
        [
            tag_chip("全部", state.timeline.module == "全部",
                     lambda e: set_module("全部")),
            *[
                tag_chip(label, state.timeline.module == key,
                         lambda e, k=key: set_module(k))
                for key, (label, _module) in MODULE_META.items()
            ],
        ],
        spacing=6,
        wrap=True,
    )

    tag_cloud = ft.Row(
        [
            tag_chip("清除标签", True, lambda e: set_tag(None)) if state.timeline.tag else ft.Container(),
            *[
                tag_chip(f"{tag} {count}", state.timeline.tag == tag,
                         lambda e, t=tag: set_tag(t))
                for tag, count, _sources in tags[:16]
            ],
        ],
        spacing=6,
        wrap=True,
    )

    # 按天分组
    groups: dict[date, list[TimelineItem]] = {}
    for item in items:
        groups.setdefault(item.day, []).append(item)

    rows: list[ft.Control] = []
    for day in sorted(groups, reverse=True):
        day_items = groups[day]
        rows.append(
            ft.Row(
                [
                    ft.Text(f"{day.month}月{day.day}日", size=th.FONT_CARD_TITLE,
                            color=th.TEXT, weight="bold"),
                    ft.Text(weekday_cn(day), size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                    ft.Container(expand=True),
                    ft.Text(f"{len(day_items)} 条", size=th.FONT_CAPTION,
                            color=th.TEXT_MUTED),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        for item in day_items:
            def open_click(e, it=item) -> None:
                _open_item(page, navigate, on_open_note, on_open_diary, it)

            rows.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Container(
                                ft.Icon(ico(MODULE_ICONS.get(item.module, "CIRCLE_OUTLINED")), size=14,
                                        color=th.PRIMARY),
                                width=26, height=26, bgcolor=th.PRIMARY_LIGHT,
                                border_radius=8, alignment=ft.Alignment.CENTER,
                            ),
                            ft.Column(
                                [
                                    ft.Text(item.title, size=th.FONT_CARD_TITLE,
                                            color=th.TEXT, weight="bold",
                                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(
                                        " · ".join(
                                            x for x in (item.label, item.detail) if x
                                        ),
                                        size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                                tight=True,
                            ),
                            ft.IconButton(
                                icon=ico("LINK"),
                                icon_size=15,
                                icon_color=th.TEXT_MUTED,
                                tooltip="查看/新增关联",
                                on_click=lambda e, it=item: open_links_dialog(page, it, None),
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=8),
                    border_radius=8,
                    on_click=open_click,
                )
            )
        rows.append(ft.Container(height=1, bgcolor=th.DIVIDER))

    if not rows:
        rows = [empty("这个时间段还没有记录")]

    header = page_header(
        "时间轴",
        f"{data['start'].isoformat()} ~ {data['end'].isoformat()} · {len(items)} 条",
        actions=[range_row],
    )

    filters = card(
        ft.Column(
            [
                ft.Text("模块", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                module_chips,
                ft.Text("标签（跨模块）", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                tag_cloud if tags else ft.Text("还没有标签，去待办或笔记里加吧",
                                               size=th.FONT_CAPTION,
                                               color=th.TEXT_MUTED),
            ],
            spacing=8,
        ),
        title="筛选",
    )

    timeline_card = card(
        # 用 ListView 而不是一次性把整列控件建出来：近一年跨模块的条目可达数千，
        # Column 会在 UI 线程同步构建全部行，切到「近一年」时明显卡住。
        ft.ListView(controls=rows, spacing=6, expand=True),
        title="生活时间线",
        expand=1,
    )

    return ft.Column(
        [
            header,
            filters,
            ft.Container(timeline_card, expand=True),
        ],
        spacing=8,
        expand=True,
    )
