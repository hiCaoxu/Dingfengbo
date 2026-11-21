"""统一时间轴、跨模块标签与记录关联。

把「待办/日记/笔记/健康/娱乐/记账/习惯」按日期汇到一条时间线上，并提供
跨模块标签聚合与记录之间的显式关联（``record_links`` 表）。

时间轴条目只带摘要，点开仍回各模块编辑，避免这里变成第二套业务逻辑。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Diary,
    Diet,
    Entertainment,
    Exercise,
    Habit,
    HabitLog,
    Note,
    RecordLink,
    Sleep,
    Todo,
    Transaction,
    Weight,
    table_label,
)
from ..db.session import session_scope
from .dates import local_date_from_utc

# 时间轴模块：键 → (中文, 跳转模块)
MODULE_META = {
    "todo": ("任务", "todo"),
    "diary": ("日记", "diary"),
    "notes": ("笔记", "notes"),
    "exercise": ("锻炼", "health"),
    "diet": ("饮食", "health"),
    "weight": ("体重", "health"),
    "sleep": ("睡眠", "health"),
    "fun": ("娱乐", "fun"),
    "finance": ("收支", "finance"),
    "habit": ("习惯", "todo"),
}


@dataclass
class TimelineItem:
    module: str
    table: str
    record_id: str
    day: date
    title: str
    detail: str = ""
    tags: list[str] = field(default_factory=list)
    at: str = ""

    @property
    def label(self) -> str:
        return MODULE_META.get(self.module, (self.module, ""))[0]


def _tags(raw: str | None) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(v).strip() for v in values if str(v).strip()] if isinstance(values, list) else []


def _clip(text: str | None, size: int = 60) -> str:
    body = (text or "").replace("\n", " ").strip()
    return body[:size] + ("…" if len(body) > size else "")


def collect(
    start: date,
    end: date,
    modules: list[str] | None = None,
    tag: str | None = None,
    limit: int = 400,
) -> list[TimelineItem]:
    """收集区间内的所有记录，按日期倒序返回。"""
    selected = set(modules or MODULE_META)
    items: list[TimelineItem] = []

    def want(module: str) -> bool:
        return module in selected

    with session_scope() as session:
        if want("todo"):
            for item in session.scalars(
                select(Todo).where(
                    Todo.is_deleted.is_(False),
                    Todo.is_template.is_(False),
                    Todo.created_at >= datetime.combine(start, time.min),
                )
            ):
                day = item.due_date or local_date_from_utc(item.created_at)
                if start <= day <= end:
                    items.append(
                        TimelineItem(
                            "todo", "todos", item.id, day, item.title,
                            ("已完成" if item.completed else "进行中")
                            + (f" · {item.priority}" if item.priority else ""),
                            _tags(item.tags) + ([item.priority] if item.priority else []),
                            item.due_time or "",
                        )
                    )
        if want("diary"):
            for item in session.scalars(
                select(Diary).where(
                    Diary.is_deleted.is_(False), Diary.date >= start, Diary.date <= end
                )
            ):
                items.append(
                    TimelineItem(
                        "diary", "diaries", item.id, item.date, item.title,
                        _clip(item.content, 80),
                    )
                )
        if want("notes"):
            for item in session.scalars(select(Note).where(Note.is_deleted.is_(False))):
                day = local_date_from_utc(item.updated_at)
                if start <= day <= end:
                    items.append(
                        TimelineItem(
                            "notes", "notes", item.id, day, item.title or "未命名笔记",
                            _clip(item.content, 80), _tags(item.tags),
                        )
                    )
        if want("exercise"):
            for item in session.scalars(
                select(Exercise).where(
                    Exercise.is_deleted.is_(False), Exercise.date >= start, Exercise.date <= end
                )
            ):
                items.append(
                    TimelineItem(
                        "exercise", "exercises", item.id, item.date,
                        f"{item.kind} {int(item.minutes or 0)} 分钟",
                        f"{round(float(item.calories or 0))} 千卡" + (f" · {item.note}" if item.note else ""),
                        [item.kind],
                    )
                )
        if want("diet"):
            for item in session.scalars(
                select(Diet).where(
                    Diet.is_deleted.is_(False), Diet.date >= start, Diet.date <= end
                )
            ):
                items.append(
                    TimelineItem(
                        "diet", "diets", item.id, item.date,
                        f"{item.meal} · {item.food}",
                        f"{round(float(item.calories or 0))} 千卡", [item.meal],
                    )
                )
        if want("weight"):
            for item in session.scalars(
                select(Weight).where(
                    Weight.is_deleted.is_(False), Weight.date >= start, Weight.date <= end
                )
            ):
                items.append(
                    TimelineItem(
                        "weight", "weights", item.id, item.date, f"体重 {float(item.kg):g} kg",
                        f"体脂 {float(item.body_fat):g}%" if item.body_fat else "",
                    )
                )
        if want("sleep"):
            for item in session.scalars(
                select(Sleep).where(
                    Sleep.is_deleted.is_(False), Sleep.date >= start, Sleep.date <= end
                )
            ):
                items.append(
                    TimelineItem(
                        "sleep", "sleeps", item.id, item.date,
                        f"睡眠 {float(item.hours or 0):g} 小时",
                        f"{item.bedtime} → {item.wake_time} · 质量 {item.quality}/5",
                    )
                )
        if want("fun"):
            for item in session.scalars(
                select(Entertainment).where(Entertainment.is_deleted.is_(False))
            ):
                day = local_date_from_utc(item.updated_at)
                if start <= day <= end:
                    items.append(
                        TimelineItem(
                            "fun", "entertainments", item.id, day, item.title,
                            f"{item.kind} · {item.status}", [item.kind, item.status],
                        )
                    )
        if want("finance"):
            for item in session.scalars(
                select(Transaction).where(
                    Transaction.is_deleted.is_(False),
                    Transaction.date >= start,
                    Transaction.date <= end,
                )
            ):
                sign = "+" if item.kind == "income" else "-"
                items.append(
                    TimelineItem(
                        "finance", "transactions", item.id, item.date,
                        f"{item.category or '未分类'} {sign}¥{float(item.amount or 0):,.2f}",
                        _clip(item.note, 50), [item.category] if item.category else [],
                    )
                )
        if want("habit"):
            habits = {
                item.id: item.name
                for item in session.scalars(select(Habit).where(Habit.is_deleted.is_(False)))
            }
            for log in session.scalars(
                select(HabitLog).where(
                    HabitLog.is_deleted.is_(False),
                    HabitLog.done.is_(True),
                    HabitLog.date >= start,
                    HabitLog.date <= end,
                )
            ):
                name = habits.get(log.habit_id)
                if not name:
                    continue
                items.append(
                    TimelineItem("habit", "habit_logs", log.id, log.date, f"打卡：{name}", log.note or "", [name])
                )

    if tag:
        items = [item for item in items if tag in item.tags]
    items.sort(key=lambda item: (item.day, item.at), reverse=True)
    return items[:limit]


def tag_counts(days: int = 365, today: date | None = None) -> list[tuple[str, int, set[str]]]:
    """跨模块标签聚合：返回 [(标签, 次数, {来源模块})]，按次数倒序。"""
    today = today or date.today()
    start = today - timedelta(days=days)
    counts: dict[str, int] = defaultdict(int)
    sources: dict[str, set[str]] = defaultdict(set)
    with session_scope() as session:
        for item in session.scalars(
            select(Todo).where(
                Todo.is_deleted.is_(False), Todo.created_at >= datetime.combine(start, time.min)
            )
        ):
            for tag in _tags(item.tags):
                counts[tag] += 1
                sources[tag].add("任务")
        for item in session.scalars(select(Note).where(Note.is_deleted.is_(False))):
            for tag in _tags(item.tags):
                counts[tag] += 1
                sources[tag].add("笔记")
        for item in session.scalars(select(Entertainment).where(Entertainment.is_deleted.is_(False))):
            for tag in (item.kind, item.status):
                if tag:
                    counts[tag] += 1
                    sources[tag].add("娱乐")
        for habit in session.scalars(select(Habit).where(Habit.is_deleted.is_(False))):
            counts[habit.name] += 0
            sources[habit.name].add("习惯")
    ordered = sorted(counts.items(), key=lambda item: -item[1])
    return [(tag, count, sources[tag]) for tag, count in ordered if count > 0]


# ------------------------------------------------------------------ 关联

_TITLE_FIELDS = {
    "todos": "title",
    "diaries": "title",
    "notes": "title",
    "transactions": "note",
    "entertainments": "title",
    "exercises": "kind",
    "diets": "food",
    "habits": "name",
}

_TITLE_MODELS = {
    "todos": Todo,
    "diaries": Diary,
    "notes": Note,
    "transactions": Transaction,
    "entertainments": Entertainment,
    "exercises": Exercise,
    "diets": Diet,
    "habits": Habit,
}


def record_title(session: Session, table: str, record_id: str) -> str:
    model = _TITLE_MODELS.get(table)
    if model is None:
        return table_label(table)
    obj = session.get(model, record_id)
    if obj is None:
        return "（已删除）"
    field = _TITLE_FIELDS.get(table, "title")
    value = getattr(obj, field, "") or ""
    if table == "transactions":
        return f"{obj.category or '未分类'} ¥{float(obj.amount or 0):,.2f}" if value == "" else value
    return str(value) or table_label(table)


def create_link(
    from_table: str, from_id: str, to_table: str, to_id: str, label: str = ""
) -> str | None:
    """创建关联；重复关联返回 None。"""
    from ..db.repo import create

    with session_scope() as session:
        exists = session.scalar(
            select(RecordLink).where(
                RecordLink.is_deleted.is_(False),
                RecordLink.from_table == from_table,
                RecordLink.from_id == from_id,
                RecordLink.to_table == to_table,
                RecordLink.to_id == to_id,
            )
        )
        if exists is not None:
            return None
        link = create(session, RecordLink, from_table=from_table, from_id=from_id,
                      to_table=to_table, to_id=to_id, label=label)
        return link.id


def delete_link(link_id: str) -> None:
    from ..db.repo import soft_delete

    with session_scope() as session:
        link = session.get(RecordLink, link_id)
        if link is not None:
            soft_delete(session, link)


def links_for(table: str, record_id: str) -> list[dict[str, Any]]:
    """某条记录的关联（双向），含对端标题。"""
    with session_scope() as session:
        rows = session.scalars(
            select(RecordLink).where(
                RecordLink.is_deleted.is_(False),
                ((RecordLink.from_table == table) & (RecordLink.from_id == record_id))
                | ((RecordLink.to_table == table) & (RecordLink.to_id == record_id)),
            )
        )
        result = []
        for link in rows:
            outgoing = link.from_table == table and link.from_id == record_id
            peer_table = link.to_table if outgoing else link.from_table
            peer_id = link.to_id if outgoing else link.from_id
            result.append(
                {
                    "id": link.id,
                    "table": peer_table,
                    "record_id": peer_id,
                    "label": link.label or table_label(peer_table),
                    "title": record_title(session, peer_table, peer_id),
                }
            )
        return result


def link_targets(query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """可被关联的记录候选（标题模糊匹配）。"""
    result: list[dict[str, Any]] = []
    keyword = (query or "").strip()
    with session_scope() as session:
        candidates = [
            (Todo, "todos", Todo.title),
            (Note, "notes", Note.title),
            (Diary, "diaries", Diary.title),
            (Entertainment, "entertainments", Entertainment.title),
        ]
        for model, table, column in candidates:
            stmt = select(model).where(model.is_deleted.is_(False))
            if keyword:
                stmt = stmt.where(column.contains(keyword, autoescape=True))
            if table == "diaries":
                stmt = stmt.order_by(Diary.date.desc())
            else:
                stmt = stmt.order_by(model.updated_at.desc())
            for obj in session.scalars(stmt.limit(limit)):
                result.append(
                    {
                        "table": table,
                        "record_id": obj.id,
                        "title": record_title(session, table, obj.id),
                        "label": table_label(table),
                    }
                )
    return result[:limit]
