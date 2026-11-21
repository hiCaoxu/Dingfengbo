"""AI 助手：表引用（@表名）+ 自由对话 + 结果引用/撤销。"""

from __future__ import annotations

import logging

import flet as ft

from ..config import AI_PROVIDERS, load_settings, save_settings
from ..icons import ico
from ..services.ai.client import AIConfigError, ai_configured, chat
from ..services.ai.context import CONTEXT_LIMIT, TABLES, get_label, parse_refs, read_context
from ..services.ai.provider import AIError
from ..theme import ACCENT, CARD, DIVIDER, PRIMARY, PRIMARY_LIGHT, TEXT, TEXT_MUTED, card_border
from .forms import open_settings_dialog
from .state import state
from .widgets import primary_button, snack

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是「定风波」个人工作台的 AI 助手。用户可能通过「@表名」引用待办、日记、笔记、锻炼、饮食、体重、睡眠、"
    "娱乐、账户、收支等数据，这些数据会以「【表名】」块的形式附加在用户消息中。请结合这些数据回答用户问题，"
    "给出务实、简洁、可执行的建议。你只输出文本建议，不要声称已修改或写入任何数据。使用简体中文。"
)

# 会话历史上限：超长会话会持续占用内存并逐步撑大每轮请求。
_MAX_MESSAGES = 100


def _user_bubble(text: str) -> ft.Control:
    return ft.Row(
        [
            ft.Container(expand=True),
            ft.Container(
                ft.Text(text, size=13, color="#FFFFFF"),
                bgcolor=PRIMARY,
                border_radius=12,
                padding=ft.Padding.symmetric(vertical=10, horizontal=14),
                width=460,
            ),
        ],
        spacing=8,
    )


def _ai_bubble(text: str, on_quote, on_undo) -> ft.Control:
    return ft.Row(
        [
            ft.Container(
                ft.Column(
                    [
                        ft.Text(text, size=13, color=TEXT, selectable=True),
                        ft.Row(
                            [
                                ft.TextButton("引用", on_click=on_quote),
                                ft.TextButton("撤销", on_click=on_undo),
                            ],
                            spacing=0,
                        ),
                    ],
                    spacing=4,
                ),
                bgcolor=CARD,
                border=card_border(),
                border_radius=12,
                padding=ft.Padding.all(14),
                width=620,
            ),
            ft.Container(expand=True),
        ],
        spacing=8,
    )


def _thinking_bubble() -> ft.Control:
    return ft.Row(
        [
            ft.Container(
                ft.Text("正在思考…", size=12, color=TEXT_MUTED, italic=True),
                bgcolor=CARD,
                border=card_border(),
                border_radius=12,
                padding=ft.Padding.all(12),
            ),
            ft.Container(expand=True),
        ],
        spacing=8,
    )


def _welcome() -> ft.Control:
    return ft.Row(
        [
            ft.Container(
                ft.Column(
                    [
                        ft.Text("你好，我是 AI 助手", size=13, color=TEXT, weight="bold"),
                        ft.Text(
                            "点击输入框上方的快捷指令可引用对应数据表（如「@待办」），再输入你的具体要求，"
                            "我会结合引用数据为你分析、整理或给出建议。",
                            size=12, color=TEXT_MUTED,
                        ),
                    ],
                    spacing=6,
                ),
                bgcolor=CARD,
                border=card_border(),
                border_radius=12,
                padding=ft.Padding.all(14),
                width=620,
            ),
            ft.Container(expand=True),
        ],
        spacing=8,
    )


