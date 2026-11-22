"""人生大事：长期目标与里程碑清单。

和任务的区别：不参与每天的待办流转，没有到期催办，只记录「这辈子要做成的事」。
只支持新增、编辑、删除，不做完成状态流转；列表按事件日期倒序，没填日期的沉底。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from ..db.models import LifeEvent
from ..db.repo import create, soft_delete, update
from ..db.session import session_scope
from .sync import mark_pending_write

MAX_TITLE = 15  # 一行放下，不缩成两行
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _row(event: LifeEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "note": event.note,
        "target_date": event.target_date,
    }


def parse_target_date(text: str) -> date | None:
    """把 ``YYYY-MM-DD`` 文本解析成日期。

    空文本返回 ``None``（表示不填日期），格式或日期本身不合法时抛 ``ValueError``，
    异常消息可以直接展示给用户。
    """
    text = (text or "").strip()
    if not text:
        return None
    if not _DATE_PATTERN.match(text):
        raise ValueError("日期格式应为 YYYY-MM-DD，例如 2026-11-21")
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("日期不存在，请检查月份和日期") from exc


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
        event_id = event.id
    # 写操作后立刻触发同步：漏掉这一步只能等 5 分钟的周期同步兜底。
    mark_pending_write()
    return event_id


def update_event(event_id: str, title: str, target_date: date | None) -> bool:
    """编辑标题和日期；标题为空或记录不存在返回 False。"""
    title = title.strip()[:MAX_TITLE]
    if not title:
        return False
    with session_scope() as session:
        event = session.get(LifeEvent, event_id)
        if event is None or event.is_deleted:
            return False
        update(session, event, title=title, target_date=target_date)
    mark_pending_write()
    return True


def delete_event(event_id: str) -> bool:
    with session_scope() as session:
        event = session.get(LifeEvent, event_id)
        if event is None or event.is_deleted:
            return False
        soft_delete(session, event)
    mark_pending_write()
    return True
