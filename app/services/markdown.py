"""轻量 Markdown 解析 + Pygments 代码高亮（不渲染公式与图表）。"""

from __future__ import annotations

import re
from typing import Any

import flet as ft
from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.token import Token

from .. import theme as th

_TOKEN_COLORS = {
    Token.Keyword: "#7B5EA7",
    Token.Keyword.Constant: "#C97B63",
    Token.Name.Function: "#3E6E8E",
    Token.Name.Class: "#8A6D3B",
    Token.Name.Builtin: "#4A7C59",
    Token.String: "#A66A3A",
    Token.Number: "#B0693F",
    Token.Comment: th.TEXT_MUTED,
    Token.Operator: th.TEXT,
    Token.Punctuation: th.TEXT_MUTED,
    Token.Generic.Heading: th.PRIMARY,
    Token.Generic.Subheading: th.PRIMARY,
}


def _color_for(token: Token) -> str | None:
    for key, color in _TOKEN_COLORS.items():
        if token in key:
            return color
    return None


def highlight_lines(code: str, lang: str = "") -> list[list[ft.TextSpan]]:
    try:
        lexer = get_lexer_by_name(lang, stripnl=False) if lang else TextLexer(stripnl=False)
    except Exception:
        lexer = TextLexer(stripnl=False)
    result: list[list[ft.TextSpan]] = [[]]
    for token, value in lex(code, lexer):
        for part in value.split("\n"):
            if part:
                result[-1].append(
                    ft.TextSpan(part, style=ft.TextStyle(color=_color_for(token) or th.TEXT, size=12, font_family="Consolas"))
                )
            if "\n" in value:
                result.append([])
    if result and not result[-1]:
        result.pop()
    return result or [[]]


def inline_spans(text: str) -> list[ft.TextSpan]:
    """支持 **粗体**、*斜体*、`行内代码`。"""
    pattern = re.compile(r"(\*\*.*?\*\*|\*.*?\*|`.*?`)")
    spans: list[ft.TextSpan] = []
    for part in pattern.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            spans.append(ft.TextSpan(part[2:-2], style=ft.TextStyle(size=13, color=th.TEXT, weight="bold")))
        elif part.startswith("`") and part.endswith("`"):
            spans.append(
                ft.TextSpan(
                    part[1:-1],
                    style=ft.TextStyle(size=12, color=th.PRIMARY, font_family="Consolas", bgcolor="#F4F6F3"),
                )
            )
        elif part.startswith("*") and part.endswith("*"):
            spans.append(ft.TextSpan(part[1:-1], style=ft.TextStyle(size=13, color=th.TEXT, italic=True)))
        else:
            spans.append(ft.TextSpan(part, style=ft.TextStyle(size=13, color=th.TEXT)))
    return spans


def _split_table_row(line: str) -> list[str]:
    line = (line or "").strip().strip("|")
    return [c.strip() for c in line.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c) for c in cells)


def parse(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    lines = (markdown or "").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        fence = re.match(r"^\s*```(\w*)", line)
        if fence:
            lang = fence.group(1)
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            index += 1
            blocks.append({"type": "code", "lang": lang, "code": "\n".join(code_lines)})
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2)})
            index += 1
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            blocks.append({"type": "hr"})
            index += 1
            continue
        if line.startswith("> "):
            quote: list[str] = []
            while index < len(lines) and lines[index].startswith("> "):
                quote.append(lines[index][2:])
                index += 1
            blocks.append({"type": "quote", "text": " ".join(quote)})
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items: list[str] = []
            while index < len(lines) and re.match(r"^\s*[-*]\s+", lines[index]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[index]))
                index += 1
            blocks.append({"type": "list", "items": items})
            continue
        if line.strip().startswith("|") and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            header = _split_table_row(line)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_split_table_row(lines[index]))
                index += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue
        if not line.strip():
            index += 1
            continue
        paragraph: list[str] = []
        while index < len(lines) and lines[index].strip() and not re.match(
            r"^\s*(```|#{1,6}\s|>\s|[-*]\s)", lines[index]
        ):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append({"type": "text", "text": " ".join(paragraph)})
    return blocks
