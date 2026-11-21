"""笔记：Markdown 源码/预览分栏、代码高亮与标签聚合。"""

from __future__ import annotations

import json
import logging
import threading

import flet as ft
from sqlalchemy import func, select

from ..db.models import Note
from ..db.repo import create, update
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.markdown import highlight_lines, inline_spans, parse
from ..services.sync import mark_pending_write
from .. import theme as th
from .async_utils import background_section
from .forms import tags_text
from .state import state
from .widgets import (
    card,
    delete_with_confirm,
    divider,
    empty,
    icon_button,
    primary_button,
    section_header,
    tag_chip,
)

logger = logging.getLogger(__name__)

_state = state.notes
_preview: ft.Container | None = None

# 预览防抖：每次击键不再触发全文解析，停顿 400ms 后后台渲染。
_PREVIEW_DEBOUNCE_MS = 400
_PREVIEW_PLAINTEXT_THRESHOLD = 50_000
_preview_timer: threading.Timer | None = None
# 每次打开笔记自增；在途的旧渲染（timer 已触发、协程已入队）据此作废。
_preview_generation = 0

# 自动保存：编辑页原先只在 on_blur 落库，输入期间若页面被重建（同步完成会整页刷新）
# 或用 Ctrl+W 关闭窗口，未失焦的内容就会丢。这里按停顿自动落库。
_AUTOSAVE_DEBOUNCE_MS = 700
_autosave_timer: threading.Timer | None = None


def _cancel_preview_timer() -> None:
    global _preview_timer
    if _preview_timer is not None:
        _preview_timer.cancel()
        _preview_timer = None


def _cancel_autosave_timer() -> None:
    global _autosave_timer
    if _autosave_timer is not None:
        _autosave_timer.cancel()
        _autosave_timer = None


def _note_snapshot(note: Note) -> tuple[str, str, str, str]:
    """编辑页一律以数据库最新值为准（id/title/content/tags）。

    同步完成会触发整页刷新并重建编辑页；若用打开笔记时的对象快照渲染，
    重建就会把用户刚输入的内容覆盖回旧值——这是「写着写着内容消失」的根因。
    """
    with session_scope() as session:
        fresh = session.get(Note, note.id)
        if fresh is not None:
            return fresh.id, fresh.title, fresh.content, fresh.tags
    return note.id, note.title, note.content, note.tags


def _code_block(code: str, lang: str) -> ft.Control:
    lines = highlight_lines(code, lang)
    rows = []
    for index, spans in enumerate(lines, 1):
        rows.append(
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(str(index), size=11, color=th.TEXT_MUTED, font_family="Consolas"),
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
        border=ft.Border.all(1, th.DIVIDER),
        border_radius=8,
        padding=ft.Padding.symmetric(vertical=8, horizontal=10),
    )


def _table(header: list[str], rows: list[list[str]]) -> ft.Control:
    table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(h, size=12, weight="bold", color=th.TEXT)) for h in header],
        rows=[
            ft.DataRow(cells=[ft.DataCell(ft.Text(c, size=12, color=th.TEXT)) for c in row])
            for row in rows
        ],
        border=ft.Border.all(1, th.DIVIDER),
        border_radius=8,
        heading_row_color="#F4F6F3",
    )
    return ft.Row([table], scroll=ft.ScrollMode.AUTO)


def render_blocks(blocks: list[dict]) -> ft.Control:
    """把 parse 得到的块列表构建为 Flet 控件树（需在 UI 线程调用）。"""
    controls: list[ft.Control] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            size = {1: 20, 2: 17, 3: 15}.get(block["level"], 13)
            controls.append(ft.Text(block["text"], size=size, color=th.TEXT, weight="bold"))
        elif kind == "code":
            controls.append(_code_block(block["code"], block["lang"]))
        elif kind == "hr":
            controls.append(divider())
        elif kind == "quote":
            controls.append(
                ft.Container(
                    content=ft.Text(block["text"], size=12, color=th.TEXT_MUTED, italic=True),
                    border=ft.Border.only(left=ft.BorderSide(3, th.PRIMARY)),
                    padding=ft.Padding.only(left=10),
                )
            )
        elif kind == "list":
            controls.append(
                ft.Column(
                    [ft.Row([ft.Text("•", size=12, color=th.PRIMARY), ft.Text(spans=inline_spans(t))], spacing=6) for t in block["items"]],
                    spacing=4,
                )
            )
        elif kind == "table":
            controls.append(_table(block["header"], block["rows"]))
        else:
            controls.append(ft.Text(spans=inline_spans(block["text"]), size=13))
    return ft.Column(controls, spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)


