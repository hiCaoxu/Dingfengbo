"""笔记：Markdown 源码、预览或并列，代码高亮与标签聚合。"""

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
from ..services.covers import is_safe_remote_url
from ..services.markdown import highlight_lines, inline_spans, parse
from ..services.sync import mark_pending_write
from .. import theme as th
from .async_utils import background_section
from . import autosave
from .forms import tags_text
from .state import state
from .uploads import upload_button
from .widgets import (
    card,
    delete_with_confirm,
    divider,
    empty,
    icon_button,
    page_header,
    primary_button,
    snack,
    tag_chip,
)

logger = logging.getLogger(__name__)

_state = state.notes
_preview: ft.Container | None = None
# 编辑页版式跨刷新保留：源码、预览，或左右并列。
_editor_view = "split"
EDITOR_VIEWS = ("source", "preview", "split")

# 预览防抖：每次击键不再触发全文解析，停顿 400ms 后后台渲染。
_PREVIEW_DEBOUNCE_MS = 400
_PREVIEW_PLAINTEXT_THRESHOLD = 50_000
_preview_timer: threading.Timer | None = None
# 每次打开笔记自增；在途的旧渲染（timer 已触发、协程已入队）据此作废。
_preview_generation = 0

# 自动保存：编辑页原先只在 on_blur 落库，输入期间若页面被重建（同步完成会整页刷新）
# 或用 Ctrl+W 关闭窗口，未失焦的内容就会丢。这里按停顿自动落库，
# 调度交给 autosave 状态机（版本化快照 + 串行写库，避免 Timer 与 UI 互相清 pending）。
_AUTOSAVE_DEBOUNCE_MS = 700


def _cancel_preview_timer() -> None:
    global _preview_timer
    if _preview_timer is not None:
        _preview_timer.cancel()
        _preview_timer = None


def _flush_autosave() -> None:
    """立即落库待保存的编辑（切页 / 整页重建前调用）。"""
    autosave.flush_now()


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


def visible_editor_panes(mode: str) -> tuple[bool, bool]:
    """返回 (显示源码, 显示预览)。未知模式按并列处理。"""
    if mode == "source":
        return True, False
    if mode == "preview":
        return False, True
    return True, True


def _code_block(code: str, lang: str, page: ft.Page | None = None) -> ft.Control:
    """代码块按阅读排版：顶栏写语言并提供复制，正文不要行号和描边。"""
    lines = highlight_lines(code, lang)
    rows = [
        ft.Text(
            spans=spans or [ft.TextSpan(" ")],
            size=14,
            selectable=True,
            font_family="Consolas",
        )
        for spans in lines
    ] or [ft.Text(" ", size=14, font_family="Consolas")]

    text = code or ""
    # CopyToClipboard 在构造时就要拿到当前页面。预览有时在没有页面上下文的
    # 线程里刷新（测试、以及部分后台回写），这时退回到点击后再写入剪贴板。
    action = None
    try:
        action = ft.CopyToClipboard(text)
    except RuntimeError:
        action = None

    def copied(_event=None) -> None:
        if page is None:
            return
        if action is not None:
            snack(page, "已复制", th.PRIMARY)
            return

        clip = ft.Clipboard()

        async def write() -> None:
            try:
                await clip.set(text)
            except Exception:
                logger.exception("复制代码失败")
                return
            snack(page, "已复制", th.PRIMARY)

        try:
            page.run_task(write)
        except Exception:
            logger.debug("复制任务调度失败", exc_info=True)

    header = ft.Row(
        [
            ft.Text(lang or "纯文本", size=12, color=th.TEXT_MUTED, font_family="Consolas"),
            ft.Container(expand=True),
            ft.TextButton(
                "复制",
                data=text,
                action=action,
                on_click=copied,
                style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8, vertical=2)),
            ),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return ft.Container(
        content=ft.Column(
            [header, ft.Container(height=1, bgcolor=th.DIVIDER), ft.Column(rows, spacing=1)],
            spacing=8,
        ),
        bgcolor=th.SUBTLE,
        border_radius=12,
        padding=ft.Padding.symmetric(vertical=10, horizontal=16),
        data="md-code",
    )


