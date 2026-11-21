"""查询后台化：重型列表/聚合在后台线程执行，回到 UI 线程再渲染。

沿用首页与图表的占位槽模式（``page.run_thread`` + ``page.run_task``），
避免数据量增长后列表查询阻塞 UI 线程。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from ..theme import ACCENT, TEXT_MUTED

logger = logging.getLogger(__name__)


def _placeholder(text: str) -> ft.Control:
    return ft.Container(
        content=ft.Text(text, size=11, color=TEXT_MUTED),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def _error_state(on_retry: Callable[[], None]) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text("加载失败（可能是数据库忙或磁盘异常）", size=11, color=ACCENT),
                ft.TextButton("重试", on_click=lambda e: on_retry()),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def background_section(
    page: ft.Page,
    loader: Callable[[], object],
    renderer: Callable[[object], ft.Control],
    placeholder: str = "加载中…",
) -> ft.Container:
    """先返回占位容器，后台执行 loader，再在 UI 线程用 renderer 替换内容。

    失败时渲染「加载失败 + 重试」，而不是把占位槽永远停在「加载中…」——
    静默的无限加载比报错更难排查。
    """
    holder = ft.Container(content=_placeholder(placeholder), expand=True)

    def retry() -> None:
        holder.content = _placeholder(placeholder)
        page.update()
        page.run_thread(load)

    def load() -> None:
        try:
            data = loader()
        except Exception:
            logger.exception("后台查询失败")

            async def show_error() -> None:
                try:
                    holder.content = _error_state(retry)
                    page.update()
                except Exception:
                    logger.debug("错误占位渲染失败，页面可能已销毁", exc_info=True)

            try:
                page.run_task(show_error)
            except Exception:
                logger.debug("错误占位回 UI 线程失败，页面可能已销毁", exc_info=True)
            return

        async def apply() -> None:
            try:
                holder.content = renderer(data)
                page.update()
            except Exception:
                logger.debug("后台查询结果渲染失败，页面可能已销毁", exc_info=True)

        try:
            page.run_task(apply)
        except Exception:
            logger.debug("后台查询结果回 UI 线程失败，页面可能已销毁", exc_info=True)

    page.run_thread(load)
    return holder
