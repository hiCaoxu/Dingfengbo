"""定风波桌面端入口：固定左导航 + 右内容区。"""

from __future__ import annotations

from datetime import date

import flet as ft

from .config import load_settings
from .db.session import init_db, session_scope
from .icons import ACTIONS, NAV, ico
from .services.dates import weekday_cn
from .services.diary import ensure_diary
from .services.sync import mark_pending_write, sync_client
from .theme import BG, CARD, DIVIDER, NAV_WIDTH, PRIMARY, PRIMARY_LIGHT, TEXT, TEXT_MUTED
from .ui import diary as diary_ui
from .ui import entertainment, finance_ui, health_ui, home, notes, todo
from .ui.forms import open_settings_dialog
from .ui.scheduler import bind as bind_sync
from .ui.widgets import icon_button, primary_button

MODULES = [
    ("home", "首页"),
    ("todo", "待办"),
    ("diary", "日记"),
    ("notes", "笔记"),
    ("health", "健康"),
    ("fun", "娱乐"),
    ("finance", "记账"),
]


def _greeting() -> str:
    hour = date.today().timetuple().tm_hour
    from datetime import datetime
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


def main(page: ft.Page) -> None:
    init_db()
    with session_scope() as session:
        ensure_diary(session, date.today())
        mark_pending_write()

    page.title = "定风波 · 个人工作台"
    page.bgcolor = BG
    page.padding = 0
    page.spacing = 0
    page.window.width = 1280
    page.window.height = 880
    page.window.min_width = 900
    page.window.min_height = 600
    page.window.resizable = True
    page.window.bgcolor = BG
    page.theme = ft.Theme(
        color_scheme_seed="#4A7C59",
        font_family="Microsoft YaHei",
        visual_density=ft.VisualDensity.COMPACT,
    )

    state = {"current": "home", "preview_note": None}
    sync_text = ft.Text(sync_client.status, size=11, color=TEXT_MUTED)
    body = ft.Container(expand=True, padding=ft.Padding.all(16))
    date_text = ft.Text(
        f"{date.today().year}年{date.today().month}月{date.today().day}日 {weekday_cn(date.today())}",
        size=12, color=TEXT_MUTED,
    )
    header = ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [ft.Text(f"{_greeting()}，欢迎回来", size=18, color=TEXT, weight="bold"), date_text],
                    spacing=4,
                ),
                ft.Container(expand=True),
                ft.Row(
                    [
                        ft.Container(width=7, height=7, bgcolor=PRIMARY, border_radius=4),
                        sync_text,
                        ft.TextButton("立即同步", on_click=lambda e: _manual_sync()),
                        icon_button(ACTIONS["settings"], lambda e: open_settings_dialog(page, refresh, refresh_status), "设置"),
                    ],
                    spacing=4,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=BG,
        padding=ft.Padding.symmetric(vertical=16, horizontal=24),
    )

    nav_column = ft.Column(spacing=2, expand=True)

    def refresh_status() -> None:
        sync_text.value = sync_client.status
        sync_text.color = TEXT_MUTED if "已同步" in sync_client.status else "#C97B63"

    def _manual_sync():
        sync_client.sync_once()
        refresh_status()
        page.update()

    def navigate(key: str) -> None:
        state["current"] = key
        _render_nav()
        refresh()

    def _render_nav() -> None:
        controls = [
            ft.Container(
                content=ft.Row(
                    [ft.Icon(ico("WINDSOCK_OUTLINED"), size=22, color=PRIMARY),
                     ft.Text("定风波", size=16, color=TEXT, weight="bold")],
                    spacing=10,
                ),
                padding=ft.Padding.symmetric(vertical=22, horizontal=18),
            )
        ]
        for key, label in MODULES:
            active = state["current"] == key or (state["current"] == "note_preview" and key == "notes")
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ico(NAV[key]), size=20, color=PRIMARY if active else TEXT_MUTED),
                            ft.Text(label, size=13, color=PRIMARY if active else TEXT_MUTED,
                                    weight="bold" if active else "normal"),
                        ],
                        spacing=12,
                    ),
                    bgcolor=PRIMARY_LIGHT if active else None,
                    border_radius=8,
                    padding=ft.Padding.symmetric(vertical=10, horizontal=14),
                    margin=ft.Margin.symmetric(horizontal=10),
                    on_click=lambda e, k=key: navigate(k),
                )
            )
        controls.append(ft.Container(expand=True))
        controls.append(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Row([ft.Container(width=7, height=7, bgcolor=PRIMARY, border_radius=4),
                                ft.Text("本地优先 · 离线可用", size=10, color=TEXT_MUTED)], spacing=6),
                        ft.Text(f"数据目录 D:\\Data\\Dingfengbo", size=9, color=TEXT_MUTED),
                    ],
                    spacing=4,
                ),
                border=ft.Border.only(top=ft.BorderSide(1, DIVIDER)),
                padding=ft.Padding.all(16),
            )
        )
        nav_column.controls = controls

    def open_diary_day(day: date) -> None:
        diary_ui.set_selected(day)
        navigate("diary")

    def open_note_preview(note) -> None:
        state["preview_note"] = note
        navigate("note_preview")

    def refresh() -> None:
        current = state["current"]
        if current == "home":
            body.content = home.build(page, refresh, open_diary_day, open_note_preview)
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
        page.update()

    _render_nav()
    page.add(
        ft.Row(
            [
                ft.Container(content=nav_column, width=NAV_WIDTH, bgcolor=CARD,
                             border=ft.Border.only(right=ft.BorderSide(1, DIVIDER))),
                ft.Column([header, body], expand=True),
            ],
            expand=True,
            spacing=0,
        )
    )
    refresh()
    bind_sync(page, refresh_status, refresh)


def run() -> None:
    if hasattr(ft, "run"):
        ft.run(main)
    else:
        ft.app(target=main)


if __name__ == "__main__":
    run()
