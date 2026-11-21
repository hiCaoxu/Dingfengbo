"""备份恢复中心：查看、校验、立即备份、恢复、导出完整数据。

恢复是破坏性操作：先校验备份可用、再自动备份当前库，确认后才替换文件；
替换完成后重建连接并刷新界面，避免用户看到旧数据。
"""

from __future__ import annotations

import logging
from datetime import datetime

import flet as ft

from .. import theme as th
from ..config import DATA_DIR, save_settings
from ..db.backup import (
    backup_database,
    configured_dir,
    configured_keep,
    export_full_data,
    human_size,
    list_backups,
    open_folder,
    restore_database,
    verify_backup,
)
from ..icons import ico
from .widgets import (
    close_modal,
    confirm,
    empty,
    form_field,
    modal,
    primary_button,
    snack,
)

logger = logging.getLogger(__name__)

EXPORT_DIR = DATA_DIR / "exports"


def open_backup_center(page: ft.Page, refresh=None) -> ft.AlertDialog:
    list_box = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=340)
    info = ft.Text("", size=10, color=th.TEXT_MUTED)
    dir_field = form_field("备份目录（留空使用默认）", str(configured_dir()))

    def reload() -> None:
        items = list_backups()
        if not items:
            list_box.controls = [empty("还没有备份文件，点「立即备份」生成一份")]
        else:
            list_box.controls = [
                ft.Container(
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(
                                        item["created"].strftime("%Y-%m-%d %H:%M:%S"),
                                        size=12, color=th.TEXT, weight="bold",
                                    ),
                                    ft.Text(
                                        f"{item['label']} · {human_size(item['size'])} · {item['name']}",
                                        size=10, color=th.TEXT_MUTED, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                                tight=True,
                            ),
                            ft.TextButton(
                                "校验",
                                on_click=lambda e, p=item["path"]: do_verify(p),
                            ),
                            ft.TextButton(
                                "恢复",
                                on_click=lambda e, p=item["path"]: do_restore(p),
                            ),
                        ],
                        spacing=8,
                    ),
                    padding=ft.Padding.symmetric(vertical=6, horizontal=8),
                    border=ft.Border.all(1, th.DIVIDER),
                    border_radius=8,
                )
                for item in items
            ]
        info.value = (
            f"共 {len(items)} 份备份 · 保留最近 {configured_keep()} 份 · 目录 {configured_dir()}"
        )
        page.update()

    def do_backup(_=None) -> None:
        try:
            path = backup_database(label="manual")
            save_settings({"last_backup_date": datetime.now().date().isoformat()})
        except Exception as exc:
            logger.exception("手动备份失败")
            snack(page, f"备份失败：{exc}", th.ACCENT)
            return
        reload()
        snack(page, f"已生成备份：{path.name}", th.PRIMARY)

    def do_verify(path) -> None:
        ok, message = verify_backup(path)
        snack(page, f"{path.name}：{message}", th.PRIMARY if ok else th.ACCENT)

    def do_restore(path) -> None:
        def action() -> None:
            try:
                result = restore_database(path)
            except Exception as exc:
                logger.exception("从备份恢复失败")
                snack(page, f"恢复失败：{exc}", th.ACCENT)
                return
            # 数据库文件已被替换：重建 schema 检查并刷新整个界面。
            try:
                from ..db.session import init_db

                init_db()
            except Exception:
                logger.exception("恢复后重建连接失败")
            if refresh is not None:
                refresh()
            safety = result.get("safety")
            snack(
                page,
                f"已恢复 {path.name}"
                + (f"，恢复前已备份为 {safety.name}" if safety else ""),
                th.PRIMARY,
            )

        confirm(
            page,
            f"将用 {path.name} 覆盖当前数据库。\n"
            "恢复前会自动备份当前数据，恢复后界面立即刷新；如有未同步的本地改动请先同步。",
            action,
        )

    def save_dir(_) -> None:
        value = (dir_field.value or "").strip()
        save_settings({"backup_dir": value})
        reload()
        snack(page, f"备份目录已更新：{configured_dir()}", th.TEXT_MUTED)

    def export_all(_) -> None:
        try:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = export_full_data(EXPORT_DIR / f"dingfengbo-完整数据-{stamp}.zip")
        except Exception as exc:
            logger.exception("导出完整数据失败")
            snack(page, f"导出失败：{exc}", th.ACCENT)
            return
        snack(page, f"已导出：{target}", th.PRIMARY)

    body = ft.Column(
        [
            ft.Row(
                [
                    primary_button("立即备份", do_backup, "save"),
                    ft.TextButton("打开备份目录", on_click=lambda e: _open_dir()),
                    ft.TextButton("导出完整数据 (zip)", on_click=export_all),
                ],
                spacing=8,
                wrap=True,
            ),
            info,
            list_box,
            ft.Container(height=1, bgcolor=th.DIVIDER),
            ft.Row(
                [
                    ft.Container(dir_field, expand=True),
                    ft.TextButton("保存目录", on_click=save_dir),
                ],
                spacing=8,
            ),
            ft.Text(
                "每天首次启动自动备份一次；恢复前会自动再备份当前库（pre-restore）。"
                "导出的 zip 含数据库快照与 settings.json，请妥善保管。",
                size=10,
                color=th.TEXT_MUTED,
            ),
        ],
        spacing=10,
        tight=True,
    )

    dialog = modal(
        page,
        "备份恢复中心",
        body,
        [ft.TextButton("关闭", on_click=lambda e: close_modal(page, dialog))],
        width=680,
    )

    def _open_dir() -> None:
        folder = configured_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if not open_folder(folder):
            snack(page, f"备份目录：{folder}", th.TEXT_MUTED)

    reload()
    return dialog


def open_backup_menu(page: ft.Page, refresh=None) -> None:
    """命令面板/设置页复用入口。"""
    open_backup_center(page, refresh)