def render_markdown(markdown: str) -> ft.Control:
    return render_blocks(parse(markdown or ""))


def _save_fields(note_id: str, **fields) -> bool:
    """只在值真的变化时写库并标脏，避免自动保存反复推高 updated_at、产生无意义同步。"""
    with session_scope() as session:
        note = session.get(Note, note_id)
        if note is None:
            return False
        if all(getattr(note, key) == value for key, value in fields.items()):
            return False
        update(session, note, **fields)
    mark_pending_write()
    return True


def save_content(note_id: str, value: str) -> None:
    _save_fields(note_id, content=value)


def save_title(note_id: str, value: str) -> None:
    _save_fields(note_id, title=value)


def save_tags(note_id: str, value: str) -> None:
    parts = [p.strip() for p in (value or "").replace("，", ",").split(",") if p.strip()]
    _save_fields(note_id, tags=json.dumps(parts, ensure_ascii=False))


def _parse_tags(raw: str) -> list[str]:
    """反序列化 tags JSON，返回路径标签列表。"""
    try:
        data = json.loads(raw or "[]")
        return [str(t).strip() for t in data if str(t).strip()]
    except (ValueError, TypeError):
        return []


def _collect_tag_paths(tag_values) -> list[str]:
    """收集路径标签（去重、排序）；入参为 tags 原始 JSON 字符串序列。"""
    seen: set[str] = set()
    for raw in tag_values:
        seen.update(_parse_tags(raw))
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
    global _preview, _preview_generation
    _preview_generation += 1
    generation = _preview_generation
    # 切换笔记时立即作废上一篇的在途渲染，否则旧正文会被写进新笔记的预览区。
    _cancel_preview_timer()
    _cancel_autosave_timer()
    note_id, title_text, content_text, tags_raw = _note_snapshot(note)

    def _autosave() -> None:
        """一次把三个字段都落库：单字段定时器会互相取消，最后一个赢，前面的改动就丢了。"""
        save_title(note_id, title_field.value or "")
        save_tags(note_id, tags_field.value or "")
        save_content(note_id, source.value or "")

    def _schedule_save() -> None:
        global _autosave_timer
        _cancel_autosave_timer()
        _autosave_timer = threading.Timer(_AUTOSAVE_DEBOUNCE_MS / 1000, _autosave)
        _autosave_timer.daemon = True
        _autosave_timer.start()

    title_field = ft.TextField(
        value=title_text,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=16, color=th.TEXT, weight="bold"),
        on_change=lambda e: _schedule_save(),
        on_blur=lambda e: save_title(note_id, e.control.value or ""),
    )
    with session_scope() as session:
        # 只为标签补全取 tags 列，避免把全部正文搬进内存。
        all_tag_paths = _collect_tag_paths(
            session.scalars(select(Note.tags).where(Note.is_deleted.is_(False))).all()
        )
    tags_field = ft.TextField(
        value=tags_text(tags_raw),
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=th.TEXT_MUTED,
        text_style=ft.TextStyle(size=11, color=th.TEXT_MUTED),
        hint_text="标签（逗号分隔，路径用 /）",
        on_change=lambda e: (
            _schedule_save(),
            _on_tags_change(e.control.value or ""),
        ),
        on_blur=lambda e: save_tags(note_id, e.control.value or ""),
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
        # Flet 里直接赋 value 不会触发 on_change，这里补一次落库。
        _schedule_save()
        suggestion_row.controls = []
        suggestion_row.visible = False
        suggestion_row.update()
        page.update()
    _preview = ft.Container(content=render_markdown(content_text), expand=True)
    source = ft.TextField(
        value=content_text,
        multiline=True,
        expand=True,
        border=ft.InputBorder.NONE,
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=12, color=th.TEXT, font_family="Consolas", height=1.5),
        hint_text="输入 Markdown，右侧实时预览；代码块使用 ```python 语法",
        on_change=lambda e: (
            _schedule_save(),
            _update_preview(e.control.value or ""),
        ),
        on_blur=lambda e: save_content(note_id, e.control.value or ""),
    )

    def _render_preview(value: str, generation: int) -> None:
        text = value or ""
        try:
            if len(text) > _PREVIEW_PLAINTEXT_THRESHOLD:
                blocks = [
                    {"type": "text", "text": "（文档过大，预览已降级为纯文本）"},
                    {"type": "code", "lang": "", "code": text},
                ]
            else:
                blocks = parse(text)  # CPU 密集的解析/高亮放后台线程
        except Exception:
            logger.exception("Markdown 解析失败，保留上一次预览")
            return

        async def apply() -> None:
            try:
                # 已经切换到别的笔记：丢弃过期结果，不覆盖当前预览。
                if generation != _preview_generation:
                    return
                if _preview is not None:
                    _preview.content = render_blocks(blocks)
                    page.update()
            except Exception:
                # 页面已销毁（切换模块/关窗）时静默放弃，与 scheduler 的处理保持一致。
                logger.debug("预览刷新失败，页面可能已销毁", exc_info=True)

        try:
            page.run_task(apply)
        except Exception:
            logger.debug("预览任务调度失败，页面可能已销毁", exc_info=True)

    def _update_preview(value: str) -> None:
        global _preview_timer
        _cancel_preview_timer()
        _preview_timer = threading.Timer(
            _PREVIEW_DEBOUNCE_MS / 1000, lambda: _render_preview(value, generation)
        )
        _preview_timer.daemon = True
        _preview_timer.start()

    def go_back(e) -> None:
        # 返回前先落库：on_blur 在控件被替换时不一定触发，别指望它。
        _cancel_autosave_timer()
        save_title(note_id, title_field.value or "")
        save_tags(note_id, tags_field.value or "")
        save_content(note_id, source.value or "")
        on_back(e)

    return ft.Column(
        [
            section_header("编辑笔记", ft.TextButton("返回", on_click=go_back)),
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
    def load():
        # 列表只需 id/标题/标签与正文前 80 字，不加载整篇 content。
        with session_scope() as session:
            return session.execute(
                select(Note.id, Note.title, Note.tags, func.substr(Note.content, 1, 80))
                .where(Note.is_deleted.is_(False))
                .order_by(Note.id.desc())
            ).all()

    return background_section(page, load, lambda records: _render(page, refresh, on_open, records))


def _render(page: ft.Page, refresh, on_open, records) -> ft.Control:
    tag_paths = _collect_tag_paths([tags for _id, _title, tags, _snippet in records])
    tree = _build_tag_tree(tag_paths)

    def open_note(note_id: str) -> None:
        with session_scope() as session:
            note = session.get(Note, note_id)
        if note is not None:
            on_open(note)

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
                                content=ft.Text("▾" if expanded else "▸", size=11, color=th.TEXT_MUTED),
                                width=18,
                                alignment=ft.Alignment.CENTER,
                                on_click=lambda e, p=path: toggle_expand(p),
                                visible=has_children,
                            ),
                            ft.Text(name, size=12, color=th.PRIMARY if active else th.TEXT,
                                    weight="bold" if active else "normal"),
                        ],
                        spacing=2,
                    ),
                    padding=ft.Padding.symmetric(vertical=4, horizontal=6),
                    bgcolor=th.PRIMARY_LIGHT if active else None,
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
                [ft.Text("全部", size=12, color=th.PRIMARY if all_active else th.TEXT,
                         weight="bold" if all_active else "normal")],
                spacing=2,
            ),
            padding=ft.Padding.symmetric(vertical=4, horizontal=6),
            bgcolor=th.PRIMARY_LIGHT if all_active else None,
            border_radius=6,
            on_click=lambda e: select_path("全部"),
        ),
        *render_tree(tree, "", 0),
    ]

    filtered = [
        record for record in records
        if _state["tag_path"] == "全部" or _matches_path(_parse_tags(record[2]), _state["tag_path"])
    ]

    def delete_note(note_id: str) -> None:
        delete_with_confirm(page, Note, note_id, refresh, "确定删除该笔记？")

    rows = [
        ft.ListTile(
            title=ft.Text(title, size=13, color=th.TEXT, weight="bold"),
            subtitle=ft.Text(
                (snippet or "").replace("\n", " ")[:60] or "（暂无正文）",
                size=11, color=th.TEXT_MUTED, overflow=ft.TextOverflow.ELLIPSIS,
            ),
            trailing=icon_button(
                ACTIONS["delete"], lambda e, i=note_id: delete_note(i), "删除"
            ),
            on_click=lambda e, note_id=note_id: open_note(note_id),
        )
        for note_id, title, _tags, snippet in filtered
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
