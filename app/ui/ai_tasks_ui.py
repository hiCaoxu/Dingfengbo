"""AI 任务卡与草稿写回的前端流程。

统一入口 :func:`run_task_dialog`：构造任务请求 → 后台调用模型 → 展示文本 →
若模型返回了结构化草稿，再弹出预览让用户确认后落库（可撤销）。
弹窗式实现让首页、回顾页、记账页都能直接复用，不必都跳到 AI 页面。
"""

from __future__ import annotations

import logging

import flet as ft

from .. import theme as th
from ..config import AI_PROVIDERS, load_settings, save_settings
from ..icons import ico
from ..services.ai import drafts as draft_service
from ..services.ai.client import AIConfigError, ai_configured, chat
from ..services.ai.context import get_label, read_context
from ..services.ai.provider import AIError
from ..services.ai.tasks import build_request
from .widgets import close_modal, dismiss, modal, open_dialog, primary_button, snack

logger = logging.getLogger(__name__)


def _not_configured(page: ft.Page, refresh=None) -> None:
    from .forms import open_settings_dialog

    snack(page, "还没有配置 AI，请先在设置里填写 API Key", th.ACCENT)
    open_settings_dialog(page, refresh, refresh)


def ensure_consent(page: ft.Page, refs: list[str], on_agree) -> bool:
    """首次把引用数据发给第三方模型前的知情确认；已确认返回 True。"""
    if not refs or load_settings().get("ai_data_notice_ack"):
        return True
    provider = AI_PROVIDERS.get(load_settings().get("ai_provider", "custom"), {}).get(
        "name", "自定义服务"
    )
    tables = "、".join(f"@{get_label(name)}" for name in refs)

    def close(_):
        dismiss(page, dialog)

    def agree(_):
        save_settings({"ai_data_notice_ack": True})
        close(_)
        on_agree()

    dialog = ft.AlertDialog(
        title=ft.Text("数据将发送至第三方大模型", size=14, color=th.TEXT, weight="bold"),
        content=ft.Text(
            f"本次任务引用了 {tables}，其近期记录会发送至 {provider}。"
            "该提示只出现一次，取消则不会发送任何内容。",
            size=12,
            color=th.TEXT_MUTED,
        ),
        actions=[ft.TextButton("取消", on_click=close), primary_button("同意并发送", agree)],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    open_dialog(page, dialog)
    return False


def run_task_dialog(
    page: ft.Page,
    key: str,
    extra: str = "",
    on_saved=None,
    refresh=None,
) -> None:
    """执行一个 AI 快捷任务，并在弹窗里展示结果。"""
    if not ai_configured():
        _not_configured(page, refresh)
        return
    request = build_request(key, extra)
    title = request.task.label if request.task else "AI 任务"
    if request.refs and not ensure_consent(
        page, request.refs, lambda: run_task_dialog(page, key, extra, on_saved, refresh)
    ):
        return

    output = ft.Column(
        [ft.Text("正在思考…", size=12, color=th.TEXT_MUTED, italic=True)],
        spacing=8,
        scroll=ft.ScrollMode.AUTO,
        height=360,
    )
    footer = ft.Row(spacing=8)
    holder = ft.Column([output, footer], spacing=10, tight=True)

    dialog = ft.AlertDialog(
        title=ft.Row(
            [
                ft.Icon(ico("AUTO_AWESOME_OUTLINED"), size=18, color=th.PRIMARY),
                ft.Text(title, size=14, color=th.TEXT, weight="bold"),
            ],
            spacing=8,
        ),
        content=ft.Container(holder, width=680),
        actions=[ft.TextButton("关闭", on_click=lambda e: close_modal(page, dialog))],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=12),
    )

    def work() -> tuple[str, list]:
        content = request.content
        refs = list(request.refs)
        if refs:
            content = f"用户引用了以下数据：\n{read_context(refs)}\n\n{content}"
        text = chat(
            [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ]
        )
        return text, draft_service.extract(text) if request.draft else []

    def finish(text: str, drafts: list) -> None:
        output.controls = [ft.Text(text, size=12, color=th.TEXT, selectable=True)]
        if drafts:
            valid = [item for item in drafts if item.valid]
            output.controls.append(
                ft.Text(
                    f"识别到 {len(drafts)} 条草稿（可保存 {len(valid)} 条）",
                    size=11,
                    color=th.PRIMARY if valid else th.ACCENT,
                )
            )
            footer.controls = [
                primary_button(
                    f"预览并保存（{len(valid)}）",
                    lambda e: (close_modal(page, dialog), open_draft_dialog(page, drafts, on_saved)),
                    "save",
                )
            ]
        page.update()

    def run() -> None:
        def worker() -> None:
            try:
                text, drafts = work()
            except (AIConfigError, AIError) as exc:
                text, drafts = f"⚠️ {exc}", []
            except Exception as exc:  # 网络/解析异常都要回到 UI，避免弹窗一直转圈
                logger.exception("AI 任务执行失败")
                text, drafts = f"⚠️ 出错了：{exc}", []

            async def apply() -> None:
                try:
                    finish(text, drafts)
                except Exception:
                    logger.debug("AI 任务结果渲染失败，可能已关闭", exc_info=True)

            try:
                page.run_task(apply)
            except Exception:
                logger.debug("AI 任务结果回 UI 线程失败", exc_info=True)

        page.run_thread(worker)

    open_dialog(page, dialog)
    run()


