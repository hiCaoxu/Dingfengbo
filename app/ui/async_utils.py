"""查询后台化：重型列表/聚合在后台线程执行，回到 UI 线程再渲染。

沿用首页与图表的占位槽模式（``page.run_thread`` + ``page.run_task``），
避免数据量增长后列表查询阻塞 UI 线程。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import flet as ft

from .. import theme as th

logger = logging.getLogger(__name__)

def _placeholder(text: str) -> ft.Control:
    return ft.Container(
        content=ft.Text(text, size=th.FONT_CAPTION, color=th.TEXT_MUTED),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def _error_state(on_retry: Callable[[], None]) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text("加载失败（可能是数据库忙或磁盘异常）", size=th.FONT_BODY,
                        color=th.ACCENT, text_align=ft.TextAlign.CENTER),
                ft.TextButton("重试", on_click=lambda e: on_retry()),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


class RenderToken:
    """页面渲染序号：每次重建界面 ``bump()`` 一次，后台任务拿着旧序号即作废。

    慢查询晚到时界面可能已经换了一版（切模块、切月份、切筛选），旧结果写回去
    就是把用户刚看到的新数据刷成旧数据；序号比对是这里唯一可靠的守卫。
    """

    def __init__(self) -> None:
        self._value = 0

    def bump(self) -> int:
        self._value += 1
        return self._value

    def current(self) -> int:
        return self._value

    def is_current(self, value: int) -> bool:
        return value == self._value


def background_section(
    page: ft.Page,
    loader: Callable[[], object],
    renderer: Callable[[object], ft.Control],
    placeholder: str = "加载中…",
    token: RenderToken | None = None,
) -> ft.Container:
    """先返回占位容器，后台执行 loader，再在 UI 线程用 renderer 替换内容。

    失败时渲染「加载失败 + 重试」，而不是把占位槽永远停在「加载中…」——
    静默的无限加载比报错更难排查。``token`` 提供跨重建的过期判定：同一槽位
    连续加载以最后一次为准，整页重建后旧结果直接丢弃。
    """
    holder = ft.Container(content=_placeholder(placeholder), expand=True)
    state = {"attempt": 0}

    def is_stale(attempt: int, token_value: int | None) -> bool:
        if attempt != state["attempt"]:
            return True  # 同一槽位已经有更新的一次加载
        if token is not None and token_value is not None and not token.is_current(token_value):
            return True  # 整页已重建
        return False

    def retry() -> None:
        holder.content = _placeholder(placeholder)
        page.update()
        page.run_thread(load)

    def load() -> None:
        state["attempt"] += 1
        attempt = state["attempt"]
        token_value = token.current() if token is not None else None
        try:
            data = loader()
        except Exception:
            logger.exception("后台查询失败")

            async def show_error() -> None:
                if is_stale(attempt, token_value):
                    logger.debug("过期的查询失败结果已丢弃")
                    return
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
            if is_stale(attempt, token_value):
                logger.debug("过期的后台查询结果已丢弃")
                return
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
