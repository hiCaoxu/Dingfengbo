"""日记自动建规则。"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Diary
from ..db.repo import create
from .dates import diary_title


def ensure_diary(session: Session, day: date) -> Diary:
    obj = session.scalar(select(Diary).where(Diary.date == day, Diary.is_deleted.is_(False)))
    if obj is None:
        obj = create(session, Diary, date=day, title=diary_title(day), content="")
    return obj
