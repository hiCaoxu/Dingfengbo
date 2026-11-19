"""笔记：Markdown 源码/预览分栏、代码高亮与标签聚合。"""

from __future__ import annotations

import flet as ft
from sqlalchemy import select

from ..db.models import Note
from ..db.repo import update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.markdown import highlight_lines, inline_spans, parse
from ..services.sync import mark_pending_write
from ..theme import DIVIDER, PRIMARY, TEXT, TEXT_MUTED
from .forms import open_note_dialog, tags_text
from .widgets import card, divider, empty, primary_button, section_header, tag_chip

_state = {"selected": None, "tag": "全部"}
_preview: ft.Container | None = None


def _code_block(code: str, lang: str) -> ft.Control:
    lines = highlight_lines(code, lang)
    rows = []
    for index, spans in enumerate(lines, 1):
        rows.append(
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(str(index), size=11, color=TEXT_MUTED, font_family="Consolas"),
                        width=30,
                        alignment=ft.Alignment.CENTER_RIGHT,
                    ),
                    ft.Text(spans=spans, size=12, selectable=True),
                ],
                spacing=8,
            )
        )
    return ft.Container(
        content=ft.Column(rows, spacing=0, scroll=ft.ScrollMode.AUTO),
        bgcolor="#F7F8F6",
        border=ft.Border.all(1, DIVIDER),
        border_radius=8,
        padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )


def render_markdown(markdown: str) -> ft.Control:
    blocks = parse(markdown or "")
    controls: list[ft.Control] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            size = {1: 20, 2: 17, 3: 15}.get(block["level"], 13)
            controls.append(ft.Text(block["text"], size=size, color=TEXT, weight="bold"))
        elif kind == "code":
            controls.append(_code_block(block["code"], block["lang"]))
        elif kind == "hr":
            controls.append(divider())
        elif kind == "quote":
            controls.append(
                ft.Container(
                    content=ft.Text(block["text"], size=12, color=TEXT_MUTED, italic=True),
                    border=ft.Border.only(left=ft.BorderSide(3, PRIMARY)),
                    padding=ft.Padding.only(left=10),
                )
            )
        elif kind == "list":
            controls.append(
                ft.Column(
                    [ft.Row([ft.Text("•", size=12, color=PRIMARY), ft.Text(spans=inline_spans(t))], spacing=6) for t in block["items"]],
                    spacing=4,
                )
            )
        else:
            controls.append(ft.Text(spans=inline_spans(block["text"]), size=13))
    return ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)


def save_content(note_id: str, value: str) -> None:
    with session_scope() as session:
        note = session.get(Note, note_id)
        if note:
            update(session, note, content=value)
    mark_pending_write()


def build_preview(note: Note, on_back) -> ft.Control:
    return ft.Column(
        [
            section_header("笔记预览", ft.TextButton("返回", on_click=on_back)),
            card(
                ft.Column(
                    [
                        ft.Text(note.title, size=18, color=TEXT, weight="bold"),
                        ft.Text(f"标签：{tags_text(note.tags) or '无'}", size=11, color=TEXT_MUTED),
                        divider(),
                        ft.Container(content=render_markdown(note.content), expand=True, height=520),
                    ],
                    spacing=12,
                ),
                expand=True,
            ),
        ],
        expand=True,
    )


def build(page: ft.Page, refresh) -> ft.Control:
    global _preview
    with session_scope() as session:
        notes = list(session.scalars(select(Note).where(Note.is_deleted.is_(False)).order_by(Note.id.desc())))
    all_tags: set[str] = set()
    for note in notes:
        all_tags.update(t for t in tags_text(note.tags).split(", ") if t)
    filtered = [n for n in notes if _state["tag"] == "全部" or _state["tag"] in tags_text(n.tags).split(", ")]
    selected = next((n for n in filtered if n.id == _state["selected"]), filtered[0] if filtered else None)
    _state["selected"] = selected.id if selected else None

    selector = ft.Dropdown(
        label="选择笔记",
        value=str(selected.id) if selected else None,
        options=[ft.dropdown.Option(key=str(n.id), text=n.title) for n in filtered],
        width=260,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        on_select=lambda e: (_state.__setitem__("selected", int(e.control.value)), refresh()),
    )
    _preview = ft.Container(content=render_markdown(selected.content if selected else ""), expand=True)
    source = ft.TextField(
        value=selected.content if selected else "",
        multiline=True,
        expand=True,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=TEXT,
        text_style=ft.TextStyle(size=12, color=TEXT, font_family="Consolas", height=1.5),
        hint_text="输入 Markdown，右侧实时预览；代码块使用 ```python 语法",
        on_change=lambda e: _update_preview(e.control.value or ""),
        on_blur=lambda e: save_content(selected.id, e.control.value or "") if selected else None,
    )

    def _update_preview(value: str) -> None:
        if _preview is not None:
            _preview.content = render_markdown(value)
            page.update()

    tag_row = ft.Row(
        [tag_chip(t, _state["tag"] == t, lambda e, tag=t: (_state.__setitem__("tag", tag), refresh()))
         for t in ["全部", *sorted(all_tags)]],
        spacing=6,
        wrap=True,
    )

    if not selected:
        return ft.Column(
            [
                section_header(
                    "笔记",
                    primary_button(
                        "新增笔记", lambda e: open_note_dialog(page, None, refresh), ACTIONS["add"]
                    ),
                ),
                tag_row,
                card(empty("暂无笔记，点击右上角新增"), expand=True),
            ],
            spacing=12,
            expand=True,
        )

    return ft.Column(
        [
            section_header(
                "笔记",
                ft.Row([primary_button("新增笔记", lambda e: open_note_dialog(page, None, refresh), ACTIONS["add"]),
                        ft.TextButton("编辑元信息", on_click=lambda e: open_note_dialog(page, selected, refresh))], spacing=6),
            ),
            tag_row,
            ft.Row(
                [
                    card(
                        ft.Column([selector, source], spacing=10, expand=True),
                        title="源码",
                        expand=1,
                    ),
                    card(_preview, title="实时预览", expand=1),
                ],
                spacing=14,
                expand=True,
            ),
        ],
        spacing=12,
        expand=True,
    )
