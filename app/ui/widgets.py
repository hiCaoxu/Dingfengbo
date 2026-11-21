"""通用 UI 组件：卡片、按钮、空状态、弹窗。"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from ..icons import ico
from ..theme import (
    ACCENT_LIGHT,
    CARD,
    CARD_PADDING,
    CARD_RADIUS,
    DIVIDER,
    PRIMARY,
    PRIMARY_LIGHT,
    TEXT,
    TEXT_MUTED,
    card_border,
)

logger = logging.getLogger(__name__)


def card(
    content: ft.Control,
    title: str | None = None,
    trailing: ft.Control | None = None,
    expand: int | None = None,
    padding: ft.Padding | None = None,
    on_click=None,
    height: int | float | None = None,
    width: int | float | None = None,
    alignment: ft.Alignment | None = None,
) -> ft.Container:
    header: list[ft.Control] = []
    if title:
        header.append(
            ft.Row(
                [
                    ft.Text(title, size=13, color=TEXT, weight="bold"),
                    trailing or ft.Container(),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
        )
    body = header + ([content] if content is not None else [])
    return ft.Container(
        content=ft.Column(body, spacing=12, tight=not expand),
        bgcolor=CARD,
        border=card_border(),
        border_radius=CARD_RADIUS,
        padding=padding or CARD_PADDING,
        expand=expand,
        on_click=on_click,
        height=height,
        width=width,
        alignment=alignment,
    )


def primary_button(text: str, on_click: Callable, icon: str | None = None, **kwargs) -> ft.ElevatedButton:
    return ft.ElevatedButton(
        content=ft.Row(
            [ft.Icon(ico(icon), size=16), ft.Text(text, size=12)] if icon else [ft.Text(text, size=12)],
            spacing=6,
            tight=True,
        ),
        bgcolor=PRIMARY,
        color="#FFFFFF",
        elevation=0,
        on_click=on_click,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), padding=ft.Padding.symmetric(vertical=10, horizontal=14)),
        **kwargs,
    )


def ghost_button(text: str, on_click: Callable, icon: str | None = None) -> ft.TextButton:
    controls = ([ft.Icon(ico(icon), size=16)] if icon else []) + [ft.Text(text, size=12, color=TEXT_MUTED)]
    return ft.TextButton(content=ft.Row(controls, spacing=5, tight=True), on_click=on_click)


def icon_button(icon: str, on_click: Callable, tooltip: str = "") -> ft.IconButton:
    return ft.IconButton(
        icon=ico(icon), icon_size=18, icon_color=TEXT_MUTED, tooltip=tooltip, on_click=on_click
    )


def empty(text: str) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ico("NOTE_ADD_OUTLINED"), size=28, color=DIVIDER),
                ft.Text(text, size=12, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        padding=ft.Padding.symmetric(vertical=24, horizontal=8),
        alignment=ft.Alignment.CENTER,
    )


def tag_chip(text: str, active: bool = False, on_click=None) -> ft.Container:
    bg = PRIMARY_LIGHT if active else CARD
    fg = PRIMARY if active else TEXT_MUTED
    return ft.Container(
        content=ft.Text(text, size=11, color=fg),
        bgcolor=bg,
        border=card_border(),
        border_radius=14,
        padding=ft.Padding.symmetric(vertical=4, horizontal=10),
        on_click=on_click,
    )


def divider() -> ft.Container:
    return ft.Container(height=1, bgcolor=DIVIDER)


def snack(page: ft.Page, message: str, color: str = TEXT) -> None:
    page.snack_bar = ft.SnackBar(ft.Text(message, color="#FFFFFF", size=12), bgcolor=color)
    page.snack_bar.open = True
    page.update()


def delete_with_confirm(
    page: ft.Page,
    model,
    record_id: str,
    refresh,
    message: str = "确定删除该记录？",
) -> None:
    """统一的软删除确认流程，供各模块复用，避免删除逻辑逐处复制。"""
    from ..db.repo import soft_delete
    from ..db.session import session_scope
    from ..services.sync import mark_pending_write

    def action():
        with session_scope() as session:
            obj = session.get(model, record_id)
            if obj:
                soft_delete(session, obj)
        mark_pending_write()
        refresh()

    confirm(page, message, action)


def confirm(page: ft.Page, message: str, on_confirm: Callable) -> None:
    def close(_):
        dismiss(page, dialog)

    def accept(_):
        close(_)
        on_confirm()

    dialog = ft.AlertDialog(
        title=ft.Text("确认操作", size=14, color=TEXT),
        content=ft.Text(message, size=12, color=TEXT_MUTED),
        actions=[
            ft.TextButton("取消", on_click=close),
            primary_button("确定", accept),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(dialog)
    dialog.open = True
    page.update()


def section_header(text: str, action: ft.Control | None = None) -> ft.Row:
    return ft.Row(
        [ft.Text(text, size=13, color=TEXT, weight="bold"), action or ft.Container()],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )


def form_field(label: str, value: str = "", multiline: bool = False, width: int | None = None) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        multiline=multiline,
        min_lines=3 if multiline else 1,
        max_lines=8 if multiline else 1,
        width=width,
        border_color=DIVIDER,
        focused_border_color=PRIMARY,
        color=TEXT,
        label_style=ft.TextStyle(color=TEXT_MUTED, size=12),
        text_style=ft.TextStyle(color=TEXT, size=13),
        border_radius=8,
        content_padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )


def dropdown(label: str, options: list[str], value: str | None = None, width: int | None = None) -> ft.Dropdown:
    return ft.Dropdown(
        label=label,
        value=value or (options[0] if options else None),
        options=[ft.dropdown.Option(option) for option in options],
        width=width,
        border_color=DIVIDER,
        focused_border_color=PRIMARY,
        color=TEXT,
        label_style=ft.TextStyle(color=TEXT_MUTED, size=12),
        border_radius=8,
    )


def modal(page: ft.Page, title: str, body: ft.Control, actions: list[ft.Control], width: int = 560) -> ft.AlertDialog:
    dialog = ft.AlertDialog(
        title=ft.Text(title, size=14, color=TEXT, weight="bold"),
        content=ft.Container(content=body, width=width),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=12),
    )
    page.overlay.append(dialog)
    dialog.open = True
    page.update()
    return dialog


def dismiss(page: ft.Page, dialog: ft.Control) -> None:
    """关闭弹窗并把它移出 ``page.overlay``。

    只置 ``open = False`` 会把隐藏的 AlertDialog 永久留在 overlay 里，
    每次开窗都新增一个，长期使用后控件树与增量消息持续膨胀。
    """
    try:
        dialog.open = False
    except Exception:
        logger.debug("设置弹窗关闭状态失败", exc_info=True)
    try:
        # Flet 控件按值比较（`==` 相等），必须按身份移除，否则可能误删参数相同的另一个弹窗。
        for index, control in enumerate(page.overlay):
            if control is dialog:
                del page.overlay[index]
                break
    except Exception:
        logger.debug("弹窗移出 overlay 失败", exc_info=True)
    page.update()


def close_modal(page: ft.Page, dialog: ft.AlertDialog) -> None:
    dismiss(page, dialog)
