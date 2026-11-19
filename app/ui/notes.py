"""笔记：Markdown 源码/预览分栏、代码高亮与标签聚合。"""

from __future__ import annotations

import json

import flet as ft
from sqlalchemy import select

from ..db.models import Note
from ..db.repo import create, update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.markdown import highlight_lines, inline_spans, parse
from ..services.sync import mark_pending_write
from ..theme import DIVIDER, PRIMARY, PRIMARY_LIGHT, TEXT, TEXT_MUTED
from .forms import tags_text
from .widgets import card, divider, empty, primary_button, section_header, tag_chip

_state = {"tag_path": "全部", "expanded": set()}
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


def _table(header: list[str], rows: list[list[str]]) -> ft.Control:
    table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(h, size=12, weight="bold", color=TEXT)) for h in header],
        rows=[
            ft.DataRow(cells=[ft.DataCell(ft.Text(c, size=12, color=TEXT)) for c in row])
            for row in rows
        ],
        border=ft.Border.all(1, DIVIDER),
        border_radius=8,
        heading_row_color="#F4F6F3",
    )
    return ft.Row([table], scroll=ft.ScrollMode.AUTO)


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
        elif kind == "table":
            controls.append(_table(block["header"], block["rows"]))
        else:
            controls.append(ft.Text(spans=inline_spans(block["text"]), size=13))
    return ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)


def save_content(note_id: str, value: str) -> None:
    with session_scope() as session:
        note = session.get(Note, note_id)
        if note:
            update(session, note, content=value)
    mark_pending_write()


def save_title(note_id: str, value: str) -> None:
    with session_scope() as session:
        note = session.get(Note, note_id)
        if note:
            update(session, note, title=value)
    mark_pending_write()


def save_tags(note_id: str, value: str) -> None:
    parts = [p.strip() for p in (value or "").replace("，", ",").split(",") if p.strip()]
    with session_scope() as session:
        note = session.get(Note, note_id)
        if note:
            update(session, note, tags=json.dumps(parts, ensure_ascii=False))
    mark_pending_write()


def _parse_tags(raw: str) -> list[str]:
    """反序列化 tags JSON，返回路径标签列表。"""
    try:
        data = json.loads(raw or "[]")
        return [str(t).strip() for t in data if str(t).strip()]
    except (ValueError, TypeError):
        return []


def _collect_tag_paths(notes) -> list[str]:
    """收集所有笔记的路径标签（去重、排序）。"""
    seen: set[str] = set()
    for note in notes:
        seen.update(_parse_tags(note.tags))
    return sorted(seen)


def _build_tag_tree(tags: list[str]) -> dict:
    """由路径标签构建嵌套树，如 {'技术': {'编程': {'Python': {}}}}。"""
    tree: dict = {}
    for tag in tags:
        parts = [p for p in tag.split("/") if p]
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    return tree


def _matches_path(note_tags: list[str], path: str) -> bool:
    """方式 A：标签等于 path 或以其为前缀（path/）。"""
    for tag in note_tags:
        if tag == path or tag.startswith(path + "/"):
            return True
    return False


def build_preview(page: ft.Page, note: Note, on_back) -> ft.Control:
    global _preview
    title_field = ft.TextField(
        value=note.title,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=TEXT,
        text_style=ft.TextStyle(size=16, color=TEXT, weight="bold"),
        on_blur=lambda e: save_title(note.id, e.control.value or ""),
    )
    with session_scope() as session:
        all_tag_paths = _collect_tag_paths(list(session.scalars(select(Note).where(Note.is_deleted.is_(False)))))
    tags_field = ft.TextField(
        value=tags_text(note.tags),
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=TEXT_MUTED,
        text_style=ft.TextStyle(size=11, color=TEXT_MUTED),
        hint_text="标签（逗号分隔，路径用 /）",
        on_change=lambda e: _on_tags_change(e.control.value or ""),
        on_blur=lambda e: save_tags(note.id, e.control.value or ""),
    )
    suggestion_row = ft.Row([], wrap=True, spacing=6)

    def _on_tags_change(value: str) -> None:
        if "," in value:
            _, _, prefix = value.rpartition(",")
            prefix = prefix.strip()
        else:
            prefix = value.strip()
        if not prefix:
            suggestion_row.controls = []
        else:
            candidates = [t for t in all_tag_paths if t.startswith(prefix) and t != prefix]
            suggestion_row.controls = [
                tag_chip(c, False, lambda e, c=c: _apply_suggestion(c))
                for c in candidates[:8]
            ]
        suggestion_row.visible = bool(suggestion_row.controls)
        suggestion_row.update()
        page.update()

    def _apply_suggestion(suggestion: str) -> None:
        value = tags_field.value or ""
        if "," in value:
            base = value.rsplit(",", 1)[0].rstrip() + ","
        else:
            base = ""
        tags_field.value = (base + " " + suggestion).strip()
        tags_field.update()
        suggestion_row.controls = []
        suggestion_row.visible = False
        suggestion_row.update()
        page.update()
    _preview = ft.Container(content=render_markdown(note.content), expand=True)
    source = ft.TextField(
        value=note.content,
        multiline=True,
        expand=True,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=TEXT,
        text_style=ft.TextStyle(size=12, color=TEXT, font_family="Consolas", height=1.5),
        hint_text="输入 Markdown，右侧实时预览；代码块使用 ```python 语法",
        on_change=lambda e: _update_preview(e.control.value or ""),
        on_blur=lambda e: save_content(note.id, e.control.value or ""),
    )

    def _update_preview(value: str) -> None:
        if _preview is not None:
            _preview.content = render_markdown(value)
            page.update()

    return ft.Column(
        [
            section_header("编辑笔记", ft.TextButton("返回", on_click=on_back)),
            card(
                ft.Column(
                    [
                        title_field,
                        ft.Column([tags_field, suggestion_row], spacing=6),
                        divider(),
                        ft.Row(
                            [
                                card(ft.Column([source], spacing=10, expand=True), title="源码", expand=1),
                                card(_preview, title="实时预览", expand=1),
                            ],
                            spacing=14,
                            expand=True,
                        ),
                    ],
                    spacing=8,
                    expand=True,
                ),
                expand=True,
            ),
        ],
        expand=True,
    )


