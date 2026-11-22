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


def ensure_consent(page: ft.Page, scope: str, on_agree) -> bool:
    """首次把本地数据发给第三方模型前的知情确认；已确认/无数据返回 True。

    ``scope`` 是**本次实际会外发的数据范围**，必须包含 source=focus/report 这类
    任务隐式带上的统计——只看 @引用会让「今日建议/写周报」绕过确认。
    """
    if not scope or load_settings().get("ai_data_notice_ack"):
        return True
    provider = AI_PROVIDERS.get(load_settings().get("ai_provider", "custom"), {}).get(
        "name", "自定义服务"
    )

    def close(_):
        dismiss(page, dialog)

    def agree(_):
        save_settings({"ai_data_notice_ack": True})
        close(_)
        on_agree()

    dialog = ft.AlertDialog(
        bgcolor=th.CARD,
        title=ft.Text("数据将发送至第三方大模型", size=th.FONT_SECTION_TITLE,
                      color=th.TEXT, weight="bold"),
        content=ft.Text(
            f"本次任务会把{scope}通过网络发送至 {provider}。"
            "该提示只出现一次，取消则不会发送任何内容。",
            size=th.FONT_BODY,
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
    if request.sends_data and not ensure_consent(
        page, request.data_scope, lambda: run_task_dialog(page, key, extra, on_saved, refresh)
    ):
        return

    output = ft.Column(
        [ft.Text("正在思考…", size=th.FONT_CAPTION, color=th.TEXT_MUTED, italic=True)],
        spacing=8,
        scroll=ft.ScrollMode.AUTO,
        height=360,
    )
    footer = ft.Row(spacing=8)
    holder = ft.Column([output, footer], spacing=10, tight=True)

    dialog = ft.AlertDialog(
        bgcolor=th.CARD,
        title=ft.Row(
            [
                ft.Icon(ico("AUTO_AWESOME_OUTLINED"), size=18, color=th.PRIMARY),
                ft.Text(title, size=th.FONT_SECTION_TITLE, color=th.TEXT, weight="bold"),
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
            content = f"用户引用了以下数据：\n{read_context(refs, since=request.since)}\n\n{content}"
        text = chat(
            [
                {"role": "system", "content": request.system},
                {"role": "user", "content": content},
            ]
        )
        return text, draft_service.extract(text) if request.draft else []

    def finish(text: str, drafts: list) -> None:
        output.controls = [ft.Text(text, size=th.FONT_BODY, color=th.TEXT, selectable=True)]
        if drafts:
            valid = [item for item in drafts if item.valid]
            output.controls.append(
                ft.Text(
                    f"识别到 {len(drafts)} 条草稿（可保存 {len(valid)} 条）",
                    size=th.FONT_CAPTION,
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
                logger.warning("AI 任务调用失败：%s", type(exc).__name__)
                text, drafts = f"⚠️ {exc}", []
            except Exception as exc:  # 网络/解析异常都要回到 UI，避免弹窗一直转圈
                logger.exception("AI 任务执行失败")
                # 与 ai_assistant 一致：不回显原始异常文本（HTTP 异常的 str 可能带
                # 完整请求 URL 与签名参数）。
                text, drafts = f"⚠️ 出错了（{type(exc).__name__}），详见日志", []

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


def _preview_value(name: str, value) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    # 只把正文类字段折成一行短摘要，其它字段原样展示：用户确认的就是落库的内容，
    # 以前整行截断到 200 字，长正文会被砍掉一半还不知道砍了什么。
    if name in ("content", "note") and len(text) > 80:
        return text[:80] + "…"
    return text


def _detail_text(draft) -> str:
    """草稿字段摘要（含标签；正文类字段折行截短）。"""
    parts: list[str] = []
    for name, value in draft.fields.items():
        if value in (None, "", "[]", False):
            continue
        parts.append(f"{name}={_preview_value(name, value)}")
    return " · ".join(parts)


def open_draft_dialog(page: ft.Page, drafts: list, on_saved=None) -> ft.AlertDialog:
    """草稿预览：逐条展示字段，确认后一次性保存并支持撤销。"""
    rows: list[ft.Control] = []
    for draft in drafts:
        detail = _detail_text(draft)
        rows.append(
            ft.Container(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    ft.Text(draft.label, size=th.FONT_CAPTION, color=th.PRIMARY),
                                    bgcolor=th.PRIMARY_LIGHT,
                                    border_radius=6,
                                    padding=ft.Padding.symmetric(vertical=2, horizontal=6),
                                ),
                                ft.Text(draft.title, size=th.FONT_CARD_TITLE, color=th.TEXT,
                                        weight="bold", expand=True, max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(
                                    "可保存" if draft.valid else draft.reason,
                                    size=th.FONT_CAPTION,
                                    color=th.PRIMARY if draft.valid else th.DANGER,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Text(detail, size=th.FONT_CAPTION,
                                color=th.TEXT_MUTED, max_lines=3,
                                tooltip=detail or None,
                                overflow=ft.TextOverflow.ELLIPSIS),
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
            ft.Text(f"已保存 {len(saved)} 条记录", color=th.ON_PRIMARY, size=th.FONT_BODY),
            bgcolor=th.PRIMARY,
            action="撤销",
            on_action=lambda e: _undo(saved),
            # 默认 4 秒对「刚保存完想反悔」太短：给足时间再自动消失。
            duration=15_000,
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
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                ),
                ft.Column(rows, spacing=6, scroll=ft.ScrollMode.AUTO, height=340),
            ],
            spacing=8,
            tight=True,
        ),
        [
            ft.TextButton("取消", on_click=lambda e: close_modal(page, dialog)),
            primary_button("保存到本地", save, "SAVE_OUTLINED"),
        ],
        width=640,
    )
    return dialog