def _normalize_table_rows(header: list[str], rows: list[list[str]]) -> list[list[str]]:
    """把每行补齐/截断到表头列数。

    Markdown 允许「少写几格」或「多写几格」，但 DataRow 的单元格数必须与列数一致，
    否则 Flet/Flutter 侧会断言失败（整块预览渲染不出来）。短行补空串、长行截断。
    """
    width = len(header)
    if width == 0:
        return []
    normalized: list[list[str]] = []
    for row in rows:
        cells = [str(cell) for cell in row[:width]]
        cells.extend([""] * (width - len(cells)))
        normalized.append(cells)
    return normalized


def _table(header: list[str], rows: list[list[str]]) -> ft.Control:
    """表格只留表头强调和行间细线，不使用带边框的数据表。"""
    safe_rows = _normalize_table_rows(header, rows)

    def cells(values: list[str], bold: bool = False) -> ft.Row:
        return ft.Row(
            [
                ft.Container(
                    content=ft.Text(
                        value, size=15, color=th.TEXT, weight="bold" if bold else None,
                        selectable=True,
                    ),
                    expand=True,
                    padding=ft.Padding.symmetric(vertical=8, horizontal=4),
                )
                for value in values
            ],
            spacing=8,
        )

    body: list[ft.Control] = [
        cells(header, bold=True),
        ft.Container(height=1, bgcolor=th.DIVIDER),
    ]
    for row in safe_rows:
        body.append(cells(row))
        body.append(ft.Container(height=1, bgcolor=th.DIVIDER))
    return ft.Column(body, spacing=0, data="md-table")


