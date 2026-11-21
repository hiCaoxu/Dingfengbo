"""待办提醒：提醒时刻计算、到点收集与去重。

语义：``remind_days_before`` 为 -1 表示不提醒，0 表示到期当天提醒，N 表示提前 N 天；
提醒时刻取 ``remind_time``，为空退回 ``due_time``，再为空按 09:00。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import Todo, TodoReminder
from .dates import parse_hm

REMIND_NONE = -1
DEFAULT_REMIND_TIME = "09:00"
# 只在这个时间窗内补发：应用长时间未启动时不要把陈年提醒一次性全弹出来。
GRACE = timedelta(hours=12)
SCAN_DAYS_AHEAD = 366
PRUNE_DAYS = 90


def _hm(value: str, fallback: str = DEFAULT_REMIND_TIME) -> time:
    return parse_hm(value) or parse_hm(fallback) or time(9, 0)


def due_at(todo: Todo) -> datetime | None:
    """到期时刻（无到期日返回 None；无时间按 09:00）。"""
    if todo.due_date is None:
        return None
    return datetime.combine(todo.due_date, _hm(todo.due_time))


def reminder_at(todo: Todo) -> datetime | None:
    """应提醒时刻；未开启提醒返回 None。"""
    if todo.remind_days_before is None or int(todo.remind_days_before) < 0:
        return None
    if todo.due_date is None:
        return None
    base = todo.due_date - timedelta(days=int(todo.remind_days_before))
    return datetime.combine(base, _hm(todo.remind_time or todo.due_time))


def _body(todo: Todo) -> str:
    parts: list[str] = []
    if todo.due_date is not None:
        label = f"到期 {todo.due_date.isoformat()}"
        if todo.due_time:
            label += f" {todo.due_time}"
        parts.append(label)
    if todo.priority:
        parts.append(f"优先级 {todo.priority}")
    if int(todo.remind_days_before or 0) > 0:
        parts.append(f"提前 {todo.remind_days_before} 天")
    return " · ".join(parts) or "到点提醒"


def collect_due(session: Session, now: datetime | None = None) -> list[dict]:
    """返回并登记本轮到点的提醒；同一 (待办, 提醒时刻) 只会返回一次。"""
    now = now or datetime.now()
    window_start = now - GRACE
    todos = session.scalars(
        select(Todo).where(
            Todo.is_deleted.is_(False),
            Todo.completed.is_(False),
            Todo.is_template.is_(False),
            Todo.remind_days_before >= 0,
            Todo.due_date.is_not(None),
            Todo.due_date >= (window_start.date() - timedelta(days=1)),
            Todo.due_date <= (now.date() + timedelta(days=SCAN_DAYS_AHEAD)),
        )
    )
    fired: list[dict] = []
    for todo in todos:
        fire = reminder_at(todo)
        if fire is None or fire > now or fire < window_start:
            continue
        if session.get(TodoReminder, (todo.id, fire)) is not None:
            continue
        session.add(TodoReminder(todo_id=todo.id, fire_at=fire))
        # SessionLocal 关了 autoflush：不主动 flush，同一会话内再查会看不到刚加的行。
        session.flush()
        fired.append(
            {
                "todo_id": todo.id,
                "fire_at": fire,
                "title": todo.title,
                "body": _body(todo),
            }
        )
    _prune(session, now)
    return fired


def _prune(session: Session, now: datetime) -> None:
    cutoff = now - timedelta(days=PRUNE_DAYS)
    session.execute(delete(TodoReminder).where(TodoReminder.created_at < cutoff))