def open_draft_dialog(page: ft.Page, drafts: list, on_saved=None) -> ft.AlertDialog:
    """草稿预览：逐条展示字段，确认后一次性保存并支持撤销。"""
    rows: list[ft.Control] = []
    for draft in drafts:
        detail_parts = []
        for name, value in draft.fields.items():
            if value in (None, "", "[]", False):
                continue
            if name in ("tags",):
                continue
            detail_parts.append(f"{name}={value}")
        rows.append(
            ft.Container(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    ft.Text(draft.label, size=9, color=th.PRIMARY),
                                    bgcolor=th.PRIMARY_LIGHT,
                                    border_radius=6,
                                    padding=ft.Padding.symmetric(vertical=2, horizontal=6),
                                ),
                                ft.Text(draft.title, size=12, color=th.TEXT, weight="bold",
                                        expand=True, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(
                                    "可保存" if draft.valid else draft.reason,
                                    size=9,
                                    color=th.PRIMARY if draft.valid else th.DANGER,
                                ),
                            ],
                            spacing=6,
                        ),
                        ft.Text(" · ".join(detail_parts)[:200], size=10, color=th.TEXT_MUTED),
                    ],
                    spacing=4,
                    tight=True,
                ),
                border=ft.Border.all(1, th.DIVIDER),
                border_radius=8,
                padding=ft.Padding.all(8),
            )
        )

    def save(_) -> None:
        try:
            saved = draft_service.save(drafts)
        except Exception:
            logger.exception("保存 AI 草稿失败")
            snack(page, "保存失败，详见日志", th.ACCENT)
            return
        close_modal(page, dialog)
        if not saved:
            snack(page, "没有可保存的草稿", th.TEXT_MUTED)
            return
        bar = ft.SnackBar(
            ft.Text(f"已保存 {len(saved)} 条记录", color="#FFFFFF", size=12),
            bgcolor=th.PRIMARY,
            action="撤销",
            on_action=lambda e: _undo(saved),
        )
        page.snack_bar = bar
        bar.open = True
        page.update()
        if on_saved is not None:
            on_saved()

    def _undo(saved: list) -> None:
        removed = draft_service.undo(saved)
        snack(page, f"已撤销 {removed} 条记录", th.TEXT_MUTED)
        if on_saved is not None:
            on_saved()

    dialog = modal(
        page,
        "AI 草稿预览",
        ft.Column(
            [
                ft.Text(
                    "以下内容由模型生成，保存前请确认；保存后仍可在对应模块修改。",
                    size=10, color=th.TEXT_MUTED,
                ),
                ft.Column(rows, spacing=6, scroll=ft.ScrollMode.AUTO, height=340),
            ],
            spacing=8,
            tight=True,
        ),
        [
            ft.TextButton("取消", on_click=lambda e: close_modal(page, dialog)),
            primary_button("保存到本地", save, "save"),
        ],
        width=640,
    )
    return dialog
