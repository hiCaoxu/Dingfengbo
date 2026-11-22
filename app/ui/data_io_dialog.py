"""数据导入导出：Markdown / CSV / JSON。

导出默认落到数据目录的 exports 下（不需要文件选择框），导入提供路径输入
与「浏览…」（tkinter 文件对话框，失败时退回手输路径）。

所有导出/导入都是整库读写，动辄几百毫秒到数秒，一律放后台线程执行；
导入是破坏性操作（按最后写入获胜合并），因此先确认、再自动备份当前库。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

import flet as ft

from .. import theme as th
from ..config import DATA_DIR
from ..db.backup import backup_database, open_folder
from ..db.models import table_label
from ..services.data_io import (
    EXPORT_TABLES,
    counts,
    export_csv,
    export_json,
    export_markdown,
    import_csv,
    import_json,
)
from .widgets import close_modal, confirm, form_field, modal, primary_button, snack

logger = logging.getLogger(__name__)

EXPORT_DIR = DATA_DIR / "exports"


def _pick_file(title: str, patterns: str = "*.*") -> str:
    """同步打开系统文件对话框（调用方负责放到后台线程）。"""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title=title, filetypes=[("文件", patterns), ("全部", "*.*")])
        root.destroy()
        return path or ""
    except Exception:
        logger.debug("系统文件对话框不可用", exc_info=True)
        return ""


def open_data_io_dialog(page: ft.Page, refresh=None) -> ft.AlertDialog:
    snapshot = counts()
    total = sum(snapshot.values())
    summary = ft.Text(
        f"当前共 {total} 条记录：" + " · ".join(
            f"{table_label(table)} {snapshot.get(table, 0)}"
            for table in EXPORT_TABLES
            if snapshot.get(table)
        ),
        size=th.FONT_CAPTION,
        color=th.TEXT_MUTED,
    )
    result = ft.Text("", size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=3,
                     overflow=ft.TextOverflow.ELLIPSIS)

    table_options = [
        ft.dropdown.Option(key=table, text=f"{table_label(table)}（{snapshot.get(table, 0)}）")
        for table in EXPORT_TABLES
    ]
    export_table = ft.Dropdown(
        label="CSV 导出单表",
        value="transactions",
        options=table_options,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        width=260,
    )
    import_table = ft.Dropdown(
        label="CSV 导入目标表",
        value="transactions",
        options=table_options,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        width=260,
    )
    path_field = form_field("导入文件路径（JSON 或 CSV）", "")

    busy = {"value": False}
    action_buttons: list[ft.Control] = []

    def set_busy(state: bool) -> None:
        busy["value"] = state
        for button in action_buttons:
            button.disabled = state
        try:
            page.update()
        except Exception:
            logger.debug("导入导出按钮状态刷新失败，页面可能已销毁", exc_info=True)

    def run_action(
        work: Callable[[], object],
        on_done: Callable[[object, Exception | None], None],
    ) -> None:
        """后台执行整库读写，回到 UI 线程收尾并恢复按钮。"""
        if busy["value"]:
            snack(page, "上一个操作还没结束，请稍候", th.ACCENT)
            return
        set_busy(True)

        def worker() -> None:
            try:
                payload, error = work(), None
            except Exception as exc:  # 交给 UI 线程统一提示
                logger.warning("数据操作失败：%s", exc)
                payload, error = None, exc

            async def finish() -> None:
                set_busy(False)
                on_done(payload, error)

            try:
                page.run_task(finish)
            except Exception:
                logger.debug("数据操作结果回 UI 线程失败，页面可能已销毁", exc_info=True)

        page.run_thread(worker)

    def _set_result(text: str, ok: bool = True) -> None:
        result.value = text
        result.color = th.PRIMARY if ok else th.ACCENT
        page.update()

    def do_export_json(_) -> None:
        def work():
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return export_json(EXPORT_DIR / f"dingfengbo-{stamp}.json")

        def done(payload, error) -> None:
            if error is not None:
                _set_result(f"导出失败：{error}", ok=False)
                return
            _set_result(f"已导出 JSON：{payload}")

        run_action(work, done)

    def do_export_markdown(_) -> None:
        def work():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return export_markdown(EXPORT_DIR / f"markdown-{stamp}")

        def done(payload, error) -> None:
            if error is not None:
                _set_result(f"导出失败：{error}", ok=False)
                return
            folder = payload[0].parent if payload else EXPORT_DIR
            _set_result(f"已导出 {len(payload or [])} 个 Markdown 文件到 {folder}")

        run_action(work, done)

    def do_export_csv(_) -> None:
        table = export_table.value or "transactions"

        def work():
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            return export_csv(table, EXPORT_DIR / f"{table}.csv")

        def done(payload, error) -> None:
            if error is not None:
                _set_result(f"导出失败：{error}", ok=False)
                return
            _set_result(f"已导出：{payload}")

        run_action(work, done)

    def do_browse(_) -> None:
        def worker() -> None:
            path = _pick_file("选择要导入的文件", "*.json;*.csv")

            async def apply() -> None:
                if path:
                    path_field.value = path
                    page.update()
                    return
                snack(page, "没有选到文件，可直接粘贴路径", th.TEXT_MUTED)

            try:
                page.run_task(apply)
            except Exception:
                logger.debug("回填导入路径失败", exc_info=True)

        page.run_thread(worker)

    def do_import(_) -> None:
        raw = (path_field.value or "").strip()
        if not raw:
            snack(page, "请先填写或选择要导入的文件", th.ACCENT)
            return
        path = Path(raw)
        if not path.exists():
            snack(page, f"文件不存在：{path}", th.ACCENT)
            return
        suffix = path.suffix.lower()
        if suffix not in (".json", ".csv"):
            snack(page, "只支持 .json 与 .csv", th.ACCENT)
            return
        table = import_table.value or "transactions"
        target = table_label(table) if suffix == ".csv" else "全部数据"
        confirm(
            page,
            f"将把 {path.name} 导入到「{target}」。\n"
            "合并规则是「最后写入获胜」，本地较新的记录不会被覆盖；"
            "但导入前会自动备份当前库，以便随时回退。\n确定继续？",
            lambda: _run_import(path, suffix, table),
        )

    def _run_import(path: Path, suffix: str, table: str) -> None:
        def work():
            # 先备份再动库：导入是不可撤销的合并操作。
            backup = backup_database(label="pre-import")
            if suffix == ".json":
                outcome = import_json(path)
                message = f"导入完成：写入/更新 {outcome['imported']} 条"
                if outcome["skipped"]:
                    message += f"，跳过 {outcome['skipped']} 条"
            else:
                outcome = import_csv(table, path)
                message = (
                    f"导入 {table_label(table)}：成功 {outcome['imported']}，"
                    f"跳过 {outcome['skipped']}"
                )
                if outcome["errors"]:
                    message += "（" + "；".join(outcome["errors"][:2]) + "）"
            return message, backup

        def done(payload, error) -> None:
            if error is not None:
                _set_result(f"导入失败：{error}", ok=False)
                return
            message, backup = payload
            _set_result(f"{message}；导入前已备份为 {backup.name}")
            if refresh is not None:
                refresh()

        run_action(work, done)

    def open_dir(_) -> None:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        if not open_folder(EXPORT_DIR):
            snack(page, f"导出目录：{EXPORT_DIR}", th.TEXT_MUTED)

    export_json_button = primary_button("导出 JSON（全量）", do_export_json, "DOWNLOAD_OUTLINED")
    export_md_button = primary_button("导出 Markdown", do_export_markdown, "DOWNLOAD_OUTLINED")
    export_csv_button = ft.TextButton("导出 CSV", on_click=do_export_csv)
    import_button = primary_button("开始导入", do_import, "UPLOAD_OUTLINED")
    action_buttons.extend([export_json_button, export_md_button, export_csv_button, import_button])

    body = ft.Column(
        [
            summary,
            ft.Container(height=1, bgcolor=th.DIVIDER),
            ft.Text("导出", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
            ft.Row(
                [
                    export_json_button,
                    export_md_button,
                    ft.Row([export_table, export_csv_button], spacing=6),
                ],
                spacing=8,
                wrap=True,
            ),
            ft.Container(height=1, bgcolor=th.DIVIDER),
            ft.Text("导入", size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
            ft.Row([path_field, ft.TextButton("浏览…", on_click=do_browse)], spacing=8),
            ft.Row([import_table, import_button], spacing=8),
            ft.Text(
                "JSON 导入按「最后写入获胜」合并，适合换机或从旧备份恢复；导入的行会重新进入待同步队列。"
                "CSV 按列名匹配、缺列用默认值；带 id 的行按 id 精确更新，无 id 时日记/笔记/习惯等"
                "按业务键去重（日期「习惯+日期」），而**收支一律追加**——同一天两笔同额交易是合法的，"
                "按业务键合并会静默少一笔。单行失败不影响其他行。",
                size=th.FONT_CAPTION,
                color=th.TEXT_MUTED,
            ),
            ft.Row([ft.TextButton("打开导出目录", on_click=open_dir), result],
                   spacing=8, wrap=True),
        ],
        spacing=10,
        tight=True,
    )

    dialog = modal(
        page,
        "数据导入导出",
        body,
        [ft.TextButton("关闭", on_click=lambda e: close_modal(page, dialog))],
        width=680,
    )
    return dialog