def build(page: ft.Page, refresh) -> ft.Control:
    settings = load_settings()
    configured = ai_configured(settings)
    model_name = (settings.get("ai_model") or "").strip()

    messages_column = ft.Column(spacing=10, expand=True, scroll=ft.ScrollMode.AUTO)

    def fill() -> None:
        controls: list[ft.Control] = []
        for index, m in enumerate(state.ai.messages):
            if m["role"] == "user":
                controls.append(_user_bubble(m["text"]))
            else:
                controls.append(_ai_bubble(m["text"], lambda e, i=index: quote(i), lambda e, i=index: undo(i)))
        if state.ai.busy:
            controls.append(_thinking_bubble())
        if not controls:
            controls.append(_welcome())
        messages_column.controls = controls

    def render() -> None:
        fill()
        page.update()

    def append(role: str, text: str) -> None:
        state.ai.messages.append({"role": role, "text": text})
        overflow = len(state.ai.messages) - _MAX_MESSAGES
        if overflow > 0:
            del state.ai.messages[:overflow]
        render()

    def quote(index: int) -> None:
        if 0 <= index < len(state.ai.messages):
            result = state.ai.messages[index]["text"]
            current = input_field.value or ""
            input_field.value = (current + "\n" + result).strip() if current else result
            input_field.update()
            input_field.focus()

    def undo(index: int) -> None:
        if not (0 <= index < len(state.ai.messages)):
            return
        del state.ai.messages[index]
        if 0 < index < len(state.ai.messages) + 1 and state.ai.messages[index - 1]["role"] == "user":
            del state.ai.messages[index - 1]
        render()

    def run_worker(work) -> None:
        if state.ai.busy:
            return
        state.ai.busy = True
        render()

        def worker() -> None:
            try:
                text = work()
            except AIConfigError as exc:
                text = f"⚠️ {exc}"
            except AIError as exc:
                text = f"⚠️ {exc}"
            except Exception as exc:
                logger.exception("AI 调用异常")
                text = f"⚠️ 出错了：{exc}"

            async def apply() -> None:
                state.ai.busy = False
                append("assistant", text)

            page.run_task(apply)

        page.run_thread(worker)

    def _submit(text: str, refs: list[str]) -> None:
        input_field.value = ""
        input_field.update()
        append("user", text)
        history = [
            {"role": m["role"], "content": m["text"]}
            for m in state.ai.messages[-8:-1]
        ]

        def work() -> str:
            user_content = text
            if refs:
                context = read_context(refs)
                user_content = f"用户引用了以下数据：\n{context}\n\n用户要求：{text}"
            return chat([{"role": "system", "content": _SYSTEM}, *history, {"role": "user", "content": user_content}])

        run_worker(work)

    def _ask_consent(refs: list[str], on_agree) -> None:
        """首次把私密数据发往第三方大模型前，明确告知接收方与数据范围。"""
        settings = load_settings()
        provider = AI_PROVIDERS.get(settings.get("ai_provider", "custom"), {}).get(
            "name", "自定义服务"
        )
        model = (settings.get("ai_model") or "").strip() or "未指定模型"
        tables = "、".join(f"@{get_label(name)}" for name in refs)

        def close(_):
            dialog.open = False
            page.update()

        def agree(_):
            save_settings({"ai_data_notice_ack": True})
            close(_)
            on_agree()

        dialog = ft.AlertDialog(
            title=ft.Text("数据将发送至第三方大模型", size=14, color=TEXT, weight="bold"),
            content=ft.Text(
                f"本次提问引用了 {tables}，其最近 {CONTEXT_LIMIT} 条记录会通过网络发送至 "
                f"{provider}（{model}）。若不希望这些数据离开本机，请取消并移除 @引用。"
                "该提示仅出现一次，之后可在设置页查看隐私说明。",
                size=12,
                color=TEXT_MUTED,
            ),
            actions=[
                ft.TextButton("取消", on_click=close),
                primary_button("同意并发送", agree),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    def send(_) -> None:
        text = (input_field.value or "").strip()
        if not text or state.ai.busy:
            return
        refs = parse_refs(text)
        if refs and not load_settings().get("ai_data_notice_ack"):
            _ask_consent(refs, lambda: _submit(text, refs))
            return
        _submit(text, refs)

    def insert_ref(label: str) -> None:
        token = f"@{label}"
        current = input_field.value or ""
        if token not in current:
            input_field.value = (current + " " + token).strip()
        input_field.update()
        input_field.focus()

    input_field = ft.TextField(
        hint_text="输入你的要求，或点击上方表格快捷指令插入 @引用…",
        multiline=True,
        min_lines=2,
        max_lines=4,
        shift_enter=True,
        border_color=DIVIDER,
        focused_border_color=PRIMARY,
        color=TEXT,
        text_style=ft.TextStyle(size=13, color=TEXT),
        expand=True,
    )
    input_field.on_submit = send

    send_btn = primary_button("发送", send, "SEND")
    quick_chips = ft.Row(
        [
            ft.Container(
                ft.Text(f"@{label}", size=11, color=PRIMARY),
                bgcolor=PRIMARY_LIGHT,
                border_radius=14,
                padding=ft.Padding.symmetric(vertical=6, horizontal=12),
                on_click=lambda e, lb=label: insert_ref(lb),
            )
            for _name, label, _model in TABLES
        ],
        spacing=8,
        wrap=True,
    )

    status_row = ft.Row(
        [
            ft.Icon(ico("AUTO_AWESOME_OUTLINED"), size=20, color=PRIMARY),
            ft.Text("AI 助手", size=15, color=TEXT, weight="bold"),
            ft.Container(width=6),
            ft.Text(f"模型：{model_name}" if model_name else "未配置模型", size=11, color=TEXT_MUTED),
            ft.Container(expand=True),
            ft.Text("已连接" if configured else "未配置 API Key", size=11,
                    color=PRIMARY if configured else ACCENT),
            ft.TextButton("去设置", on_click=lambda e: open_settings_dialog(page, refresh, refresh),
                          visible=not configured),
        ],
        spacing=8,
    )

    header = ft.Column(
        [
            status_row,
            ft.Container(height=1, bgcolor=DIVIDER),
        ],
        spacing=8,
    )

    fill()

    return ft.Column(
        [
            header,
            ft.Container(
                messages_column,
                expand=True,
                bgcolor="#FBFAF7",
                border=card_border(),
                border_radius=12,
                padding=ft.Padding.all(14),
            ),
            quick_chips,
            ft.Row([input_field, send_btn], spacing=8),
        ],
        spacing=10,
        expand=True,
    )
