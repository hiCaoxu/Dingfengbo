"""今日聚焦：把「今天该做什么」所需的数据一次取齐。

首页从「数据仪表盘」升级为「行动页」的数据源：今日待办/完成率、日记提醒、
健康摘要、消费与预算、习惯待打卡、写作连续天数、节日与随机回顾。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from ..db.models import (
    Diary,
    Diet,
    Exercise,
    Note,
    Sleep,
    Todo,
    Transaction,
    Weight,
)
from ..db.session import session_scope
from . import budget as budget_service
from .dates import local_date_from_utc, lunar_text
from .habits import overview as habits_overview
from .habits import pending_today
from .holidays import holiday_name


def _writing_streak(session, today: date) -> int:
    days = {
        item.date
        for item in session.scalars(
            select(Diary).where(Diary.is_deleted.is_(False), Diary.content != "")
        )
    }
    cursor = today if today in days else today - timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= timedelta(days=1)
    return count


def today_focus(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    with session_scope() as session:
        todos = list(
            session.scalars(
                select(Todo).where(
                    Todo.is_deleted.is_(False),
                    Todo.completed.is_(False),
                    Todo.is_template.is_(False),
                    Todo.due_date <= today,
                ).order_by(Todo.due_date, Todo.due_time, Todo.priority)
            )
        )
        all_todos = list(
            session.scalars(
                select(Todo).where(Todo.is_deleted.is_(False), Todo.is_template.is_(False))
            )
        )
        completed_today = [
            item
            for item in all_todos
            if item.completed
            and item.completed_at
            and local_date_from_utc(item.completed_at) == today
        ]
        diary = session.scalar(
            select(Diary).where(Diary.is_deleted.is_(False), Diary.date == today)
        )
        exercise_minutes = session.scalar(
            select(func.coalesce(func.sum(Exercise.minutes), 0)).where(
                Exercise.is_deleted.is_(False), Exercise.date == today
            )
        )
        diet_calories = session.scalar(
            select(func.coalesce(func.sum(Diet.calories), 0)).where(
                Diet.is_deleted.is_(False), Diet.date == today
            )
        )
        sleep = session.scalar(
            select(Sleep)
            .where(Sleep.is_deleted.is_(False), Sleep.date <= today)
            .order_by(Sleep.date.desc())
        )
        weight = session.scalar(
            select(Weight)
            .where(Weight.is_deleted.is_(False), Weight.date <= today)
            .order_by(Weight.date.desc())
        )
        spent_today = session.scalar(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.is_deleted.is_(False),
                Transaction.kind == "expense",
                Transaction.date == today,
            )
        )
        note = session.scalar(
            select(Note)
            .where(Note.is_deleted.is_(False))
            .order_by(func.random())
            .limit(1)
        )
        streak = _writing_streak(session, today)
        pomodoro = sum(
            int(item.pomodoro_count or 0)
            for item in completed_today
            if item.pomodoro_count
        )
        overdue = [item for item in todos if item.due_date and item.due_date < today]

    budget_data = budget_service.overview(today)
    habits = habits_overview(today)
    total_today = len(todos) + len(completed_today)
    return {
        "today": today,
        "weekday_festival": holiday_name(today) or lunar_text(today).split(" ")[0],
        "todos": todos[:8],
        "overdue_count": len(overdue),
        "pending_count": len(todos),
        "completed_today": len(completed_today),
        "completion_rate": round(len(completed_today) / total_today * 100) if total_today else 0,
        "diary": diary,
        "diary_written": bool(diary and (diary.content or "").strip()),
        "writing_streak": streak,
        "exercise_minutes": int(exercise_minutes or 0),
        "diet_calories": int(diet_calories or 0),
        "sleep_hours": float(sleep.hours or 0) if sleep else 0.0,
        "sleep_quality": int(sleep.quality or 0) if sleep else 0,
        "weight": float(weight.kg) if weight else None,
        "spent_today": round(float(spent_today or 0), 2),
        "budget": budget_data,
        "budget_alerts": budget_service.alerts(today),
        "habits": habits,
        "habits_pending": pending_today(today),
        "pomodoro": pomodoro,
        "note": note,
    }


def brief_text(data: dict[str, Any]) -> str:
    """把聚焦数据压成一段纯文本，供 AI「今日建议」使用（不含正文）。"""
    lines = [
        f"日期：{data['today'].isoformat()}",
        f"今日未完成任务 {data['pending_count']} 项（已逾期 {data['overdue_count']} 项），"
        f"今天已完成 {data['completed_today']} 项，完成率 {data['completion_rate']}%。",
    ]
    titles = "、".join(item.title for item in data["todos"][:8])
    if titles:
        lines.append(f"今日任务：{titles}")
    lines.append(
        f"日记：{'已写' if data['diary_written'] else '未写'}，连续写作 {data['writing_streak']} 天。"
    )
    lines.append(
        f"健康：运动 {data['exercise_minutes']} 分钟，摄入 {data['diet_calories']} 千卡，"
        f"睡眠 {data['sleep_hours']} 小时（质量 {data['sleep_quality']}/5）。"
    )
    lines.append(f"今日支出 ¥{data['spent_today']:.2f}。")
    if data["budget"]["total_budget"]:
        lines.append(
            f"本月预算 ¥{data['budget']['total_budget']:.2f}，已用 {data['budget']['percent']}%。"
        )
    if data["habits_pending"]:
        lines.append("待打卡习惯：" + "、".join(data["habits_pending"][:6]))
    if data["budget_alerts"]:
        lines.append("提醒：" + "；".join(data["budget_alerts"]))
    return "\n".join(lines)
