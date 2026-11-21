"""人生大事：长期目标与里程碑清单。

和任务的区别：不参与每天的待办流转，没有到期催办，只记录「这辈子要做成的事」，
做完留一个完成时间，慢慢沉淀成一份人生履历。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from ..db.models import LifeEvent, utcnow
from ..db.repo import create, soft_delete, update
from ..db.session import session_scope

MAX_TITLE = 15  # 一行放下，不缩成两行


def _row(event: LifeEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "note": event.note,
        "target_date": event.target_date,
        "done": event.done,
        "done_at": event.done_at,
    }


def list_events() -> list[dict[str, Any]]:
    """按事件日期倒序（新的在上），没填日期的排在最后。"""
    with session_scope() as session:
        events = session.scalars(
            select(LifeEvent)
            .where(LifeEvent.is_deleted.is_(False))
            .order_by(
                LifeEvent.target_date.is_(None),
                LifeEvent.target_date.desc(),
                LifeEvent.created_at.desc(),
            )
        ).all()
        return [_row(event) for event in events]


def add_event(
    title: str,
    target_date: date | None = None,
    note: str = "",
) -> str | None:
    """新增一件人生大事，返回 id；标题为空则忽略。"""
    title = title.strip()[:MAX_TITLE]
    if not title:
        return None
    with session_scope() as session:
        event = create(session, LifeEvent, title=title, target_date=target_date, note=note)
        return event.id


def toggle_event(event_id: str) -> bool | None:
    """完成/取消完成，返回新的完成状态；记录不存在返回 None。"""
    with session_scope() as session:
        event = session.get(LifeEvent, event_id)
        if event is None or event.is_deleted:
            return None
        done = not event.done
        update(session, event, done=done, done_at=utcnow() if done else None)
        return done


def rename_event(event_id: str, title: str) -> bool:
    title = title.strip()[:MAX_TITLE]
    if not title:
        return False
    with session_scope() as session:
        event = session.get(LifeEvent, event_id)
        if event is None or event.is_deleted:
            return False
        update(session, event, title=title)
        return True


def delete_event(event_id: str) -> bool:
    with session_scope() as session:
        event = session.get(LifeEvent, event_id)
        if event is None or event.is_deleted:
            return False
        soft_delete(session, event)
        return True
