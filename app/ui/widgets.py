"""通用 UI 组件：卡片、按钮、空状态、弹窗。"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from ..icons import ico
from .. import theme as th

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
    title_size: int = th.FONT_CARD_TITLE,
) -> ft.Container:
    header: list[ft.Control] = []
    if title:
        header.append(
            ft.Row(
                [
                    ft.Text(title, size=title_size, color=th.TEXT, weight="bold"),
                    trailing or ft.Container(),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
    body = header + ([content] if content is not None else [])
    # 固定高度的卡片：内容用 expand 撑满剩余空间，超出部分在卡片内部滚动，
    # 而不是把卡片顶出一截、和同行的卡片底边不齐或叠到下方卡片上。
    if height is not None and isinstance(content, ft.Column):
        if content.expand is None:
            content.expand = True
        if content.scroll is None:
            content.scroll = ft.ScrollMode.AUTO
    fills = bool(expand) or height is not None
    # expand 必须一路传给内层 Column：否则卡片高度撑满了，内部带 expand 的
    # 列表/滚动区拿不到高度，Flet 会渲染成一块空白。
    return ft.Container(
        content=ft.Column(body, spacing=12, tight=not fills, expand=True if fills else None),
        bgcolor=th.CARD,
        border=th.card_border(),
        border_radius=th.CARD_RADIUS,
        padding=padding or th.CARD_PADDING,
        expand=expand,
        on_click=on_click,
        height=height,
        width=width,
        alignment=alignment,
    )


def equal_height_row(
    controls: list[ft.Control],
    spacing: int = 14,
    height: int | float | None = None,
    wrap: bool = False,
    expand: int | None = None,
) -> ft.Row:
    """同行卡片：统一垂直拉伸 + 可选统一高度，避免一张高一张矮、底边不齐。

    子控件自身若已有固定高度，``height`` 会覆盖它；否则仅靠 STRETCH 让
    Row 内所有卡片被拉成同一高度（以最高者为基准）。

    ``expand`` 让整行在父 Column 里按比例瓜分剩余高度（仪表盘铺满窗口用）：
    不传则保持原来的「按内容高度排布、从顶部堆叠」行为。
    """
    if height is not None:
        for control in controls:
            if isinstance(control, ft.Container):
                control.height = height
    row = ft.Row(
        controls,
        spacing=spacing,
        wrap=wrap,
        run_spacing=spacing,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        expand=expand,
    )
    if height is not None:
        row.height = height
    return row


def page_header(
    title: str,
    subtitle: str | None = None,
    actions: list[ft.Control] | None = None,
) -> ft.Control:
    """统一页面标题区：左标题（+说明），右动作区。"""
    left: list[ft.Control] = [
        ft.Text(title, size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold")
    ]
    if subtitle:
        left.append(
            ft.Text(
                subtitle,
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    return ft.Row(
        [
            ft.Column(left, spacing=4, tight=True, expand=True),
            ft.Row(actions or [], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=12,
    )


def toolbar_row(controls: list[ft.Control], spacing: int = 8) -> ft.Row:
    """统一筛选/工具条：可换行，避免 1120 宽下按钮被裁切。"""
    return ft.Row(
        controls,
        spacing=spacing,
        wrap=True,
        run_spacing=spacing,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def metric_card(
    label: str,
    value: str,
    hint: str | None = None,
    value_color: str | None = None,
    height: int | float | None = th.CARD_HEIGHT_SM,
    expand: int | None = None,
    on_click=None,
) -> ft.Container:
    """统一指标卡：标题/数值/说明三段，行内等高靠 equal_height_row 保证。"""
    body: list[ft.Control] = [
        ft.Text(
            label,
            size=th.FONT_CAPTION,
            color=th.TEXT_MUTED,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        ft.Text(
            value,
            size=th.FONT_METRIC,
            color=value_color or th.TEXT,
            weight="bold",
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    ]
    if hint:
        body.append(
            ft.Text(
                hint,
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    return ft.Container(
        content=ft.Column(body, spacing=4, tight=True, alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=th.CARD,
        border=th.card_border(),
        border_radius=th.CARD_RADIUS,
        padding=ft.Padding.symmetric(vertical=12, horizontal=14),
        height=height,
        expand=expand,
        on_click=on_click,
        alignment=ft.Alignment.CENTER_LEFT,
    )


def status_badge(text: str, tone: str = "muted") -> ft.Container:
    """统一状态标签；颜色区分语义，字号不低于 FONT_MIN。"""
    palette = {
        "primary": (th.PRIMARY_LIGHT, th.PRIMARY),
        "accent": (th.ACCENT_LIGHT, th.ACCENT),
        "danger": (th.ACCENT_LIGHT, th.DANGER),
        "muted": (th.SUBTLE, th.TEXT_MUTED),
    }
    bg, fg = palette.get(tone, palette["muted"])
    return ft.Container(
        content=ft.Text(text, size=th.FONT_CAPTION, color=fg, max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS),
        bgcolor=bg,
        border_radius=10,
        padding=ft.Padding.symmetric(vertical=2, horizontal=8),
    )


def list_row(
    title: str,
    subtitle: str | None = None,
    leading: ft.Control | None = None,
    trailing: ft.Control | None = None,
    on_click=None,
    padding: ft.Padding | None = None,
) -> ft.Container:
    """统一列表项：左侧可选图标、中间标题+副标题、右侧操作。"""
    middle: list[ft.Control] = [
        ft.Text(
            title,
            size=th.FONT_BODY,
            color=th.TEXT,
            weight="bold",
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
    ]
    if subtitle:
        middle.append(
            ft.Text(
                subtitle,
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    controls: list[ft.Control] = []
    if leading is not None:
        controls.append(leading)
    controls.append(ft.Column(middle, spacing=2, tight=True, expand=True))
    if trailing is not None:
        controls.append(trailing)
    return ft.Container(
        content=ft.Row(
            controls,
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=padding or ft.Padding.symmetric(vertical=8, horizontal=10),
        border=ft.Border.only(bottom=ft.BorderSide(1, th.DIVIDER)),
        on_click=on_click,
    )


def state_container(
    kind: str = "empty",
    message: str = "",
    hint: str | None = None,
    on_retry: Callable | None = None,
    height: int | float | None = None,
) -> ft.Container:
    """统一的状态块（loading/empty/error），可与同行卡片等高。"""
    icons = {
        "loading": "HOURGLASS_EMPTY",
        "empty": "NOTE_ADD_OUTLINED",
        "error": "ERROR_OUTLINE",
    }
    colors = {
        "loading": th.TEXT_MUTED,
        "empty": th.DIVIDER,
        "error": th.ACCENT,
    }
    body: list[ft.Control] = [
        ft.Icon(ico(icons.get(kind, "NOTE_ADD_OUTLINED")), size=28,
                color=colors.get(kind, th.DIVIDER)),
        ft.Text(
            message,
            size=th.FONT_BODY,
            color=th.TEXT_MUTED,
            text_align=ft.TextAlign.CENTER,
            max_lines=3,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    ]
    if hint:
        body.append(
            ft.Text(
                hint,
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
                text_align=ft.TextAlign.CENTER,
                max_lines=3,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    if on_retry is not None:
        body.append(ft.TextButton("重试", on_click=lambda e: on_retry()))
    return ft.Container(
        content=ft.Column(
            body,
            spacing=8,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(vertical=16, horizontal=12),
        alignment=ft.Alignment.CENTER,
        height=height,
        expand=True if height is None else None,
    )


def busy_button(
    text: str,
    on_click: Callable | None = None,
    icon: str | None = None,
    busy_text: str = "处理中…",
    **kwargs,
) -> ft.Button:
    """忙碌态按钮：执行期间禁用并换成 busy_text，防止重复点击。"""
    label = ft.Text(text, size=th.FONT_BODY)
    controls: list[ft.Control] = [ft.Icon(ico(icon), size=16)] if icon else []
    controls.append(label)
    button = ft.Button(
        content=ft.Row(controls, spacing=6, tight=True),
        bgcolor=th.PRIMARY,
        color=th.ON_PRIMARY,
        elevation=0,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.Padding.symmetric(vertical=10, horizontal=14),
        ),
        **kwargs,
    )

    def handler(e) -> None:
        if on_click is None or button.disabled:
            return
        button.disabled = True
        label.value = busy_text
        try:
            button.update()
        except Exception:
            logger.debug("忙碌按钮状态更新失败", exc_info=True)
        try:
            on_click(e)
        finally:
            button.disabled = False
            label.value = text
            try:
                button.update()
            except Exception:
                # 回调里通常已关闭弹窗，控件不在树上时会抛，忽略即可。
                logger.debug("忙碌按钮复位失败", exc_info=True)

    button.on_click = handler
    return button


def primary_button(text: str, on_click: Callable, icon: str | None = None, **kwargs) -> ft.Button:
    # Flet 0.80 起 ElevatedButton 已弃用（1.0 移除），Button 参数完全兼容。
    label = ft.Text(text, size=12)
    controls: list[ft.Control] = [ft.Icon(ico(icon), size=16)] if icon else []
    controls.append(label)
    button = ft.Button(
        content=ft.Row(controls, spacing=6, tight=True),
        bgcolor=th.PRIMARY,
        color=th.ON_PRIMARY,
        elevation=0,
        on_click=on_click,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), padding=ft.Padding.symmetric(vertical=10, horizontal=14)),
        **kwargs,
    )
    # 动态改文案的入口：ft.Button 没有 text 属性，文案只来自 content 里的 Text。
    button.label_control = label
    return button


def set_button_text(button: ft.Control, text: str) -> None:
    """更新 ``primary_button`` 的文案。

    直接写 ``button.text = ...`` 只是给控件挂了个无人读取的属性，界面上永远不变；
    必须改 content 里那个 Text 的 value。
    """
    label = getattr(button, "label_control", None)
    if label is None:
        return
    label.value = text
    try:
        button.update()
    except Exception:
        logger.debug("按钮文案更新失败", exc_info=True)


def ghost_button(text: str, on_click: Callable, icon: str | None = None) -> ft.TextButton:
    controls = ([ft.Icon(ico(icon), size=16)] if icon else []) + [ft.Text(text, size=12, color=th.TEXT_MUTED)]
    return ft.TextButton(content=ft.Row(controls, spacing=5, tight=True), on_click=on_click)


def icon_button(icon: str, on_click: Callable, tooltip: str = "", compact: bool = False) -> ft.IconButton:
    """图标按钮；``compact=True`` 时把点击区压到 24px 左右，用于窄面板里的行内操作。"""
    if compact:
        return ft.IconButton(
            icon=ico(icon),
            icon_size=14,
            icon_color=th.TEXT_MUTED,
            tooltip=tooltip,
            on_click=on_click,
            padding=2,
            visual_density=ft.VisualDensity.COMPACT,
        )
    return ft.IconButton(
        icon=ico(icon), icon_size=18, icon_color=th.TEXT_MUTED, tooltip=tooltip, on_click=on_click
    )


def empty(text: str) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ico("NOTE_ADD_OUTLINED"), size=28, color=th.DIVIDER),
                ft.Text(text, size=12, color=th.TEXT_MUTED, text_align=ft.TextAlign.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        padding=ft.Padding.symmetric(vertical=24, horizontal=8),
        alignment=ft.Alignment.CENTER,
    )


def tag_chip(text: str, active: bool = False, on_click=None) -> ft.Container:
    bg = th.PRIMARY_LIGHT if active else th.CARD
    fg = th.PRIMARY if active else th.TEXT_MUTED
    return ft.Container(
        content=ft.Text(text, size=th.FONT_CAPTION, color=fg),
        bgcolor=bg,
        border=th.card_border(),
        border_radius=14,
        padding=ft.Padding.symmetric(vertical=4, horizontal=10),
        on_click=on_click,
    )


def divider() -> ft.Container:
    return ft.Container(height=1, bgcolor=th.DIVIDER)


def snack(page: ft.Page, message: str, color: str = th.TEXT) -> None:
    page.snack_bar = ft.SnackBar(ft.Text(message, color=th.ON_PRIMARY, size=12), bgcolor=color)
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


def open_dialog(page: ft.Page, dialog: ft.Control) -> None:
    """显示弹窗：优先走 Flet 0.86 的受管 dialog 栈，老版本退回 overlay。"""
    opener = getattr(page, "show_dialog", None)
    if callable(opener):
        opener(dialog)
        return
    page.overlay.append(dialog)
    dialog.open = True
    page.update()


def confirm(page: ft.Page, message: str, on_confirm: Callable) -> None:
    def close(_):
        dismiss(page, dialog)

    def accept(_):
        close(_)
        on_confirm()

    dialog = ft.AlertDialog(
        bgcolor=th.CARD,
        title=ft.Text("确认操作", size=14, color=th.TEXT),
        content=ft.Text(message, size=12, color=th.TEXT_MUTED),
        actions=[
            ft.TextButton("取消", on_click=close),
            primary_button("确定", accept),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    open_dialog(page, dialog)


def section_header(text: str, action: ft.Control | None = None) -> ft.Row:
    return ft.Row(
        [
            ft.Text(text, size=th.FONT_SECTION_TITLE, color=th.TEXT, weight="bold"),
            action or ft.Container(),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def form_field(label: str, value: str = "", multiline: bool = False, width: int | None = None) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        multiline=multiline,
        min_lines=3 if multiline else 1,
        max_lines=8 if multiline else 1,
        width=width,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        label_style=ft.TextStyle(color=th.TEXT_MUTED, size=12),
        text_style=ft.TextStyle(color=th.TEXT, size=13),
        border_radius=8,
        content_padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )


def dropdown(label: str, options: list[str], value: str | None = None, width: int | None = None) -> ft.Dropdown:
    return ft.Dropdown(
        label=label,
        value=value or (options[0] if options else None),
        options=[ft.dropdown.Option(option) for option in options],
        width=width,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        label_style=ft.TextStyle(color=th.TEXT_MUTED, size=th.FONT_CAPTION),
        text_style=ft.TextStyle(color=th.TEXT, size=th.FONT_CARD_TITLE),
        border_radius=8,
    )


def modal(
    page: ft.Page,
    title: str,
    body: ft.Control,
    actions: list[ft.Control],
    width: int = 560,
    modal: bool = False,
    on_dismiss=None,
) -> ft.AlertDialog:
    dialog = ft.AlertDialog(
        bgcolor=th.CARD,
        title=ft.Text(title, size=th.FONT_SECTION_TITLE, color=th.TEXT, weight="bold"),
        content=ft.Container(content=body, width=width),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=12),
        modal=modal,
        on_dismiss=on_dismiss,
    )
    open_dialog(page, dialog)
    return dialog


def dismiss(page: ft.Page, dialog: ft.Control) -> None:
    """关闭弹窗：只置 ``open = False`` 并 update，**不要**手动从 overlay 移除。

    Flet 0.86 的弹窗关闭由 Flutter 侧动画驱动，官方实现（``Page.pop_dialog`` /
    ``_wrap_dialog_on_dismiss``）也只在 ``on_dismiss`` 回调里把控件移出栈；
    在关闭的同一帧删除控件会丢掉 dismiss 回调，表现为「点了取消/保存但弹窗不关」。
    走 ``show_dialog`` 打开时，控件会在 dismiss 后由 Flet 自动从栈中清理。
    """
    try:
        dialog.open = False
    except Exception:
        logger.debug("设置弹窗关闭状态失败", exc_info=True)
        return
    page.update()


def close_modal(page: ft.Page, dialog: ft.AlertDialog) -> None:
    dismiss(page, dialog)
