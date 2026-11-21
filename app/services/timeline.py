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

from sqlalchemy import and_, or_, select
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

    # 时间轴只显示摘要，所以每张表都只取用得到的列，并把日期过滤下推到 SQL：
    # select(Todo) 会把整行（含长正文）物化成 ORM 对象，一年几千条记录时
    # 既慢又占内存，而这里 90% 的字段根本用不到。
    # updated_at 是 UTC，本地日期要多留两天余量再交给 Python 精确判断。
    utc_start = datetime.combine(start - timedelta(days=2), time.min)
    with session_scope() as session:
        if want("todo"):
            rows = session.execute(
                select(
                    Todo.id, Todo.title, Todo.due_date, Todo.due_time,
                    Todo.completed, Todo.priority, Todo.tags, Todo.created_at,
                ).where(
                    Todo.is_deleted.is_(False),
                    Todo.is_template.is_(False),
                    or_(
                        Todo.due_date.between(start, end),
                        and_(
                            Todo.due_date.is_(None),
                            Todo.created_at >= utc_start,
                        ),
                    ),
                )
            )
            for row in rows:
                day = row.due_date or local_date_from_utc(row.created_at)
                if start <= day <= end:
                    items.append(
                        TimelineItem(
                            "todo", "todos", row.id, day, row.title,
                            ("已完成" if row.completed else "进行中")
                            + (f" · {row.priority}" if row.priority else ""),
                            _tags(row.tags) + ([row.priority] if row.priority else []),
                            row.due_time or "",
                        )
                    )
        if want("diary"):
            rows = session.execute(
                select(Diary.id, Diary.date, Diary.title, Diary.content).where(
                    Diary.is_deleted.is_(False), Diary.date.between(start, end)
                )
            )
            for row in rows:
                items.append(
                    TimelineItem(
                        "diary", "diaries", row.id, row.date, row.title,
                        _clip(row.content, 80),
                    )
                )
        if want("notes"):
            rows = session.execute(
                select(Note.id, Note.title, Note.content, Note.tags, Note.updated_at).where(
                    Note.is_deleted.is_(False), Note.updated_at >= utc_start
                )
            )
            for row in rows:
                day = local_date_from_utc(row.updated_at)
                if start <= day <= end:
                    items.append(
                        TimelineItem(
                            "notes", "notes", row.id, day, row.title or "未命名笔记",
                            _clip(row.content, 80), _tags(row.tags),
                        )
                    )
        if want("exercise"):
            rows = session.execute(
                select(
                    Exercise.id, Exercise.date, Exercise.kind, Exercise.minutes,
                    Exercise.calories, Exercise.note,
                ).where(Exercise.is_deleted.is_(False), Exercise.date.between(start, end))
            )
            for row in rows:
                items.append(
                    TimelineItem(
                        "exercise", "exercises", row.id, row.date,
                        f"{row.kind} {int(row.minutes or 0)} 分钟",
                        f"{round(float(row.calories or 0))} 千卡"
                        + (f" · {row.note}" if row.note else ""),
                        [row.kind],
                    )
                )
        if want("diet"):
            rows = session.execute(
                select(Diet.id, Diet.date, Diet.meal, Diet.food, Diet.calories).where(
                    Diet.is_deleted.is_(False), Diet.date.between(start, end)
                )
            )
            for row in rows:
                items.append(
                    TimelineItem(
                        "diet", "diets", row.id, row.date,
                        f"{row.meal} · {row.food}",
                        f"{round(float(row.calories or 0))} 千卡", [row.meal],
                    )
                )
        if want("weight"):
            rows = session.execute(
                select(Weight.id, Weight.date, Weight.kg, Weight.body_fat).where(
                    Weight.is_deleted.is_(False), Weight.date.between(start, end)
                )
            )
            for row in rows:
                items.append(
                    TimelineItem(
                        "weight", "weights", row.id, row.date, f"体重 {float(row.kg):g} kg",
                        f"体脂 {float(row.body_fat):g}%" if row.body_fat else "",
                    )
                )
        if want("sleep"):
            rows = session.execute(
                select(
                    Sleep.id, Sleep.date, Sleep.hours, Sleep.bedtime,
                    Sleep.wake_time, Sleep.quality,
                ).where(Sleep.is_deleted.is_(False), Sleep.date.between(start, end))
            )
            for row in rows:
                items.append(
                    TimelineItem(
                        "sleep", "sleeps", row.id, row.date,
                        f"睡眠 {float(row.hours or 0):g} 小时",
                        f"{row.bedtime} → {row.wake_time} · 质量 {row.quality}/5",
                    )
                )
        if want("fun"):
            rows = session.execute(
                select(
                    Entertainment.id, Entertainment.title, Entertainment.kind,
                    Entertainment.status, Entertainment.updated_at,
                ).where(
                    Entertainment.is_deleted.is_(False),
                    Entertainment.updated_at >= utc_start,
                )
            )
            for row in rows:
                day = local_date_from_utc(row.updated_at)
                if start <= day <= end:
                    items.append(
                        TimelineItem(
                            "fun", "entertainments", row.id, day, row.title,
                            f"{row.kind} · {row.status}", [row.kind, row.status],
                        )
                    )
        if want("finance"):
            rows = session.execute(
                select(
                    Transaction.id, Transaction.date, Transaction.kind,
                    Transaction.category, Transaction.amount, Transaction.note,
                ).where(Transaction.is_deleted.is_(False), Transaction.date.between(start, end))
            )
            for row in rows:
                sign = "+" if row.kind == "income" else "-"
                items.append(
                    TimelineItem(
                        "finance", "transactions", row.id, row.date,
                        f"{row.category or '未分类'} {sign}¥{float(row.amount or 0):,.2f}",
                        _clip(row.note, 50), [row.category] if row.category else [],
                    )
                )
        if want("habit"):
            habits = {
                row.id: row.name
                for row in session.execute(
                    select(Habit.id, Habit.name).where(Habit.is_deleted.is_(False))
                )
            }
            logs = session.execute(
                select(HabitLog.id, HabitLog.habit_id, HabitLog.date, HabitLog.note).where(
                    HabitLog.is_deleted.is_(False),
                    HabitLog.done.is_(True),
                    HabitLog.date.between(start, end),
                )
            )
            for row in logs:
                name = habits.get(row.habit_id)
                if not name:
                    continue
                items.append(
                    TimelineItem(
                        "habit", "habit_logs", row.id, row.date,
                        f"打卡：{name}", row.note or "", [name],
                    )
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
        # 只要 tags / kind / status / name 这几列：聚合标签不需要正文。
        for row in session.execute(
            select(Todo.tags).where(
                Todo.is_deleted.is_(False), Todo.created_at >= datetime.combine(start, time.min)
            )
        ):
            for tag in _tags(row.tags):
                counts[tag] += 1
                sources[tag].add("任务")
        for row in session.execute(select(Note.tags).where(Note.is_deleted.is_(False))):
            for tag in _tags(row.tags):
                counts[tag] += 1
                sources[tag].add("笔记")
        for row in session.execute(
            select(Entertainment.kind, Entertainment.status).where(
                Entertainment.is_deleted.is_(False)
            )
        ):
            for tag in (row.kind, row.status):
                if tag:
                    counts[tag] += 1
                    sources[tag].add("娱乐")
        for row in session.execute(select(Habit.name).where(Habit.is_deleted.is_(False))):
            counts[row.name] += 0
            sources[row.name].add("习惯")
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
            # 只取 id 与标题列：候选列表不需要正文，也顺手消掉了逐行 record_title
            # 的 N+1 查询（每行一次 session.get）。
            stmt = select(model.id, column).where(model.is_deleted.is_(False))
            if keyword:
                stmt = stmt.where(column.contains(keyword, autoescape=True))
            if table == "diaries":
                stmt = stmt.order_by(Diary.date.desc())
            else:
                stmt = stmt.order_by(model.updated_at.desc())
            for row in session.execute(stmt.limit(limit)):
                result.append(
                    {
                        "table": table,
                        "record_id": row.id,
                        "title": str(row[1] or "") or table_label(table),
                        "label": table_label(table),
                    }
                )
    return result[:limit]
