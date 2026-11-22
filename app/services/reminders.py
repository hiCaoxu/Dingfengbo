"""待办提醒：提醒时刻计算、到点收集与去重。

语义：``remind_days`` 是提前天数数组（``[]`` 为不提醒，``0`` 为到期当天，``N`` 为提前 N 天），
支持多选（如 ``[1, 3, 7]`` 表示提前 3 天、1 天与当天各提醒一次）。旧版单值字段
``remind_days_before`` 仍兼容：新字段为空时按它推断。

提醒时刻的时分取 ``remind_time``，为空退回 ``due_time``，再为空按 09:00。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..db.models import Todo, TodoReminder, utcnow
from .dates import parse_hm

logger = logging.getLogger(__name__)

REMIND_NONE = -1
DEFAULT_REMIND_TIME = "09:00"
# 只在这个时间窗内补发：应用长时间未启动时不要把陈年提醒一次性全弹出来。
GRACE = timedelta(hours=12)
SCAN_DAYS_AHEAD = 366
PRUNE_DAYS = 90


def _hm(value: str, fallback: str = DEFAULT_REMIND_TIME) -> time:
    return parse_hm(value) or parse_hm(fallback) or time(9, 0)


def parse_offsets(todo: Todo) -> list[int]:
    """解析提前天数集合；新字段为空时回退到旧版单值字段。"""
    try:
        raw = json.loads(todo.remind_days or "[]")
    except (ValueError, TypeError) as exc:
        logger.debug("parse_offsets：忽略 %s", type(exc).__name__)
        raw = []
    offsets: list[int] = []
    if isinstance(raw, list):
        offsets = sorted(
            {
                int(day)
                for day in raw
                if str(day).lstrip("-").isdigit() and int(day) >= 0
            }
        )
    if offsets:
        return offsets
    try:
        legacy = int(todo.remind_days_before if todo.remind_days_before is not None else -1)
    except (TypeError, ValueError) as exc:
        logger.debug("parse_offsets：忽略 %s", type(exc).__name__)
        legacy = REMIND_NONE
    return [legacy] if legacy >= 0 else []


def due_at(todo: Todo) -> datetime | None:
    """到期时刻（无到期日返回 None；无时间按 09:00）。"""
    if todo.due_date is None:
        return None
    return datetime.combine(todo.due_date, _hm(todo.due_time))


def reminder_times(todo: Todo) -> list[datetime]:
    """全部应提醒时刻（升序）；未开启提醒返回空列表。"""
    if todo.due_date is None:
        return []
    moment = _hm(todo.remind_time or todo.due_time)
    return sorted(
        datetime.combine(todo.due_date - timedelta(days=offset), moment)
        for offset in parse_offsets(todo)
    )


def reminder_at(todo: Todo) -> datetime | None:
    """最早的提醒时刻（保留给旧调用方）。"""
    times = reminder_times(todo)
    return times[0] if times else None


def describe(todo: Todo) -> str:
    """列表里的提醒摘要，如「提醒 3/1/0 天前」。"""
    offsets = parse_offsets(todo)
    if not offsets:
        return ""
    labels = ["当天" if offset == 0 else f"{offset}天" for offset in offsets]
    return "提醒 " + "/".join(labels)


def _body(todo: Todo, offset: int) -> str:
    parts: list[str] = []
    if todo.due_date is not None:
        label = f"到期 {todo.due_date.isoformat()}"
        if todo.due_time:
            label += f" {todo.due_time}"
        parts.append(label)
    if todo.priority:
        parts.append(f"优先级 {todo.priority}")
    if offset > 0:
        parts.append(f"提前 {offset} 天")
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
            or_(Todo.remind_days != "[]", Todo.remind_days_before >= 0),
            Todo.due_date.is_not(None),
            Todo.due_date >= (window_start.date() - timedelta(days=1)),
            Todo.due_date <= (now.date() + timedelta(days=SCAN_DAYS_AHEAD)),
        )
    )
    fired: list[dict] = []
    for todo in todos:
        offsets = parse_offsets(todo)
        for offset in offsets:
            fire = datetime.combine(
                todo.due_date - timedelta(days=offset),
                _hm(todo.remind_time or todo.due_time),
            )
            if fire > now or fire < window_start:
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
                    "offset": offset,
                    "title": todo.title,
                    "body": _body(todo, offset),
                }
            )
    _prune(session)
    return fired


def forget(session: Session, todo_id: str, fire_at: datetime) -> None:
    """撤销一次已登记但没能送达的提醒，让下一轮重新收集并重试。

    ``collect_due`` 是先登记再返回给调用方弹窗；如果弹窗环节抛异常（PowerShell
    被策略拦截、无窗口句柄等），这条提醒会被误记为"已发送"，之后再也不会出现。
    """
    session.execute(
        delete(TodoReminder).where(
            TodoReminder.todo_id == todo_id, TodoReminder.fire_at == fire_at
        )
    )


def _prune(session: Session) -> None:
    """清理过期登记。

    ``TodoReminder.created_at`` 是 naive UTC（模型默认 ``utcnow``），而提醒的
    ``fire_at`` 用本地时间。旧实现拿本地 ``now`` 去减保留天数再比 UTC 列，
    UTC+8 下整整偏了 8 小时——过期行会多留半天，清理窗口也一并偏移。
    """
    cutoff = utcnow() - timedelta(days=PRUNE_DAYS)
    session.execute(delete(TodoReminder).where(TodoReminder.created_at < cutoff))
