"""备份恢复中心：查看、校验、立即备份、恢复、导出完整数据。

恢复是破坏性操作：先校验备份可用、再自动备份当前库，确认后才替换文件；
替换完成后重建连接并刷新界面，避免用户看到旧数据。

备份 / 校验 / 恢复 / 导出都是文件 IO 与整库拷贝，动辄几百毫秒到数秒，
一律放到后台线程执行，UI 线程只负责按钮状态与提示——否则窗口会整体卡死，
连「正在处理」都画不出来。恢复期间还会暂停自动同步并等正在跑的同步落地。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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
    info = ft.Text("", size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=2,
                   overflow=ft.TextOverflow.ELLIPSIS)
    dir_field = form_field("备份目录（留空使用默认）", str(configured_dir()))
    busy = {"value": False}
    fixed_buttons: list[ft.Control] = []
    row_buttons: list[ft.Control] = []

    def set_busy(state: bool) -> None:
        busy["value"] = state
        for button in fixed_buttons + row_buttons:
            button.disabled = state
        try:
            page.update()
        except Exception:
            logger.debug("备份中心按钮状态刷新失败，页面可能已销毁", exc_info=True)

    def run_action(work: Callable[[], object], on_done: Callable[[object, Exception | None], None]) -> None:
        """后台执行一个耗时操作，回到 UI 线程收尾并恢复按钮。"""
        if busy["value"]:
            snack(page, "上一个操作还没结束，请稍候", th.ACCENT)
            return
        set_busy(True)

        def worker() -> None:
            try:
                payload, error = work(), None
            except Exception as exc:  # 交给 UI 线程统一提示
                logger.warning("备份操作失败：%s", exc)
                payload, error = None, exc

            async def finish() -> None:
                set_busy(False)
                on_done(payload, error)

            try:
                page.run_task(finish)
            except Exception:
                logger.debug("备份操作结果回 UI 线程失败，页面可能已销毁", exc_info=True)

        page.run_thread(worker)

    def reload() -> None:
        items = list_backups()
        row_buttons.clear()
        if not items:
            list_box.controls = [empty("还没有备份文件，点「立即备份」生成一份")]
        else:
            rows = []
            for item in items:
                verify_button = ft.TextButton(
                    "校验",
                    on_click=lambda e, p=item["path"]: do_verify(p),
                )
                restore_button = ft.TextButton(
                    "恢复",
                    on_click=lambda e, p=item["path"]: do_restore(p),
                )
                verify_button.disabled = busy["value"]
                restore_button.disabled = busy["value"]
                row_buttons.extend([verify_button, restore_button])
                rows.append(
                    ft.Container(
                        ft.Row(
                            [
                                ft.Column(
                                    [
                                        ft.Text(
                                            item["created"].strftime("%Y-%m-%d %H:%M:%S"),
                                            size=th.FONT_CARD_TITLE, color=th.TEXT,
                                            weight="bold",
                                        ),
                                        ft.Text(
                                            f"{item['label']} · {human_size(item['size'])} · {item['name']}",
                                            size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        ),
                                    ],
                                    spacing=2,
                                    expand=True,
                                    tight=True,
                                ),
                                verify_button,
                                restore_button,
                            ],
                            spacing=8,
                        ),
                        padding=ft.Padding.symmetric(vertical=6, horizontal=8),
                        border=ft.Border.all(1, th.DIVIDER),
                        border_radius=8,
                    )
                )
            list_box.controls = rows
        info.value = (
            f"共 {len(items)} 份备份 · 保留最近 {configured_keep()} 份 · 目录 {configured_dir()}"
        )
        page.update()

    def do_backup(_=None) -> None:
        def work():
            path = backup_database(label="manual")
            save_settings({"last_backup_date": datetime.now().date().isoformat()})
            return path

        def done(payload, error) -> None:
            if error is not None:
                snack(page, f"备份失败：{error}", th.ACCENT)
                return
            reload()
            snack(page, f"已生成备份：{payload.name}", th.PRIMARY)

        run_action(work, done)

    def do_verify(path) -> None:
        def done(payload, error) -> None:
            if error is not None:
                snack(page, f"校验失败：{error}", th.ACCENT)
                return
            ok, message = payload
            snack(page, f"{path.name}：{message}", th.PRIMARY if ok else th.ACCENT)

        run_action(lambda: verify_backup(path), done)

    def do_restore(path) -> None:
        def work():
            # 恢复期间必须停掉自动同步：后台 pull 正持有连接，库被整库替换后
            # 它继续提交会写进已关闭的库（database is closed），也可能把旧数据
            # 灌回刚恢复的库。先暂停，再等正在跑的那一次同步结束。
            from ..services.sync import sync_client
            from .scheduler import pause_auto_sync, resume_auto_sync

            pause_auto_sync("备份恢复中")
            try:
                sync_client.wait_idle()
                result = restore_database(path)
                # 数据库文件已被替换：重建 schema 检查。
                from ..db.session import init_db

                init_db()
                return result
            finally:
                resume_auto_sync()

        def done(payload, error) -> None:
            if error is not None:
                snack(page, f"恢复失败：{error}", th.ACCENT)
                return
            # 数据库文件已被替换：刷新整个界面，避免用户看到旧数据。
            if refresh is not None:
                refresh()
            safety = (payload or {}).get("safety")
            snack(
                page,
                f"已恢复 {path.name}"
                + (f"，恢复前已备份为 {safety.name}" if safety else "")
                + "；同步游标已重置，下次同步会重新拉取云端全量（冲突以较新记录为准）。",
                th.PRIMARY,
            )

        confirm(
            page,
            f"将用 {path.name} 覆盖当前数据库。\n"
            "恢复前会自动再备份一份当前库（可用它回退）；恢复期间自动同步会暂停。\n"
            "注意：恢复后自动同步照常继续——若云端数据更新，下次同步（默认 5 分钟内）"
            "会以云端为准覆盖这份备份。想保留当前备份的状态，请先在设置里关闭同步。",
            lambda: run_action(work, done),
        )

    def save_dir(_) -> None:
        value = (dir_field.value or "").strip()
        save_settings({"backup_dir": value})
        reload()
        snack(page, f"备份目录已更新：{configured_dir()}", th.TEXT_MUTED)

    def export_all(_) -> None:
        def work():
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return export_full_data(EXPORT_DIR / f"dingfengbo-完整数据-{stamp}.zip")

        def done(payload, error) -> None:
            if error is not None:
                snack(page, f"导出失败：{error}", th.ACCENT)
                return
            # 导出包里的 settings.json 已剥离凭证：换机导入后需重新填同步令牌与 AI Key。
            snack(page, f"已导出：{payload}（不含同步令牌 / AI Key）", th.PRIMARY)

        run_action(work, done)

    backup_button = primary_button("立即备份", do_backup, "SAVE_OUTLINED")
    export_button = ft.TextButton("导出完整数据 (zip)", on_click=export_all)
    fixed_buttons.extend([backup_button, export_button])

    body = ft.Column(
        [
            ft.Row(
                [
                    backup_button,
                    ft.TextButton("打开备份目录", on_click=lambda e: _open_dir()),
                    export_button,
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
                size=th.FONT_CAPTION,
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
