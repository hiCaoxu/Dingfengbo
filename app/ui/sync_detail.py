"""同步明细：失败记录、历史、设备与本机选择性同步。

状态栏只显示「待同步 N · 失败 M」，用户看到失败数时最想知道的是「哪条、为什么」，
这里把三件事都摊开，并支持单条重试/忽略。
"""

from __future__ import annotations

import logging
from datetime import datetime

import flet as ft

from .. import theme as th
from ..config import load_settings, save_settings
from ..db.models import MODEL_BY_TABLE, table_label
from ..db.repo import (
    clear_conflicts,
    ignore_issue,
    list_conflicts,
    list_error_records,
    retry_issue,
    sync_queue_stats,
)
from ..db.backup import reset_sync_cursors
from ..services.sync import mark_pending_write, sync_devices, sync_history
from .widgets import close_modal, modal, snack

logger = logging.getLogger(__name__)

VIEWS = ("失败明细", "冲突", "同步历史", "设备", "选择性同步")


def _load_lists():
    from ..db.session import session_scope

    with session_scope() as session:
        stats = sync_queue_stats(session)
        errors = list_error_records(session)
        conflicts = list_conflicts(session)
    settings = load_settings()
    selected = set(settings.get("sync_tables") or [])
    return {
        "stats": stats,
        "errors": errors,
        "conflicts": conflicts,
        "history": sync_history(),
        "devices": sync_devices(),
        "selected": selected,
    }


