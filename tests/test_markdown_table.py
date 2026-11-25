"""Markdown 预览：表格行列不齐不能炸、首渲染要有兜底。"""

from __future__ import annotations

import unittest

import flet as ft

from app.services.markdown import parse
from app.ui.notes import _normalize_table_rows, render_blocks, render_markdown


def _find(control, kind):
    """在控件树里找指定类型（Flet 控件是 dataclass，直接遍历属性）。"""
    found = []
    stack = [control]
    while stack:
        current = stack.pop()
        if isinstance(current, kind):
            found.append(current)
            continue
        for name in ("controls", "content", "cells", "rows", "columns"):
            value = getattr(current, name, None)
            if isinstance(value, list):
                stack.extend(value)
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                stack.append(value)
    return found


class TableRowNormalizationTests(unittest.TestCase):
    def test_short_and_long_rows_are_fixed(self) -> None:
        header = ["A", "B", "C"]
        rows = [["1"], ["1", "2", "3", "4"], []]
        normalized = _normalize_table_rows(header, rows)
        self.assertEqual([len(row) for row in normalized], [3, 3, 3])
        self.assertEqual(normalized[0], ["1", "", ""], "短行补空串")
        self.assertEqual(normalized[1], ["1", "2", "3"], "长行截断")
        self.assertEqual(normalized[2], ["", "", ""])

    def test_empty_header_yields_no_rows(self) -> None:
        self.assertEqual(_normalize_table_rows([], [["1"]]), [])


class TableRenderTests(unittest.TestCase):
    def test_ragged_markdown_table_renders_with_matching_cells(self) -> None:
        text = (
            "| 列1 | 列2 | 列3 |\n"
            "| --- | --- | --- |\n"
            "| 只有一格 |\n"
            "| 1 | 2 | 3 | 4 |\n"
        )
        blocks = parse(text)
        control = render_blocks(blocks)

        def walk(node):
            yield node
            for name in ("controls", "content"):
                value = getattr(node, name, None)
                if isinstance(value, list):
                    for child in value:
                        yield from walk(child)
                elif isinstance(value, ft.Control):
                    yield from walk(value)

        tables = [item for item in walk(control) if getattr(item, "data", None) == "md-table"]
        self.assertEqual(len(tables), 1)
        rows = [item for item in tables[0].controls if isinstance(item, ft.Row)]
        self.assertEqual(len(rows), 3, "表头加两行数据")
        for row in rows:
            self.assertEqual(len(row.controls), 3, "每行单元格数必须等于表头列数")

    def test_render_markdown_handles_ragged_table(self) -> None:
        control = render_markdown("| a | b |\n| --- | --- |\n| 1 |\n")
        self.assertIsNotNone(control)


class QuoteAndCodeTests(unittest.TestCase):
    def test_blockquote_is_not_italic(self) -> None:
        control = render_markdown("> 一段引用")
        texts = _find(control, ft.Text)
        self.assertTrue(texts)
        for text in texts:
            self.assertIsNot(text.italic, True)
            for span in text.spans or []:
                style = span.style
                if style is not None:
                    self.assertIsNot(style.italic, True)

    def test_code_block_shows_language_and_copy(self) -> None:
        control = render_markdown("```python\nprint(1)\n```")
        labels = [text.value for text in _find(control, ft.Text) if text.value]
        self.assertIn("python", labels)
        copied = [button.data for button in _find(control, ft.TextButton)]
        self.assertIn("print(1)", copied)

    def test_code_block_without_language_says_plain_text(self) -> None:
        control = render_markdown("```\nx\n```")
        labels = [text.value for text in _find(control, ft.Text) if text.value]
        self.assertIn("纯文本", labels)


if __name__ == "__main__":
    unittest.main()
