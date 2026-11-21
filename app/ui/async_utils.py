"""查询后台化：重型列表/聚合在后台线程执行，回到 UI 线程再渲染。

沿用首页与图表的占位槽模式（``page.run_thread`` + ``page.run_task``），
避免数据量增长后列表查询阻塞 UI 线程。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from ..theme import TEXT_MUTED

logger = logging.getLogger(__name__)


def background_section(
    page: ft.Page,
    loader: Callable[[], object],
    renderer: Callable[[object], ft.Control],
    placeholder: str = "加载中…",
) -> ft.Container:
    """先返回占位容器，后台执行 loader，再在 UI 线程用 renderer 替换内容。"""
    holder = ft.Container(
        content=ft.Container(
            content=ft.Text(placeholder, size=11, color=TEXT_MUTED),
            alignment=ft.Alignment.CENTER,
            expand=True,
        ),
        expand=True,
    )

    def load() -> None:
        try:
            data = loader()
        except Exception:
            logger.exception("后台查询失败")
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
            logger.debug("后台查询结果回 UI 线程失败", exc_info=True)

    page.run_thread(load)
    return holder
