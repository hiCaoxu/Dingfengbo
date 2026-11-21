"""定风波桌面端入口：固定左导航 + 右内容区。"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime
from pathlib import Path

import flet as ft

from . import theme as th
from .config import DATA_DIR, LOGS_DIR, load_settings, save_settings
from .db.backup import maybe_daily_backup
from .db.session import init_db, session_scope
from .icons import ACTIONS, NAV, ico
from .logging_setup import setup_logging
from .services import budget as budget_service
from .services import system_integration
from .services.dates import weekday_cn
from .services.diary import ensure_diary
from .services.holidays import ensure_sample_file
from .services.sync import mark_pending_write, queue_counts, sync_client
from .ui import ai_assistant, diary as diary_ui
from .ui import entertainment, finance_ui, health_ui, home, notes
from .ui import help as help_ui
from .ui import reports as reports_ui
from .ui import timeline as timeline_ui
from .ui import todo
from .ui.command_palette import open_command_palette
from .ui.forms import open_settings_dialog
from .ui.scheduler import bind as bind_sync
from .ui.sync_detail import open_sync_detail
from .ui import theme_switch
from .ui.widgets import icon_button

logger = logging.getLogger(__name__)

# assets 用绝对路径：打包后工作目录可能不是项目根
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

# 全部可用模块；实际顺序与显隐由设置里的 module_order / hidden_modules 决定。
MODULES = [
    ("home", "首页"),
    ("todo", "任务"),
    ("diary", "日记"),
    ("notes", "笔记"),
    ("health", "健康"),
    ("fun", "娱乐"),
    ("finance", "记账"),
    ("reports", "回顾"),
    ("timeline", "时间轴"),
    ("ai", "AI 助手"),
]

MODULE_LABELS = dict(MODULES)


def _module_list() -> list[tuple[str, str]]:
    settings = load_settings()
    hidden = {str(key) for key in (settings.get("hidden_modules") or [])}
    order = [str(key) for key in (settings.get("module_order") or [])]
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in order:
        if key in MODULE_LABELS and key not in hidden and key not in seen:
            ordered.append((key, MODULE_LABELS[key]))
            seen.add(key)
    # 新版本新增的模块不在旧设置里：自动追加到末尾，避免升级后入口消失。
    for key, label in MODULES:
        if key not in seen and key not in hidden:
            ordered.append((key, label))
    # 全部被隐藏时只保留首页，避免导航区变空白后无法回到其他模块。
    return ordered or [("home", "首页")]


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 6:
        return "夜深了"
    if hour < 11:
        return "早上好"
    if hour < 14:
        return "中午好"
    if hour < 18:
        return "下午好"
    return "晚上好"


def _generate_recurring() -> None:
    """启动时补齐到期的周期记账（失败只记日志，不影响启动）。"""
    try:
        created = budget_service.generate_due()
        if created:
            logger.info("已自动生成 %d 条周期交易", created)
    except Exception:
        logger.exception("生成周期交易失败")


def main(page: ft.Page) -> None:
    setup_logging(LOGS_DIR)
    init_db()
    with session_scope() as session:
        ensure_diary(session, date.today())
        mark_pending_write()
    # 首次启动生成节假日补充文件模板（内置 2026-2027 数据已生效，这里只放说明）
    ensure_sample_file()
    # 每日自动备份放后台线程：SQLite 在线快照不影响启动速度，失败只记日志。
    threading.Thread(target=maybe_daily_backup, daemon=True).start()
    # 固定支出/订阅：到期自动生成交易（幂等，重复启动不会重复记账）。
    threading.Thread(target=_generate_recurring, daemon=True).start()

    settings = load_settings()
    th.set_mode(settings.get("theme_mode", "light"))

    page.title = "定风波 · 个人工作台"
    page.bgcolor = th.BG
    page.padding = 0
    page.spacing = 0
    page.window.width = 1280
    page.window.height = 880
    # 任务页是三栏（月历/象限 + 列表 + 习惯），低于此宽度会把中间列表挤没
    page.window.min_width = 1120
    page.window.min_height = 600
    page.window.resizable = True
    page.window.maximizable = True
    page.window.minimizable = True
    page.window.bgcolor = th.BG
    # 隐藏系统标题栏，改用自定义标题栏（可拖动 + 最小化/最大化/关闭按钮）
    page.window.title_bar_hidden = True
    # 中文霞鹜文楷 + 外文 Consolas（见 theme.apply_fonts）
    th.apply_fonts(page)
    page.theme = th.build_theme()

    state = {"current": "home", "preview_note": None, "palette": False}
    # 顶栏状态文字限宽 + 省略号：内容再长也不会把右侧图标按钮挤出屏幕。
    sync_text = ft.Text(
        sync_client.status, size=th.FONT_CAPTION, color=th.TEXT_MUTED,
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, width=176,
    )
    queue_text = ft.Text(
        "", size=th.FONT_CAPTION, color=th.TEXT_MUTED,
        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, width=110,
    )
    # 有失败记录时整块可点击，直接打开同步明细看重试/忽略。
    queue_box = ft.Container(
        content=queue_text,
        padding=ft.Padding.symmetric(horizontal=4, vertical=2),
        border_radius=6,
        visible=False,
    )
    body = ft.Container(expand=True, padding=ft.Padding.all(16))
    date_text = ft.Text(
        f"{date.today().year}年{date.today().month}月{date.today().day}日 {weekday_cn(date.today())}",
        size=th.FONT_CAPTION, color=th.TEXT_MUTED,
    )
    greeting_text = ft.Text(f"{_greeting()}，欢迎回来", size=th.FONT_PAGE_TITLE,
                            color=th.TEXT, weight="bold")
    slogan_text = ft.Text("刻意练习  要事第一  持续精进", size=th.FONT_SECTION_TITLE,
                          color=th.PRIMARY, weight="bold")
    dot = ft.Container(width=7, height=7, bgcolor=th.PRIMARY, border_radius=4)

    def toggle_maximize(_=None) -> None:
        """最大化/还原：标题栏按钮和顶栏双击共用。"""
        page.window.maximized = not bool(page.window.maximized)
        page.update()

    def window_buttons() -> ft.Row:
        def minimize(_):
            page.window.minimized = True
            page.update()

        def close(_):
            request_close()

        return ft.Row(
            [
                ft.IconButton(icon=ico("MINIMIZE"), icon_size=16, icon_color=th.TEXT_MUTED,
                              tooltip="最小化", on_click=minimize),
                ft.IconButton(icon=ico("CROP_SQUARE"), icon_size=15, icon_color=th.TEXT_MUTED,
                              tooltip="最大化/还原", on_click=toggle_maximize),
                ft.IconButton(icon=ico("CLOSE"), icon_size=16, icon_color=th.TEXT_MUTED,
                              tooltip="关闭", on_click=close),
            ],
            spacing=0,
        )

    def toggle_theme(_=None) -> None:
        apply_theme("light" if th.is_dark() else "dark")

    header_row = ft.Row(spacing=4)

    def build_header_row() -> None:
        header_row.controls = [
            dot,
            sync_text,
            queue_box,
            ft.TextButton("立即同步", on_click=lambda e: _manual_sync()),
            icon_button(ACTIONS["search"], lambda e: open_search(), "搜索与命令 (Ctrl+K)"),
            icon_button(
                "DARK_MODE_OUTLINED" if not th.is_dark() else "LIGHT_MODE_OUTLINED",
                toggle_theme,
                "切换暗色/亮色主题 (Ctrl+T)",
            ),
            icon_button("HELP_OUTLINE", lambda e: navigate("help"), "帮助与上手 (F1)"),
            icon_button(ACTIONS["settings"],
                        lambda e: open_settings_dialog(page, refresh, refresh_status), "设置"),
        ]

    # 座右铭单独放在左右两块之间，居中显示；窄窗口下自动隐藏，优先保证按钮完整。
    slogan_box = ft.Container(
        content=slogan_text,
        expand=True,
        alignment=ft.Alignment.CENTER,
    )
    header_box = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        greeting_text,
                        date_text,
                    ],
                    spacing=4,
                ),
                slogan_box,
                header_row,
                ft.Container(width=1, height=20, bgcolor=th.DIVIDER,
                             margin=ft.Margin.symmetric(horizontal=6)),
                window_buttons(),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=th.BG,
        padding=ft.Padding.symmetric(vertical=12, horizontal=24),
    )
    build_header_row()

    def fit_header(width: float | None = None) -> None:
        """1120 宽下顶栏放不下座右铭：按窗口宽度收起，避免按钮被裁切。"""
        current = width or page.width or page.window.width or 1280
        slogan_box.visible = current >= 1240
        try:
            page.update()
        except Exception:
            logger.debug("顶栏自适应刷新失败", exc_info=True)

    # 双击顶栏空白处 = 最大化/还原；拖动交给 start_dragging。
    # 不用 WindowDragArea：它的 on_double_tap 在 Windows 上收不到事件（原生拖动区把手势吃掉了）。
    header = ft.GestureDetector(
        content=header_box,
        on_double_tap=lambda e: toggle_maximize(),
        on_pan_start=lambda e: page.run_task(page.window.start_dragging),
    )

    # 导航在 600 高度下仍要能看全 10 个模块：主体区可滚动，底部信息固定。
    nav_column = ft.Column(spacing=2, expand=True, scroll=ft.ScrollMode.AUTO)
    nav_footer = ft.Container()
    nav_box = ft.Container(
        content=ft.Column([nav_column, nav_footer], spacing=0, expand=True),
        width=th.NAV_WIDTH, bgcolor=th.CARD,
        border=ft.Border.only(right=ft.BorderSide(1, th.DIVIDER)),
    )

    def apply_theme(mode: str) -> None:
        th.set_mode(mode)
        save_settings({"theme_mode": th.current_mode()})
        page.bgcolor = th.BG
        page.window.bgcolor = th.BG
        page.theme = th.build_theme()
        header_box.bgcolor = th.BG
        greeting_text.color = th.TEXT
        slogan_text.color = th.PRIMARY
        date_text.color = th.TEXT_MUTED
        dot.bgcolor = th.PRIMARY
        nav_box.bgcolor = th.CARD
        nav_box.border = ft.Border.only(right=ft.BorderSide(1, th.DIVIDER))
        build_header_row()
        _render_nav()
        refresh()

    def refresh_status() -> None:
        sync_text.value = sync_client.status
        sync_text.color = th.TEXT_MUTED if "已同步" in sync_client.status else th.ACCENT
        # 限宽后可能被省略，完整状态放进 tooltip
        sync_text.tooltip = sync_client.status
        # 队列可见性：单条聚合查询，告诉用户「还有多少没传上去 / 有多少被拒」。
        pending, failed = queue_counts()
        parts = []
        if pending:
            parts.append(f"待同步 {pending}")
        if failed:
            parts.append(f"失败 {failed}")
        queue_text.value = " · ".join(parts)
        queue_text.color = th.ACCENT if failed else th.TEXT_MUTED
        queue_box.visible = bool(parts)
        queue_box.on_click = (
            (lambda e: open_sync_detail(page, refresh_status)) if parts else None
        )
        queue_box.tooltip = "点击查看同步明细（失败原因 / 重试 / 忽略）" if failed else "点击查看同步明细"

    syncing = {"value": False}

    def _manual_sync():
        # 手动同步放到后台线程：sync_once 含最长 10s 的 HTTP 超时，同步执行会冻结界面。
        if syncing["value"]:
            return
        syncing["value"] = True
        sync_text.value = "正在同步…"
        sync_text.color = th.TEXT_MUTED
        page.update()

        def worker() -> None:
            try:
                sync_client.sync_once()
            except Exception:
                logger.exception("手动同步异常")

            async def apply() -> None:
                syncing["value"] = False
                refresh_status()
                if sync_client.status.startswith("已同步"):
                    refresh(force=False)  # 拉取到新数据后刷新当前模块（笔记编辑页除外）
                else:
                    page.update()

            try:
                page.run_task(apply)
            except Exception:
                syncing["value"] = False
                logger.exception("手动同步结果回 UI 线程失败")

        page.run_thread(worker)

    def navigate(key: str) -> None:
        state["current"] = key
        _render_nav()
        refresh()

    def _render_nav() -> None:
        controls = [
            ft.Container(
                content=ft.Row(
                    [ft.Icon(ico("WINDSOCK_OUTLINED"), size=22, color=th.PRIMARY),
                     ft.Text("定风波", size=th.FONT_SECTION_TITLE, color=th.TEXT, weight="bold")],
                    spacing=10,
                ),
                padding=ft.Padding.symmetric(vertical=20, horizontal=18),
            )
        ]
        for key, label in _module_list():
            active = state["current"] == key or (state["current"] == "note_preview" and key == "notes")
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ico(NAV.get(key, "CIRCLE_OUTLINED")), size=20,
                                    color=th.PRIMARY if active else th.TEXT_MUTED),
                            ft.Text(label, size=th.FONT_NAV,
                                    color=th.PRIMARY if active else th.TEXT_MUTED,
                                    weight="bold" if active else "normal",
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True),
                        ],
                        spacing=12,
                    ),
                    bgcolor=th.PRIMARY_LIGHT if active else None,
                    border_radius=8,
                    padding=ft.Padding.symmetric(vertical=9, horizontal=14),
                    margin=ft.Margin.symmetric(horizontal=10),
                    on_click=lambda e, k=key: navigate(k),
                )
            )
        # 帮助不是业务模块：不进 MODULES，也不参与 Ctrl+数字与设置里的排序
        help_active = state["current"] == "help"
        controls.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ico("HELP_OUTLINE"), size=20,
                                color=th.PRIMARY if help_active else th.TEXT_MUTED),
                        ft.Text("帮助", size=th.FONT_NAV,
                                color=th.PRIMARY if help_active else th.TEXT_MUTED,
                                weight="bold" if help_active else "normal",
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                expand=True),
                    ],
                    spacing=12,
                ),
                bgcolor=th.PRIMARY_LIGHT if help_active else None,
                border_radius=8,
                padding=ft.Padding.symmetric(vertical=9, horizontal=14),
                margin=ft.Margin.symmetric(horizontal=10),
                on_click=lambda e: navigate("help"),
            )
        )
        nav_column.controls = controls
        # 辅助信息固定在导航底部：字号仍为 12，只靠颜色弱化。
        nav_footer.content = ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(width=7, height=7, bgcolor=th.PRIMARY, border_radius=4),
                        ft.Text("本地优先 · 离线可用", size=th.FONT_CAPTION,
                                color=th.TEXT_MUTED, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                    ],
                    spacing=6,
                ),
                ft.Text(
                    f"数据目录 {DATA_DIR}", size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=str(DATA_DIR),
                ),
                ft.Text("Ctrl+K 搜索", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
            ],
            spacing=4,
            tight=True,
        )
        nav_footer.border = ft.Border.only(top=ft.BorderSide(1, th.DIVIDER))
        nav_footer.padding = ft.Padding.all(14)

    def open_diary_day(day: date) -> None:
        diary_ui.set_selected(day)
        navigate("diary")

    def open_note_preview(note) -> None:
        state["preview_note"] = note
        navigate("note_preview")

    def open_search() -> None:
        if state["palette"]:
            return
        state["palette"] = True

        def closed(_=None) -> None:
            state["palette"] = False

        dialog = open_command_palette(
            page,
            navigate,
            refresh,
            on_open_note=open_note_preview,
            on_open_diary=open_diary_day,
            on_sync=_manual_sync,
        )
        dialog.on_dismiss = closed

    def refresh(force: bool = True) -> None:
        current = state["current"]
        if not force and current == "note_preview":
            # 后台同步完成不做整页重建：编辑页被重建会用控件初值覆盖正在输入的内容。
            page.update()
            return
        if current == "home":
            body.content = home.build(page, refresh, open_diary_day, open_note_preview, navigate)
        elif current == "todo":
            body.content = todo.build(page, refresh)
        elif current == "diary":
            body.content = diary_ui.build(page, refresh)
        elif current == "notes":
            body.content = notes.build(page, refresh, open_note_preview)
        elif current == "note_preview":
            note = state["preview_note"]
            if note is None:
                body.content = notes.build(page, refresh, open_note_preview)
            else:
                body.content = notes.build_preview(page, note, lambda e: navigate("notes"))
        elif current == "health":
            body.content = health_ui.build(page, refresh)
        elif current == "fun":
            body.content = entertainment.build(page, refresh)
        elif current == "finance":
            body.content = finance_ui.build(page, refresh)
        elif current == "reports":
            body.content = reports_ui.build(page, refresh)
        elif current == "timeline":
            body.content = timeline_ui.build(
                page, refresh, navigate, open_note_preview, open_diary_day
            )
        elif current == "ai":
            body.content = ai_assistant.build(page, refresh)
        elif current == "help":
            body.content = help_ui.build(page, navigate)
        page.update()

    def on_keyboard(e: ft.KeyboardEvent) -> None:
        key = (e.key or "").lower()
        if e.ctrl and key == "k":
            open_search()
            return
        if e.ctrl and key == "t":
            toggle_theme()
            return
        if key == "f1":
            navigate("help")
            return
        if e.ctrl and len(key) == 1 and key in "0123456789":
            modules = [name for name, _label in _module_list()]
            # Ctrl+1..9 对应前九个模块，Ctrl+0 对应第十个
            index = 9 if key == "0" else int(key) - 1
            if 0 <= index < len(modules):
                navigate(modules[index])
            return

    def request_close(_=None) -> None:
        """关闭窗口：开了「关闭到托盘」就先隐藏；没有托盘依赖时最小化到任务栏。"""
        if not load_settings().get("close_to_tray"):
            page.run_task(page.window.close)
            return
        if system_integration.tray_available():
            try:
                system_integration.start_tray(page, on_quit=lambda: page.window.close())
                if system_integration.tray_running():
                    page.window.skip_task_bar = True
                    page.window.visible = False
                    page.update()
                    return
            except Exception:
                logger.exception("收入托盘失败")
        # 没有 pystray：降级为最小化，避免用户以为「关到托盘」却直接退出。
        try:
            page.window.minimized = True
            page.update()
        except Exception:
            logger.debug("最小化窗口失败", exc_info=True)

    theme_switch.bind(apply_theme)
    _render_nav()
    page.add(ft.Row([nav_box, ft.Column([header, body], expand=True)], expand=True, spacing=0))
    fit_header()
    page.on_resize = lambda e: fit_header(e.width)
    refresh()
    bind_sync(page, refresh_status, lambda: refresh(force=False))
    page.on_keyboard_event = on_keyboard

    async def _bind_loop() -> None:
        try:
            system_integration.bind_loop(asyncio.get_running_loop())
            if load_settings().get("close_to_tray") and system_integration.tray_available():
                system_integration.start_tray(page, on_quit=lambda: page.window.close())
        except Exception:
            logger.debug("托盘/事件循环初始化失败", exc_info=True)

    try:
        page.run_task(_bind_loop)
    except Exception:
        logger.debug("绑定事件循环失败", exc_info=True)


def run() -> None:
    # assets 里放霞鹜文楷字体，必须用绝对路径注册，否则换工作目录就找不到
    if hasattr(ft, "run"):
        ft.run(main, assets_dir=str(ASSETS_DIR))
    else:
        ft.app(target=main, assets_dir=str(ASSETS_DIR))


if __name__ == "__main__":
    run()
