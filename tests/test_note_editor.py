"""笔记编辑页快照：重建时必须以数据库为准，否则旧快照会覆盖用户刚输入的内容。"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

from app.ui import notes


class _FakeSession:
    def __init__(self, row):
        self._row = row

    def get(self, _model, _key):
        return self._row


def _scope_with(row):
    @contextmanager
    def _scope():
        yield _FakeSession(row)

    return _scope


class NoteSnapshotTests(unittest.TestCase):
    def test_database_value_wins_over_stale_object(self) -> None:
        stale = SimpleNamespace(id="n1", title="旧标题", content="旧内容", tags="[]")
        fresh = SimpleNamespace(id="n1", title="新标题", content="新内容", tags='["标签"]')

        with mock.patch.object(notes, "session_scope", _scope_with(fresh)):
            note_id, title, content, tags = notes._note_snapshot(stale)

        self.assertEqual(note_id, "n1")
        self.assertEqual(title, "新标题")
        self.assertEqual(content, "新内容")
        self.assertEqual(tags, '["标签"]')

    def test_falls_back_to_object_when_row_missing(self) -> None:
        stale = SimpleNamespace(id="n2", title="标题", content="内容", tags="[]")

        with mock.patch.object(notes, "session_scope", _scope_with(None)):
            note_id, title, content, tags = notes._note_snapshot(stale)

        self.assertEqual((note_id, title, content, tags), ("n2", "标题", "内容", "[]"))


class EditorViewTests(unittest.TestCase):
    def test_source_preview_and_split(self) -> None:
        self.assertEqual(notes.visible_editor_panes("source"), (True, False))
        self.assertEqual(notes.visible_editor_panes("preview"), (False, True))
        self.assertEqual(notes.visible_editor_panes("split"), (True, True))
        self.assertEqual(notes.visible_editor_panes("其他"), (True, True))


if __name__ == "__main__":
    unittest.main()
