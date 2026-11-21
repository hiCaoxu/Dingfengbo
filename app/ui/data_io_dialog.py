"""数据导入导出：Markdown / CSV / JSON。

导出默认落到数据目录的 exports 下（不需要文件选择框），导入提供路径输入
与「浏览…」（tkinter 文件对话框，失败时退回手输路径）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import flet as ft

from .. import theme as th
from ..config import DATA_DIR
from ..db.backup import open_folder
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
from .widgets import close_modal, form_field, modal, primary_button, snack

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
        size=10,
        color=th.TEXT_MUTED,
    )
    result = ft.Text("", size=10, color=th.TEXT_MUTED)

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

    def do_export_json(_) -> None:
        try:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = export_json(EXPORT_DIR / f"dingfengbo-{stamp}.json")
        except Exception as exc:
            logger.exception("导出 JSON 失败")
            result.value = f"导出失败：{exc}"
            result.color = th.ACCENT
            page.update()
            return
        result.value = f"已导出 JSON：{path}"
        result.color = th.PRIMARY
        page.update()

    def do_export_markdown(_) -> None:
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            files = export_markdown(EXPORT_DIR / f"markdown-{stamp}")
        except Exception as exc:
            logger.exception("导出 Markdown 失败")
            result.value = f"导出失败：{exc}"
            result.color = th.ACCENT
            page.update()
            return
        result.value = f"已导出 {len(files)} 个 Markdown 文件到 {files[0].parent if files else EXPORT_DIR}"
        result.color = th.PRIMARY
        page.update()

    def do_export_csv(_) -> None:
        table = export_table.value or "transactions"
        try:
            path = export_csv(table, EXPORT_DIR / f"{table}.csv")
        except Exception as exc:
            logger.exception("导出 CSV 失败")
            result.value = f"导出失败：{exc}"
            result.color = th.ACCENT
            page.update()
            return
        result.value = f"已导出：{path}"
        result.color = th.PRIMARY
        page.update()

    def do_browse(_) -> None:
        def worker() -> None:
            path = _pick_file("选择要导入的文件", "*.json;*.csv")

            async def apply() -> None:
                if path:
                    path_field.value = path
                    page.update()

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
        try:
            if path.suffix.lower() == ".json":
                outcome = import_json(path)
                message = f"导入完成：写入/更新 {outcome['imported']} 条"
            elif path.suffix.lower() == ".csv":
                table = import_table.value or "transactions"
                outcome = import_csv(table, path)
                message = (
                    f"导入 {table_label(table)}：成功 {outcome['imported']}，跳过 {outcome['skipped']}"
                )
                if outcome["errors"]:
                    message += "（" + "；".join(outcome["errors"][:2]) + "）"
            else:
                snack(page, "只支持 .json 与 .csv", th.ACCENT)
                return
        except Exception as exc:
            logger.exception("导入失败")
            snack(page, f"导入失败：{exc}", th.ACCENT)
            return
        snack(page, message, th.PRIMARY)
        if refresh is not None:
            refresh()

    def open_dir(_) -> None:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        if not open_folder(EXPORT_DIR):
            snack(page, f"导出目录：{EXPORT_DIR}", th.TEXT_MUTED)

    body = ft.Column(
        [
            summary,
            ft.Container(height=1, bgcolor=th.DIVIDER),
            ft.Text("导出", size=12, color=th.TEXT, weight="bold"),
            ft.Row(
                [
                    primary_button("导出 JSON（全量）", do_export_json, "download"),
                    primary_button("导出 Markdown", do_export_markdown, "download"),
                    ft.Row([export_table, ft.TextButton("导出 CSV", on_click=do_export_csv)],
                           spacing=6),
                ],
                spacing=8,
                wrap=True,
            ),
            ft.Container(height=1, bgcolor=th.DIVIDER),
            ft.Text("导入", size=12, color=th.TEXT, weight="bold"),
            ft.Row([path_field, ft.TextButton("浏览…", on_click=do_browse)], spacing=8),
            ft.Row(
                [
                    import_table,
                    primary_button("开始导入", do_import, "upload"),
                ],
                spacing=8,
            ),
            ft.Text(
                "JSON 导入按「最后写入获胜」合并，适合换机或从旧备份恢复；导入的行会重新进入待同步队列。"
                "CSV 按列名匹配、缺列用默认值，已有记录按 ID 或业务键（日期 / 习惯+日期）去重，"
                "文件里更旧的行不会覆盖较新的数据，单行失败不影响其他行。",
                size=10,
                color=th.TEXT_MUTED,
            ),
            ft.Row([ft.TextButton("打开导出目录", on_click=open_dir), result], spacing=8),
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
