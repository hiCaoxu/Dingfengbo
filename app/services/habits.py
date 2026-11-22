"""习惯：打卡、连续天数、周目标进度与日历热力图。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from sqlalchemy import select

from ..db.models import Habit, HabitLog
from ..db.repo import create, soft_delete, update
from ..db.session import session_scope

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 400


def streak(days: Iterable[date], today: date | None = None) -> int:
    """连续天数：从今天（或昨天）往前数连续打卡。"""
    today = today or date.today()
    done = set(days)
    cursor = today if today in done else today - timedelta(days=1)
    count = 0
    while cursor in done:
        count += 1
        cursor -= timedelta(days=1)
    return count


def longest_streak(days: Iterable[date]) -> int:
    ordered = sorted(set(days))
    best = current = 0
    previous: date | None = None
    for day in ordered:
        current = current + 1 if previous and (day - previous).days == 1 else 1
        best = max(best, current)
        previous = day
    return best


def week_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def week_count(days: Iterable[date], today: date | None = None) -> int:
    start, end = week_bounds(today)
    return sum(1 for day in set(days) if start <= day <= end)


def overview(today: date | None = None) -> list[dict[str, Any]]:
    """全部习惯的当日/周进度与连续天数（一次查询取回近一年打卡）。"""
    today = today or date.today()
    edge = today - timedelta(days=LOOKBACK_DAYS)
    with session_scope() as session:
        habits = list(
            session.scalars(
                select(Habit)
                .where(Habit.is_deleted.is_(False), Habit.archived.is_(False))
                .order_by(Habit.created_at)
            )
        )
        logs = list(
            session.scalars(
                select(HabitLog).where(
                    HabitLog.is_deleted.is_(False),
                    HabitLog.done.is_(True),
                    HabitLog.date >= edge,
                )
            )
        )
    by_habit: dict[str, set[date]] = defaultdict(set)
    for log in logs:
        by_habit[log.habit_id].add(log.date)
    result = []
    for habit in habits:
        days = by_habit.get(habit.id, set())
        done_today = today in days
        weekly = week_count(days, today)
        result.append(
            {
                "habit": habit,
                "id": habit.id,
                "name": habit.name,
                "target_per_week": int(habit.target_per_week or 7),
                "target_minutes": int(habit.target_minutes or 0),
                "note": habit.note or "",
                "done_today": done_today,
                "streak": streak(days, today),
                "longest": longest_streak(days),
                "week_done": weekly,
                "week_percent": round(weekly / max(1, int(habit.target_per_week or 7)) * 100),
                "days": days,
            }
        )
    return result


def check_in(habit_id: str, day: date | None = None, done: bool = True) -> str:
    """打卡/取消打卡（按 habit+date 幂等），返回记录 ID。"""
    from ..services.sync import mark_pending_write

    day = day or date.today()
    with session_scope() as session:
        row = session.scalar(
            select(HabitLog).where(
                HabitLog.is_deleted.is_(False),
                HabitLog.habit_id == habit_id,
                HabitLog.date == day,
            )
        )
        if row is None:
            if not done:
                return ""
            row = create(session, HabitLog, habit_id=habit_id, date=day, done=True)
        else:
            update(session, row, done=done)
        record_id = row.id
    mark_pending_write()
    return record_id


def save_habit(
    habit_id: str | None,
    *,
    name: str,
    target_per_week: int,
    target_minutes: int = 0,
    note: str = "",
) -> None:
    from ..services.sync import mark_pending_write

    with session_scope() as session:
        if habit_id:
            habit = session.get(Habit, habit_id)
            if habit is None:
                return
            update(
                session,
                habit,
                name=name,
                target_per_week=target_per_week,
                target_minutes=target_minutes,
                note=note,
            )
        else:
            create(
                session,
                Habit,
                name=name,
                target_per_week=target_per_week,
                target_minutes=target_minutes,
                note=note,
            )
    mark_pending_write()


def delete_habit(habit_id: str) -> None:
    from ..services.sync import mark_pending_write

    with session_scope() as session:
        habit = session.get(Habit, habit_id)
        if habit is not None:
            soft_delete(session, habit)
        for log in session.scalars(
            select(HabitLog).where(
                HabitLog.is_deleted.is_(False), HabitLog.habit_id == habit_id
            )
        ):
            soft_delete(session, log)
    mark_pending_write()


def calendar(habit_id: str, weeks: int = 12, today: date | None = None) -> list[list[date | None]]:
    """按周分列的打卡日历（列=周，行=周一..周日），用于习惯热力图。"""
    today = today or date.today()
    start, _ = week_bounds(today)
    start -= timedelta(weeks=weeks - 1)
    with session_scope() as session:
        days = {
            log.date
            for log in session.scalars(
                select(HabitLog).where(
                    HabitLog.is_deleted.is_(False),
                    HabitLog.done.is_(True),
                    HabitLog.habit_id == habit_id,
                    HabitLog.date >= start,
                )
            )
        }
    columns: list[list[date | None]] = []
    for index in range(weeks):
        column: list[date | None] = []
        for weekday in range(7):
            day = start + timedelta(days=index * 7 + weekday)
            column.append(day if day in days else (None if day > today else day))
        columns.append(column)
    return columns


def pending_today(today: date | None = None) -> list[str]:
    """今天还没打卡的习惯名（供今日聚焦）。"""
    return [
        item["name"]
        for item in overview(today)
        if not item["done_today"]
    ]