def build(page: ft.Page, refresh, on_open) -> ft.Control:
    with session_scope() as session:
        notes = list(session.scalars(select(Note).where(Note.is_deleted.is_(False)).order_by(Note.id.desc())))
    tag_paths = _collect_tag_paths(notes)
    tree = _build_tag_tree(tag_paths)

    def add_note(_):
        with session_scope() as session:
            note = create(session, Note, title="未命名笔记", content="", tags="[]")
            note_id = note.id
        with session_scope() as session:
            new_note = session.get(Note, note_id)
        if new_note:
            on_open(new_note)

    def select_path(path: str) -> None:
        _state["tag_path"] = path
        refresh()

    def toggle_expand(path: str) -> None:
        if path in _state["expanded"]:
            _state["expanded"].discard(path)
        else:
            _state["expanded"].add(path)
        refresh()

    def render_tree(node: dict, parent_path: str, depth: int) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for name in sorted(node.keys()):
            path = f"{parent_path}/{name}" if parent_path else name
            children = node[name]
            has_children = bool(children)
            expanded = path in _state["expanded"]
            active = _state["tag_path"] == path
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(width=depth * 14),
                            ft.Container(
                                content=ft.Text("▾" if expanded else "▸", size=11, color=TEXT_MUTED),
                                width=18,
                                alignment=ft.Alignment.CENTER,
                                on_click=lambda e, p=path: toggle_expand(p),
                                visible=has_children,
                            ),
                            ft.Text(name, size=12, color=PRIMARY if active else TEXT,
                                    weight="bold" if active else "normal"),
                        ],
                        spacing=2,
                    ),
                    padding=ft.Padding.symmetric(vertical=4, horizontal=6),
                    bgcolor=PRIMARY_LIGHT if active else None,
                    border_radius=6,
                    on_click=lambda e, p=path: select_path(p),
                )
            )
            if has_children and expanded:
                controls.extend(render_tree(children, path, depth + 1))
        return controls

    all_active = _state["tag_path"] == "全部"
    tag_controls = [
        ft.Container(
            content=ft.Row(
                [ft.Text("全部", size=12, color=PRIMARY if all_active else TEXT,
                         weight="bold" if all_active else "normal")],
                spacing=2,
            ),
            padding=ft.Padding.symmetric(vertical=4, horizontal=6),
            bgcolor=PRIMARY_LIGHT if all_active else None,
            border_radius=6,
            on_click=lambda e: select_path("全部"),
        ),
        *render_tree(tree, "", 0),
    ]

    filtered = [
        n for n in notes
        if _state["tag_path"] == "全部" or _matches_path(_parse_tags(n.tags), _state["tag_path"])
    ]

    rows = [
        ft.ListTile(
            title=ft.Text(n.title, size=13, color=TEXT, weight="bold"),
            subtitle=ft.Text(
                (n.content or "").replace("\n", " ")[:60] or "（暂无正文）",
                size=11, color=TEXT_MUTED, overflow=ft.TextOverflow.ELLIPSIS,
            ),
            on_click=lambda e, nn=n: on_open(nn),
        )
        for n in filtered
    ]

    return ft.Column(
        [
            section_header("笔记", primary_button("新增笔记", add_note, ACTIONS["add"])),
            ft.Row(
                [
                    card(
                        ft.Column(tag_controls, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),
                        title="标签",
                        width=220,
                    ),
                    card(
                        ft.Column(rows, spacing=2, scroll=ft.ScrollMode.AUTO, expand=True) if rows else empty("暂无笔记"),
                        expand=True,
                    ),
                ],
                spacing=12,
                expand=True,
            ),
        ],
        spacing=12,
        expand=True,
    )
