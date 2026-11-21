"""图片选图上传：系统选图 → 后台压缩 → 传到同步服务器 → 回填服务器链接。

封面、笔记、日记共用这一条路径，避免每处各写一遍「压图 + HTTP + 同步令牌」。
"""

from __future__ import annotations

import logging
from typing import Callable

import flet as ft

from .. import theme as th
from ..services import images
from .widgets import snack

logger = logging.getLogger(__name__)


def pick_and_upload(
    page: ft.Page,
    on_done: Callable[[str], None],
    dialog_title: str = "选择图片",
) -> None:
    """打开系统选图框；成功后把服务器链接交给 ``on_done``（在主线程执行）。"""

    async def choose() -> None:
        picker = ft.FilePicker()
        page.services.append(picker)
        try:
            files = await picker.pick_files(
                dialog_title=dialog_title,
                file_type=ft.FilePickerFileType.IMAGE,
                allow_multiple=False,
            )
        except Exception:
            logger.exception("打开文件选择器失败")
            snack(page, "无法打开文件选择器", th.DANGER)
            return
        finally:
            try:
                page.services.remove(picker)
            except ValueError:
                pass

        if not files:
            return
        picked = files[0]
        if not picked.path and not picked.bytes:
            snack(page, "读取不到所选图片，请换一张试试", th.DANGER)
            return
        snack(page, "正在压缩并上传图片…", th.TEXT_MUTED)

        def worker() -> None:
            try:
                url = (
                    images.upload_path(picked.path)
                    if picked.path
                    else images.upload_bytes(picked.bytes or b"")
                )
            except images.UploadError as exc:
                async def fail() -> None:
                    snack(page, str(exc), th.DANGER)
                page.run_task(fail)
                return
            except Exception:
                logger.exception("图片上传异常")
                async def fail() -> None:
                    snack(page, "图片上传失败，请稍后重试", th.DANGER)
                page.run_task(fail)
                return

            async def apply() -> None:
                on_done(url)
                snack(page, "图片已上传，服务器链接已填入", th.PRIMARY)

            page.run_task(apply)

        page.run_thread(worker)

    page.run_task(choose)


def upload_button(
    page: ft.Page,
    on_done: Callable[[str], None],
    label: str = "上传图片",
    dialog_title: str = "选择图片",
) -> ft.Control:
    """统一的「上传图片」按钮，自带选图与提示。"""
    return ft.TextButton(
        label,
        on_click=lambda e: pick_and_upload(page, on_done, dialog_title),
    )