def open_sync_detail(page: ft.Page, refresh_status=None) -> ft.AlertDialog:
    from ..db.session import session_scope

    data = _load_lists()
    content = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, height=380)
    active = {"view": VIEWS[0]}
    tabs_row = ft.Row(spacing=6)

    def reload() -> None:
        with session_scope() as session:
            data["stats"] = sync_queue_stats(session)
            data["errors"] = list_error_records(session)
            data["conflicts"] = list_conflicts(session)
        data["history"] = sync_history()
        data["devices"] = sync_devices()
        data["selected"] = set(load_settings().get("sync_tables") or [])
        render()

    def render() -> None:
        tabs_row.controls = [
            ft.Button(
                label,
                bgcolor=th.PRIMARY if active["view"] == label else th.PRIMARY_LIGHT,
                color=th.ON_PRIMARY if active["view"] == label else th.TEXT_MUTED,
                elevation=0,
                on_click=lambda e, name=label: switch(name),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=12),
                ),
            )
            for label in VIEWS
        ]
        if active["view"] == "失败明细":
            content.controls = _errors_view()
        elif active["view"] == "冲突":
            content.controls = _conflicts_view()
        elif active["view"] == "同步历史":
            content.controls = _history_view()
        elif active["view"] == "设备":
            content.controls = _devices_view()
        else:
            content.controls = _selective_view()
        page.update()

    def switch(name: str) -> None:
        active["view"] = name
        render()

    def _errors_view() -> list[ft.Control]:
        errors = data["errors"]
        stats = data["stats"]
        controls: list[ft.Control] = [
            ft.Text(
                f"待同步 {stats.get('pending', 0)} 条 · 被拒 {stats.get('error', 0)} 条",
                size=th.FONT_BODY,
                color=th.ACCENT if stats.get("error") else th.TEXT_MUTED,
                weight="bold",
            )
        ]
        if not errors:
            controls.append(ft.Text("没有被拒记录。", size=th.FONT_CAPTION,
                                    color=th.TEXT_MUTED))
            return controls
        for row in errors:
            controls.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(row["table_label"], size=th.FONT_CAPTION,
                                                    color=th.PRIMARY),
                                            ft.Text(row["title"], size=th.FONT_CARD_TITLE,
                                                    color=th.TEXT,
                                                    weight="bold", max_lines=1,
                                                    overflow=ft.TextOverflow.ELLIPSIS),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.Text(
                                        (row["reason"] or "未提供原因")
                                        + " · "
                                        + (row["created_at"].strftime("%m-%d %H:%M")
                                           if isinstance(row["created_at"], datetime) else ""),
                                        size=th.FONT_CAPTION,
                                        color=th.TEXT_MUTED,
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                                tight=True,
                            ),
                            ft.TextButton(
                                "重试",
                                on_click=lambda e, r=row: do_retry(r),
                            ),
                            ft.TextButton(
                                "忽略",
                                on_click=lambda e, r=row: do_ignore(r),
                            ),
                        ],
                        spacing=6,
                    ),
                    padding=ft.Padding.all(8),
                    border=ft.Border.all(1, th.DIVIDER),
                    border_radius=8,
                )
            )
        controls.append(
            ft.Text(
                "重试会把该条放回待同步队列；忽略则标记为已同步并停止重推。",
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
            )
        )
        return controls

    def _conflicts_view() -> list[ft.Control]:
        conflicts = data.get("conflicts") or []
        controls: list[ft.Control] = [
            ft.Text(
                "同步冲突：远端版本比本地旧，本地获胜并保留了冲突记录，供人工核对。",
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
            )
        ]
        if not conflicts:
            controls.append(ft.Text("没有冲突记录。", size=th.FONT_CAPTION,
                                    color=th.TEXT_MUTED))
            return controls
        for row in conflicts:
            controls.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Row(
                                        [
                                            ft.Text(row["table_label"], size=th.FONT_CAPTION,
                                                    color=th.PRIMARY),
                                            ft.Text(row["title"], size=th.FONT_CARD_TITLE,
                                                    color=th.TEXT,
                                                    weight="bold", max_lines=1,
                                                    overflow=ft.TextOverflow.ELLIPSIS),
                                        ],
                                        spacing=6,
                                    ),
                                    ft.Text(
                                        "本地 "
                                        + (row["local"].strftime("%m-%d %H:%M")
                                           if isinstance(row["local"], datetime) else "?")
                                        + " / 远端 "
                                        + (row["remote"].strftime("%m-%d %H:%M")
                                           if isinstance(row["remote"], datetime) else "?"),
                                        size=th.FONT_CAPTION,
                                        color=th.TEXT_MUTED,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                                tight=True,
                            ),
                        ],
                        spacing=6,
                    ),
                    padding=ft.Padding.all(8),
                    border=ft.Border.all(1, th.DIVIDER),
                    border_radius=8,
                )
            )

        def clear(_) -> None:
            with session_scope() as session:
                removed = clear_conflicts(session)
            snack(page, f"已清除 {removed} 条冲突记录", th.TEXT_MUTED)
            reload()

        controls.append(ft.TextButton("清除全部冲突记录", on_click=clear))
        return controls

    def _history_view() -> list[ft.Control]:
        history = data["history"]
        if not history:
            return [ft.Text("还没有同步记录。", size=th.FONT_CAPTION, color=th.TEXT_MUTED)]
        return [
            ft.Row(
                [
                    ft.Text(item.get("time", ""), size=th.FONT_CAPTION,
                            color=th.TEXT_MUTED, width=140),
                    ft.Text(item.get("status", ""), size=th.FONT_BODY, color=th.TEXT,
                            expand=True, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            for item in history
        ]

    def _devices_view() -> list[ft.Control]:
        devices = data["devices"]
        if not devices:
            return [ft.Text("还没有同步过，本机设备记录会在首次同步后出现。",
                            size=th.FONT_CAPTION, color=th.TEXT_MUTED)]
        return [
            ft.Container(
                ft.Column(
                    [
                        ft.Text(f"{item['host']} · {item['server']}", size=th.FONT_CARD_TITLE,
                                color=th.TEXT, weight="bold", max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(
                            f"首次 {item['first_seen']} · 最近 {item['last_sync']}",
                            size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=2,
                    tight=True,
                ),
                padding=ft.Padding.all(8),
                border=ft.Border.all(1, th.DIVIDER),
                border_radius=8,
            )
            for item in devices
        ]

    def _selective_view() -> list[ft.Control]:
        selected = data["selected"] or set(MODEL_BY_TABLE)
        boxes = []
        for table in MODEL_BY_TABLE:
            if table_label(table) == table:
                continue
            boxes.append(
                ft.Checkbox(
                    label=table_label(table),
                    value=table in selected,
                    data=table,
                    label_style=th.checkbox_label_style(),
                )
            )
        controls: list[ft.Control] = [
            ft.Text(
                "取消勾选的模块不参与上传/下载（游标也会保留，重新勾选后会补拉历史）。",
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
            ),
            ft.Row(boxes, wrap=True, spacing=8, run_spacing=4),
        ]

        def save(_) -> None:
            chosen = [box.data for box in boxes if box.value]
            if len(chosen) == len(boxes):
                save_settings({"sync_tables": []})
            else:
                save_settings({"sync_tables": chosen})
            snack(page, "选择性同步已保存，下一轮同步生效", th.PRIMARY)
            reload()

        controls.append(ft.Row([ft.TextButton("保存选择", on_click=save)], spacing=6))
        return controls

    def do_retry(row: dict) -> None:
        with session_scope() as session:
            changed = retry_issue(session, row["table"], row["id"])
        if changed:
            mark_pending_write()
        snack(page, "已重新排队" if changed else "该记录已不在失败队列", th.PRIMARY)
        reload()
        if refresh_status is not None:
            refresh_status()

    def do_ignore(row: dict) -> None:
        with session_scope() as session:
            changed = ignore_issue(session, row["table"], row["id"])
        snack(page, "已忽略该条记录" if changed else "该记录已不在失败队列", th.TEXT_MUTED)
        reload()
        if refresh_status is not None:
            refresh_status()

    def do_reset_cursors(_=None) -> None:
        """游标与库内容不匹配时自救：恢复备份/手工换库后，旧游标会让云端旧数据
        永远拉不回来（表现为「同步成功但列表空」）。清空后重新拉全量。"""
        reset_sync_cursors()
        mark_pending_write()  # 走「写入后 10 秒同步」通道，不必手动再点一次
        snack(page, "同步游标已重置，10 秒内会重新拉取云端全量数据", th.PRIMARY)
        reload()
        if refresh_status is not None:
            refresh_status()

    dialog = modal(
        page,
        "同步明细",
        ft.Column([tabs_row, ft.Container(height=1, bgcolor=th.DIVIDER), content], spacing=8,
                  tight=True),
        [
            ft.TextButton("重新拉取云端全量", on_click=do_reset_cursors),
            ft.TextButton("刷新", on_click=lambda e: reload()),
            ft.TextButton("关闭", on_click=lambda e: close_modal(page, dialog)),
        ],
        width=680,
    )
    render()
    return dialog
