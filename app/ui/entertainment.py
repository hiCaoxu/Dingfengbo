"""娱乐：想看 / 在看 / 看完 三列看板。"""

from __future__ import annotations

from pathlib import Path

import flet as ft
from sqlalchemy import select

from ..db.models import Entertainment
from ..db.repo import update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.sync import mark_pending_write
from ..theme import DIVIDER, PRIMARY_LIGHT, TEXT, TEXT_MUTED
from .forms import ENTERTAINMENT_KINDS, open_entertainment_dialog
from .state import state
from .widgets import card, delete_with_confirm, divider, empty, icon_button, primary_button, section_header

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
        content=ft.Icon(ico("IMAGE_OUTLINED"), size=24, color=TEXT_MUTED),
        width=64, height=84, bgcolor=PRIMARY_LIGHT, border_radius=6,
        alignment=ft.Alignment.CENTER,
    )


def _cover(item: Entertainment) -> ft.Control:
    source = (item.cover or "").strip()
    if source.startswith(("http://", "https://")):
        return ft.Image(
            src=source, width=64, height=84, fit=ft.BoxFit.COVER, border_radius=6,
            error_content=_placeholder_cover(),
        )
    if source and Path(source).exists():
        return ft.Image(src=source, width=64, height=84, fit=ft.BoxFit.COVER, border_radius=6)
    return _placeholder_cover()


def _item_card(item: Entertainment, page, refresh) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        _cover(item),
                        ft.Column(
                            [
                                ft.Text(item.title, size=12, color=TEXT, weight="bold",
                                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(item.kind, size=10, color=TEXT_MUTED),
                                ft.Text(f"评分 {item.rating}" if item.rating else "未评分",
                                        size=10, color=TEXT_MUTED),
                            ],
                            spacing=4, expand=True,
                        ),
                    ],
                    spacing=10,
                ),
                ft.Text(item.note or "", size=10, color=TEXT_MUTED, max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS),
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
        bgcolor="#FFFFFF",
        border=ft.Border.all(1, DIVIDER),
        border_radius=10,
        padding=10,
        on_click=lambda e, i=item: open_entertainment_dialog(page, i, refresh),
    )


def build(page: ft.Page, refresh) -> ft.Control:
    with session_scope() as session:
        items = list(session.scalars(select(Entertainment).where(Entertainment.is_deleted.is_(False))))

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
                        ft.Row([ft.Text(status, size=13, color=TEXT, weight="bold"),
                                ft.Container(expand=True),
                                ft.Text(f"{len(filtered)} 项", size=11, color=TEXT_MUTED)]),
                        divider(),
                        ft.Column([_item_card(i, page, refresh) for i in filtered], spacing=10)
                        if filtered else empty("暂无条目"),
                    ],
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                bgcolor="#F7F4EF",
                border=ft.Border.all(1, DIVIDER),
                border_radius=12,
                padding=12,
                expand=1,
            )
        )

    return ft.Column(
        [
            section_header("娱乐", primary_button("新增条目", lambda e: open_entertainment_dialog(page, None, refresh), ACTIONS["add"])),
            kind_row,
            ft.Row(columns, spacing=14, expand=True),
        ],
        spacing=12,
        expand=True,
    )
