"""日记自动建规则。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Diary
from ..db.repo import create, update
from .dates import diary_title


def ensure_diary(session: Session, day: date) -> Diary:
    """取当天日记，没有就建；**优先复用墓碑**。

    全表唯一约束时代，删掉一篇日记后墓碑仍占着 ``(owner_id, date)``，直接新建会撞
    唯一约束。现在约束改成「仅活跃行唯一」，但墓碑还是建议复用：一是把 id 留住，
    云端同一条记录不会被拆成两条；二是走 ``update()`` 才会标脏进同步队列。
    """
    obj = session.scalar(
        select(Diary)
        .where(Diary.owner_id == "local", Diary.date == day)
        .order_by(Diary.is_deleted, Diary.updated_at.desc())
    )
    if obj is None:
        return create(session, Diary, date=day, title=diary_title(day), content="")
    if obj.is_deleted:
        update(session, obj, is_deleted=False, title=diary_title(day), content="")
    return obj
