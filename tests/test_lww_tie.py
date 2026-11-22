"""LWW 平局（同一 updated_at、不同内容）：确定性决胜 + 败方回执。"""

from __future__ import annotations

import unittest

from sqlalchemy import select

from app.db.models import Note, SyncConflict
from app.db.repo import apply_remote, payload_digest, serialize
from app.db.session import session_scope
from tests.helpers import reset_db

STAMP = "2026-02-01T12:00:00"


def _change(title: str, note_id: str = "tie-note", stamp: str = STAMP) -> dict:
    return {
        "table": "notes",
        "data": {
            "id": note_id,
            "owner_id": "local",
            "created_at": stamp,
            "updated_at": stamp,
            "is_deleted": False,
            "sync_status": "clean",
            "sync_seq": 0,
            "title": title,
            "content": "",
            "tags": "[]",
        },
    }


def _apply(changes: list[dict]) -> list[dict]:
    with session_scope() as session:
        return apply_remote(session, changes, owner_id="local")


def _stored_title(note_id: str = "tie-note") -> str | None:
    with session_scope() as session:
        note = session.get(Note, note_id)
        return note.title if note else None


class LwwTieTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def _expected_winner(self, first: dict, second: dict) -> str:
        model = Note.__table__
        digest_a = payload_digest(Note, first["data"])
        digest_b = payload_digest(Note, second["data"])
        self.assertNotEqual(digest_a, digest_b, "两份不同内容不应撞摘要")
        return first["data"]["title"] if digest_a >= digest_b else second["data"]["title"]

    def test_tie_converges_regardless_of_apply_order(self) -> None:
        a, b = _change("A 版"), _change("B 版")
        expected = self._expected_winner(a, b)

        _apply([a])
        _apply([b])
        first_order = _stored_title()

        reset_db()
        _apply([b])
        _apply([a])
        second_order = _stored_title()

        self.assertEqual(first_order, expected)
        self.assertEqual(second_order, expected, "两种应用顺序必须收敛到同一结果")

    def test_losing_side_gets_superseded_receipt(self) -> None:
        a, b = _change("A 版"), _change("B 版")
        expected = self._expected_winner(a, b)
        loser = b if expected == a["data"]["title"] else a

        _apply([_change(expected)])
        result = _apply([loser])

        self.assertEqual(len(result), 1, "平局不能静默 continue：必须给回执")
        self.assertTrue(result[0].get("superseded"), "落败方应收到 superseded 回执")
        self.assertFalse(result[0].get("changed"))
        self.assertEqual(_stored_title(), expected)

    def test_winning_side_is_applied(self) -> None:
        a, b = _change("A 版"), _change("B 版")
        expected = self._expected_winner(a, b)
        loser = b if expected == a["data"]["title"] else a

        _apply([_change(loser["data"]["title"])])
        result = _apply([_change(expected)])

        self.assertTrue(result[0].get("changed"))
        self.assertFalse(result[0].get("superseded"))
        self.assertEqual(_stored_title(), expected)

    def test_tie_leaves_conflict_record(self) -> None:
        a, b = _change("A 版"), _change("B 版")
        _apply([a])
        _apply([b])
        with session_scope() as session:
            conflicts = list(session.scalars(select(SyncConflict)))
        self.assertTrue(conflicts, "平局应留一条冲突记录供用户核对")

    def test_digest_ignores_sync_metadata(self) -> None:
        """摘要只看业务内容：sync_seq / sync_status / owner_id 变化不应改变摘要。"""
        base = _change("同一内容")
        noisy = _change("同一内容")
        noisy["data"]["sync_seq"] = 123
        noisy["data"]["sync_status"] = "pending"
        noisy["data"]["owner_id"] = "another-owner"
        self.assertEqual(
            payload_digest(Note, base["data"]), payload_digest(Note, noisy["data"])
        )

    def test_digest_matches_orm_serialization(self) -> None:
        """两端算法必须一致：ORM 序列化后的摘要要与同一份 JSON 载荷相同。"""
        _apply([_change("一致性检查")])
        with session_scope() as session:
            note = session.get(Note, "tie-note")
            local_digest = payload_digest(Note, serialize(note)["data"])
        remote_digest = payload_digest(Note, _change("一致性检查")["data"])
        self.assertEqual(local_digest, remote_digest)


if __name__ == "__main__":
    unittest.main()
