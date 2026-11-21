"""娱乐：想看 / 在看 / 看完 三列看板。"""

from __future__ import annotations

import flet as ft
from sqlalchemy import select

from ..db.models import Entertainment
from ..db.repo import update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.covers import prefetch, resolve_cover
from ..services.sync import mark_pending_write
from .. import theme as th
from .async_utils import background_section
from .forms import ENTERTAINMENT_KINDS, open_entertainment_dialog
from .state import state
from .widgets import (
    delete_with_confirm,
    divider,
    empty,
    icon_button,
    page_header,
    primary_button,
)

COLUMNS = ["想看", "在看", "看完"]
_state = state.entertainment


def move(item_id: str, direction: int, page, refresh) -> None:
    with session_scope() as session:
        item = session.get(Entertainment, item_id)
        if item:
            index = COLUMNS.index(item.status) if item.status in COLUMNS else 0
            target = COLUMNS[min(len(COLUMNS) - 1, max(0, index + direction))]
            update(session, item, status=target)
    mark_pending_write()
    refresh()


def delete(item_id: str, page, refresh) -> None:
    delete_with_confirm(page, Entertainment, item_id, refresh, "确定删除该条目？")


def _placeholder_cover() -> ft.Control:
    return ft.Container(
        content=ft.Icon(ico("IMAGE_OUTLINED"), size=24, color=th.TEXT_MUTED),
        width=64, height=84, bgcolor=th.PRIMARY_LIGHT, border_radius=6,
        alignment=ft.Alignment.CENTER,
    )


def _cover(source: str, raw: str = "") -> ft.Control:
    """``source`` 已由 :func:`covers.resolve_cover` 解析成本地路径；空串走占位图。

    地址填了但解析不出来（链接 403/过期、路径不存在）时给占位图挂 tooltip，
    否则用户只会看到一张灰图，不知道是地址问题。
    """
    if source:
        return ft.Image(
            src=source, width=64, height=84, fit=ft.BoxFit.COVER, border_radius=6,
            error_content=_placeholder_cover(),
        )
    placeholder = _placeholder_cover()
    if (raw or "").strip():
        placeholder.tooltip = "封面加载失败：地址无效、图片不可访问，或豆瓣等图床链接已过期"
    return placeholder


def _item_card(item: Entertainment, cover_src: str, page, refresh) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        _cover(cover_src, item.cover or ""),
                        ft.Column(
                            [
                                ft.Text(item.title, size=th.FONT_CARD_TITLE, color=th.TEXT,
                                        weight="bold", max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(item.kind, size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(f"评分 {item.rating}" if item.rating else "未评分",
                                        size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                            ],
                            spacing=4, expand=True,
                        ),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Text(item.note or "", size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                divider(),
                ft.Row(
                    [
                        icon_button(ACTIONS["chevron_left"], lambda e, i=item.id: move(i, -1, page, refresh), "移到上一列"),
                        icon_button(ACTIONS["chevron_right"], lambda e, i=item.id: move(i, 1, page, refresh), "移到下一列"),
                        icon_button(ACTIONS["edit"], lambda e, i=item: open_entertainment_dialog(page, i, refresh), "编辑"),
                        icon_button(ACTIONS["delete"], lambda e, i=item.id: delete(i, page, refresh), "删除"),
                    ],
                    spacing=0,
                ),
            ],
            spacing=8,
        ),
        bgcolor=th.CARD,
        border=ft.Border.all(1, th.DIVIDER),
        border_radius=10,
        padding=10,
        on_click=lambda e, i=item: open_entertainment_dialog(page, i, refresh),
    )


def _query() -> list[tuple[Entertainment, str]]:
    """列表数据 + 解析好的封面地址（后台线程：网络封面在这里下载缓存）。"""
    with session_scope() as session:
        items = list(
            session.scalars(select(Entertainment).where(Entertainment.is_deleted.is_(False)))
        )
    # 先并发预热网络封面，再逐条解析成本地路径。
    prefetch([item.cover for item in items])
    return [(item, resolve_cover(item.cover)) for item in items]


def build(page: ft.Page, refresh) -> ft.Control:
    # 查询放后台线程，避免条目增多后切换模块卡顿。
    return background_section(page, _query, lambda items: _render(page, refresh, items))


def _render(page: ft.Page, refresh, records) -> ft.Control:
    items = [item for item, _cover_src in records]
    covers = {item.id: cover_src for item, cover_src in records}
    kind_row = ft.Row(
        [ft.TextButton(("● " if _state["kind"] == k else "") + k,
                       on_click=lambda e, value=k: (_state.__setitem__("kind", value), refresh()))
         for k in ["全部", *ENTERTAINMENT_KINDS]],
        spacing=2,
        wrap=True,
    )

    columns = []
    for status in COLUMNS:
        filtered = [i for i in items if i.status == status and (_state["kind"] == "全部" or i.kind == _state["kind"])]
        columns.append(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(status, size=th.FONT_CARD_TITLE, color=th.TEXT,
                                        weight="bold"),
                                ft.Container(expand=True),
                                ft.Text(f"{len(filtered)} 项", size=th.FONT_CAPTION,
                                        color=th.TEXT_MUTED),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        divider(),
                        ft.Column(
                            [
                                _item_card(i, covers.get(i.id, ""), page, refresh)
                                for i in filtered
                            ],
                            spacing=10,
                        )
                        if filtered else empty("暂无条目"),
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                bgcolor=th.SUBTLE,
                border=ft.Border.all(1, th.DIVIDER),
                border_radius=12,
                padding=12,
                expand=1,
            )
        )

    return ft.Column(
        [
            page_header(
                "娱乐",
                f"三列看板 · 共 {len(items)} 条",
                actions=[
                    primary_button(
                        "新增条目",
                        lambda e: open_entertainment_dialog(page, None, refresh),
                        ACTIONS["add"],
                    )
                ],
            ),
            kind_row,
            # 三列等高：列内滚动，列本身不被内容撑高
            ft.Row(
                columns,
                spacing=14,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ],
        spacing=12,
        expand=True,
    )
