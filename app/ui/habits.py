"""习惯模块：每日打卡、连续天数、周目标与打卡日历。"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import flet as ft

from .. import theme as th
from ..icons import ACTIONS, ico
from ..services.habits import (
    calendar,
    check_in,
    delete_habit,
    overview,
    save_habit,
)
from .async_utils import background_section
from .widgets import (
    card,
    confirm,
    divider,
    empty,
    form_field,
    icon_button,
    modal,
    primary_button,
    snack,
)

logger = logging.getLogger(__name__)

WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"]


def open_habit_dialog(page: ft.Page, habit=None, on_done=None) -> ft.AlertDialog:
    name = form_field("习惯名称 *", habit.name if habit else "")
    target = form_field("每周目标次数", str(habit.target_per_week) if habit else "7", width=160)
    minutes = form_field("单次目标分钟（可选）", str(habit.target_minutes) if habit else "0",
                         width=200)
    note = form_field("备注", habit.note if habit else "", multiline=True)

    def save(_) -> None:
        title = (name.value or "").strip()
        if not title:
            snack(page, "请填写习惯名称", th.ACCENT)
            return
        try:
            weekly = max(1, min(7 * 4, int(float(target.value or 7))))
        except ValueError:
            weekly = 7
        try:
            mins = max(0, int(float(minutes.value or 0)))
        except ValueError:
            mins = 0
        try:
            save_habit(
                habit.id if habit else None,
                name=title,
                target_per_week=weekly,
                target_minutes=mins,
                note=note.value or "",
            )
        except Exception:
            logger.exception("保存习惯失败")
            snack(page, "保存失败，详见日志", th.ACCENT)
            return
        from .widgets import close_modal

        close_modal(page, dialog)
        if on_done is not None:
            on_done()

    dialog = modal(
        page,
        "编辑习惯" if habit else "新增习惯",
        ft.Column(
            [
                name,
                ft.Row([target, minutes], spacing=12),
                note,
                ft.Text(
                    "连续天数按自然日计算：今天没打卡时，连续记录保留到昨天为止。",
                    size=10,
                    color=th.TEXT_MUTED,
                ),
            ],
            spacing=10,
            tight=True,
        ),
        [ft.TextButton("取消", on_click=lambda e: _cancel()), primary_button("保存", save)],
        width=520,
    )

    def _cancel() -> None:
        from .widgets import close_modal

        close_modal(page, dialog)

    return dialog


def open_calendar_dialog(page: ft.Page, item: dict) -> ft.AlertDialog:
    """单习惯的 12 周打卡日历。"""
    columns = calendar(item["id"], weeks=12)
    today = date.today()
    grid_rows: list[ft.Control] = []
    for weekday in range(7):
        cells: list[ft.Control] = []
        for column in columns:
            day = column[weekday]
            done = day is not None and day in item["days"]
            future = day is not None and day > today
            cells.append(
                ft.Container(
                    width=16,
                    height=16,
                    bgcolor=th.PRIMARY if done else (th.BG if future else th.PRIMARY_LIGHT),
                    border_radius=4,
                    tooltip=day.isoformat() if day else "",
                )
            )
        grid_rows.append(
            ft.Row(
                [ft.Text(WEEK_LABELS[weekday], size=9, color=th.TEXT_MUTED, width=14), *cells],
                spacing=3,
            )
        )
    dialog = modal(
        page,
        f"{item['name']} · 打卡日历",
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(f"连续 {item['streak']} 天", size=12, color=th.PRIMARY, weight="bold"),
                        ft.Text(f"最长 {item['longest']} 天", size=11, color=th.TEXT_MUTED),
                        ft.Text(
                            f"本周 {item['week_done']}/{item['target_per_week']}",
                            size=11, color=th.TEXT_MUTED,
                        ),
                    ],
                    spacing=16,
                ),
                *grid_rows,
                ft.Text("最近 12 周，颜色越深表示当天已打卡。", size=10, color=th.TEXT_MUTED),
            ],
            spacing=6,
            tight=True,
        ),
        [ft.TextButton("关闭", on_click=lambda e: _close())],
        width=460,
    )

    def _close() -> None:
        from .widgets import close_modal

        close_modal(page, dialog)

    return dialog


def _load():
    return overview()


def build(page: ft.Page, refresh) -> ft.Control:
    return background_section(page, _load, lambda items: _render(page, refresh, items))


def _render(page: ft.Page, refresh, items: list[dict]) -> ft.Control:
    today = date.today()
    today_done = sum(1 for item in items if item["done_today"])

    def toggle(habit_id: str, done: bool) -> None:
        try:
            check_in(habit_id, today, not done)
        except Exception:
            logger.exception("习惯打卡失败")
            snack(page, "打卡失败，详见日志", th.ACCENT)
            return
        refresh()

    rows: list[ft.Control] = []
    for item in items:
        week_cells = []
        week_start = today - timedelta(days=today.weekday())
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            done = day in item["days"]
            week_cells.append(
                ft.Container(
                    width=12,
                    height=12,
                    bgcolor=th.PRIMARY if done else th.DIVIDER,
                    border_radius=3,
                    tooltip=f"{day.isoformat()} {'已打卡' if done else '未打卡'}",
                )
            )
        rows.append(
            ft.Container(
                ft.Row(
                    [
                        ft.Checkbox(
                            value=item["done_today"],
                            on_change=lambda e, hid=item["id"], d=item["done_today"]: toggle(hid, d),
                        ),
                        ft.Column(
                            [
                                ft.Text(item["name"], size=13, color=th.TEXT, weight="bold"),
                                ft.Text(
                                    f"连续 {item['streak']} 天 · 最长 {item['longest']} 天"
                                    + (
                                        f" · 单次目标 {item['target_minutes']} 分钟"
                                        if item["target_minutes"] else ""
                                    ),
                                    size=10,
                                    color=th.TEXT_MUTED,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                            tight=True,
                        ),
                        ft.Row(week_cells, spacing=3),
                        ft.Text(
                            f"{item['week_done']}/{item['target_per_week']}",
                            size=11,
                            color=th.PRIMARY if item["week_done"] >= item["target_per_week"]
                            else th.TEXT_MUTED,
                            width=44,
                        ),
                        icon_button(ACTIONS["calendar"],
                                    lambda e, it=item: open_calendar_dialog(page, it), "打卡日历"),
                        icon_button(ACTIONS["edit"],
                                    lambda e, it=item: open_habit_dialog(page, it["habit"], refresh),
                                    "编辑"),
                        icon_button(
                            ACTIONS["delete"],
                            lambda e, it=item: confirm(
                                page,
                                f"删除习惯「{it['name']}」及其打卡记录？",
                                lambda: (delete_habit(it["id"]), refresh()),
                            ),
                            "删除",
                        ),
                    ],
                    spacing=10,
                ),
                padding=ft.Padding.symmetric(vertical=8, horizontal=10),
                border=ft.Border.all(1, th.DIVIDER),
                border_radius=10,
            )
        )

    body: ft.Control = (
        ft.Column(rows, spacing=8)
        if rows
        else empty("还没有习惯。先加一个「每天阅读 30 分钟」之类的小目标。")
    )

    return card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            f"今天已打卡 {today_done}/{len(items)} 个习惯"
                            if items else "习惯会记录你的连续天数与周目标完成度",
                            size=11,
                            color=th.TEXT_MUTED,
                        ),
                        ft.Container(expand=True),
                        primary_button("新增习惯",
                                       lambda e: open_habit_dialog(page, None, refresh),
                                       ACTIONS["add"]),
                    ]
                ),
                divider(),
                ft.Column([body], scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            spacing=12,
            expand=True,
        ),
        expand=True,
    )