def render_blocks(blocks: list[dict], page: ft.Page | None = None) -> ft.Control:
    """把 parse 得到的块列表构建为 Flet 控件树（需在 UI 线程调用）。"""
    controls: list[ft.Control] = []
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            # 标题比正文大两级以上，段前留白，读起来像一篇笔记而不是一排标签。
            size = {1: 26, 2: 21, 3: 18}.get(block["level"], 16)
            controls.append(
                ft.Container(
                    content=ft.Text(
                        block["text"], size=size, color=th.TEXT, weight="bold", selectable=True,
                    ),
                    padding=ft.Padding.only(top=14 if controls else 0, bottom=2),
                )
            )
        elif kind == "code":
            controls.append(_code_block(block["code"], block["lang"], page))
        elif kind == "hr":
            controls.append(
                ft.Container(
                    height=1, bgcolor=th.DIVIDER,
                    margin=ft.Margin.symmetric(vertical=8),
                )
            )
        elif kind == "quote":
            controls.append(
                ft.Container(
                    content=ft.Text(
                        spans=inline_spans(block["text"]),
                        size=15,
                        color=th.TEXT,
                        selectable=True,
                    ),
                    border=ft.Border.only(left=ft.BorderSide(3, th.PRIMARY)),
                    padding=ft.Padding.only(left=14, top=2, bottom=2),
                )
            )
        elif kind == "list":
            rows: list[ft.Control] = []
            for raw in block["items"]:
                text = raw
                marker: ft.Control
                if raw.startswith("[x] ") or raw.startswith("[X] "):
                    text = raw[4:]
                    marker = ft.Container(
                        width=14, height=14, border_radius=4, bgcolor=th.PRIMARY,
                        margin=ft.Margin.only(top=4),
                    )
                elif raw.startswith("[ ] "):
                    text = raw[4:]
                    marker = ft.Container(
                        width=14, height=14, border_radius=4,
                        border=ft.Border.all(1.4, th.TEXT_MUTED),
                        margin=ft.Margin.only(top=4),
                    )
                else:
                    marker = ft.Container(
                        width=6, height=6, border_radius=3, bgcolor=th.PRIMARY,
                        margin=ft.Margin.only(top=8),
                    )
                rows.append(
                    ft.Row(
                        [
                            marker,
                            ft.Text(
                                spans=inline_spans(text), size=15, selectable=True, expand=True,
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                )
            controls.append(ft.Column(rows, spacing=8))
        elif kind == "table":
            controls.append(_table(block["header"], block["rows"]))
        elif kind == "image":
            src = str(block.get("src") or "")
            # 图片行也要过与封面相同的 SSRF 过滤：笔记正文可能来自同步/AI，
            # 不过滤就会让应用替任意地址发起请求（内网探测、云元数据端点）。
            if not is_safe_remote_url(src):
                controls.append(
                    ft.Text(
                        f"（已拦截不安全的图片地址：{block.get('alt') or src[:60]}）",
                        size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                    )
                )
            else:
                controls.append(
                    ft.Image(
                        src=src,
                        fit=ft.BoxFit.CONTAIN,
                        border_radius=8,
                        error_content=ft.Text(
                            block.get("alt") or "图片加载失败", size=th.FONT_CAPTION,
                            color=th.TEXT_MUTED,
                        ),
                    )
                )
        else:
            controls.append(
                ft.Text(spans=inline_spans(block["text"]), size=15, selectable=True)
            )
    return ft.Column(
        controls,
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def render_markdown(markdown: str, page: ft.Page | None = None) -> ft.Control:
    return render_blocks(parse(markdown or ""), page)


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
    except (ValueError, TypeError) as exc:
        logger.debug("_parse_tags：忽略 %s", type(exc).__name__)
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
    # 先 flush 再重建：把「上一个编辑页里已输入但还没到写盘时间」的内容落库。
    _flush_autosave()
    note_id, title_text, content_text, tags_raw = _note_snapshot(note)

    def _write_note(key: str, snapshot: dict) -> None:
        """一次把三个字段都落库：单字段定时器会互相取消，最后一个赢，前面的改动就丢了。"""
        save_title(snapshot["id"], snapshot["title"])
        save_tags(snapshot["id"], snapshot["tags"])
        save_content(snapshot["id"], snapshot["content"])

    def _schedule_save() -> None:
        # 快照在 UI 线程里取：Timer 触发时控件可能已被整页重建替换掉。
        autosave.schedule(
            "note",
            {
                "id": note_id,
                "title": title_field.value or "",
                "tags": tags_field.value or "",
                "content": source.value or "",
            },
            _write_note,
            _AUTOSAVE_DEBOUNCE_MS,
        )

    title_field = ft.TextField(
        value=title_text,
        border=ft.NoInputBorder(),
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=16, color=th.TEXT, weight="bold"),
        on_change=lambda e: _schedule_save(),
        on_blur=lambda e: (_schedule_save(), _flush_autosave()),
    )
    with session_scope() as session:
        # 只为标签补全取 tags 列，避免把全部正文搬进内存。
        all_tag_paths = _collect_tag_paths(
            session.scalars(select(Note.tags).where(Note.is_deleted.is_(False))).all()
        )
    tags_field = ft.TextField(
        value=tags_text(tags_raw),
        border=ft.NoInputBorder(),
        border_width=0,
        filled=False,
        color=th.TEXT_MUTED,
        text_style=ft.TextStyle(size=th.FONT_BODY, color=th.TEXT_MUTED),
        hint_text="标签（逗号分隔，路径用 /）",
        hint_style=ft.TextStyle(size=th.FONT_BODY, color=th.TEXT_MUTED),
        on_change=lambda e: (
            _schedule_save(),
            _on_tags_change(e.control.value or ""),
        ),
        on_blur=lambda e: (_schedule_save(), _flush_autosave()),
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
        # 只刷新这一行和整页一次：suggestion_row.update() 在 Flet 老版本会顺带
        # 触发整页重绘，两次 page.update() 叠加会让每次击键抖两下。
        suggestion_row.update()

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
    def _safe_render(value: str) -> ft.Control:
        """首渲染兜底：解析/构建失败时退回纯文本并记日志，别让预览区（甚至整窗）挂掉。"""
        try:
            return render_markdown(value, page)
        except Exception:
            logger.exception("Markdown 预览渲染失败，退回纯文本")
            return ft.Container(
                content=ft.Text(
                    value or "（预览渲染失败，已显示原文）",
                    size=14, color=th.TEXT_MUTED, selectable=True,
                ),
                expand=True,
            )

    _preview = ft.Container(
        content=_safe_render(content_text),
        expand=True,
        padding=ft.Padding.symmetric(vertical=6, horizontal=8),
    )
    source = ft.TextField(
        value=content_text,
        multiline=True,
        expand=True,
        border=ft.NoInputBorder(),
        border_width=0,
        filled=False,
        color=th.TEXT,
        text_style=ft.TextStyle(size=14, color=th.TEXT, font_family="Consolas", height=1.65),
        hint_text="输入 Markdown；可切到预览或与源码并列。代码块使用 ```python 语法",
        on_change=lambda e: (
            _schedule_save(),
            _update_preview(e.control.value or ""),
        ),
        on_blur=lambda e: (_schedule_save(), _flush_autosave()),
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
                    _preview.content = render_blocks(blocks, page)
                    # 只刷预览区。整页 update 会把自定义标题栏的窗口闪一帧。
                    _preview.update()
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

    def insert_image(url: str) -> None:
        """把上传得到的服务器链接以 Markdown 图片语法追加到正文末尾。"""
        text = source.value or ""
        separator = "\n" if text and not text.endswith("\n") else ""
        source.value = f"{text}{separator}![]({url})\n"
        source.update()
        _schedule_save()
        _update_preview(source.value)

    def go_back(e) -> None:
        # 返回前先落库：on_blur 在控件被替换时不一定触发，别指望它。
        _schedule_save()
        _flush_autosave()
        on_back(e)

    source_card = card(ft.Column([source], spacing=10, expand=True), title="源码", expand=1)
    preview_card = card(_preview, title="实时预览", expand=1)
    editor_row = ft.Row(
        spacing=14,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    mode_buttons: dict[str, ft.Button] = {}

    def _layout_editor(mode: str) -> None:
        global _editor_view
        if mode not in EDITOR_VIEWS:
            mode = "split"
        _editor_view = mode
        show_source, show_preview = visible_editor_panes(mode)
        panes: list[ft.Control] = []
        if show_source:
            source_card.expand = 1
            panes.append(source_card)
        if show_preview:
            preview_card.expand = 1
            panes.append(preview_card)
        editor_row.controls = panes
        for key, button in mode_buttons.items():
            active = key == mode
            button.bgcolor = th.PRIMARY if active else th.SUBTLE
            label = getattr(button, "label_control", None)
            if label is not None:
                label.color = th.ON_PRIMARY if active else th.TEXT

    def _choose_view(mode: str) -> None:
        show_preview = visible_editor_panes(mode)[1]
        _layout_editor(mode)
        if show_preview and _preview is not None:
            _cancel_preview_timer()
            _preview.content = _safe_render(source.value or "")
        try:
            editor_row.update()
            mode_row.update()
        except Exception:
            page.update()

    for key, label in (("source", "源码"), ("preview", "预览"), ("split", "并列")):
        text = ft.Text(label, size=12)
        button = ft.Button(
            content=text,
            elevation=0,
            on_click=lambda e, picked=key: _choose_view(picked),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.Padding.symmetric(vertical=6, horizontal=12),
            ),
        )
        button.label_control = text
        mode_buttons[key] = button
    mode_row = ft.Row(
        [mode_buttons[key] for key in ("source", "preview", "split")],
        spacing=6,
    )
    _layout_editor(_editor_view)

    return ft.Column(
        [
            page_header(
                "编辑笔记",
                "自动保存 · 源码、预览或并列",
                actions=[
                    mode_row,
                    upload_button(page, insert_image, "插入图片", "选择要插入的图片"),
                    ft.TextButton("返回", on_click=go_back),
                ],
            ),
            card(
                ft.Column(
                    [
                        title_field,
                        ft.Column([tags_field, suggestion_row], spacing=6),
                        divider(),
                        editor_row,
                    ],
                    spacing=8,
                    expand=True,
                ),
                expand=True,
            ),
        ],
        spacing=12,
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
                                content=ft.Text("▾" if expanded else "▸",
                                                size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                                width=18,
                                alignment=ft.Alignment.CENTER,
                                on_click=lambda e, p=path: toggle_expand(p),
                                visible=has_children,
                            ),
                            ft.Text(name, size=th.FONT_BODY,
                                    color=th.PRIMARY if active else th.TEXT,
                                    weight="bold" if active else "normal",
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True),
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
            title=ft.Text(title, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold",
                           max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            subtitle=ft.Text(
                (snippet or "").replace("\n", " ")[:60] or "（暂无正文）",
                size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                overflow=ft.TextOverflow.ELLIPSIS,
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
            page_header(
                "笔记",
                f"共 {len(records)} 条笔记 · 当前筛选 {len(filtered)} 条",
                actions=[primary_button("新增笔记", add_note, ACTIONS["add"])],
            ),
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
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ],
        spacing=12,
        expand=True,
    )
