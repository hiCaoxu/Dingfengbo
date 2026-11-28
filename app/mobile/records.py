"""手机端录入与阅读。只调用仓储和已有计算，不碰桌面界面。"""

from __future__ import annotations

import math
from datetime import date

from sqlalchemy import select

from ..db.models import (
    Account,
    Diary,
    Diet,
    Exercise,
    Note,
    Sleep,
    Todo,
    Transaction,
    Weight,
    utcnow,
)
from ..db.repo import create, update
from ..db.session import session_scope
from ..services.dates import diary_title, parse_hours, parse_relative_day
from ..services.diary import ensure_diary
from ..services.health import calorie_of, estimate_exercise_calories, latest_weight_kg
from ..services.sync import mark_pending_write
from ..services.todo_views import normalize_priority, priority_label

EXERCISE_KINDS = ("跑步", "快走", "骑行", "游泳", "力量训练", "瑜伽", "跳绳", "爬楼")
MEALS = ("早餐", "午餐", "晚餐", "加餐")
EXPENSE_CATEGORIES = ("餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "通讯", "人情", "其他")
INCOME_CATEGORIES = ("工资", "奖金", "理财", "兼职", "礼金", "报销", "其他")
PRIORITIES = (("", "无"), ("low", "低"), ("medium", "中"), ("high", "高"))


class RecordError(ValueError):
    """录入不能保存。消息可以直接显示给用户。"""


def open_todos() -> list[dict]:
    """未完成任务：置顶在前，有日期的排在没日期的前面。"""
    with session_scope() as session:
        rows = session.scalars(
            select(Todo)
            .where(Todo.owner_id == "local", Todo.is_deleted.is_(False), Todo.completed.is_(False))
            .order_by(Todo.pinned.desc(), Todo.due_date.asc().nulls_last(), Todo.title)
        ).all()
        return [
            {
                "id": row.id,
                "title": row.title,
                "due_date": row.due_date.isoformat() if row.due_date else "",
                "priority": normalize_priority(row.priority),
                "priority_label": priority_label(row.priority),
                "pinned": bool(row.pinned),
            }
            for row in rows
        ]


def create_todo(title: str, due: date | None, priority: str) -> str:
    text = (title or "").strip()
    if not text:
        raise RecordError("请填写标题")
    with session_scope() as session:
        row = create(
            session,
            Todo,
            title=text,
            due_date=due,
            priority=normalize_priority(priority),
            quadrant="",
            pomodoro_count=0,
            repeat_rule="none",
        )
        record_id = row.id
    mark_pending_write()
    return record_id


def update_todo(todo_id: str, title: str, due: date | None, priority: str) -> None:
    text = (title or "").strip()
    if not text:
        raise RecordError("请填写标题")
    with session_scope() as session:
        row = session.get(Todo, todo_id)
        if row is None or row.is_deleted:
            raise RecordError("这条任务已经不在了")
        update(
            session,
            row,
            title=text,
            due_date=due,
            priority=normalize_priority(priority),
        )
    mark_pending_write()


def complete_todo(todo_id: str) -> None:
    with session_scope() as session:
        row = session.get(Todo, todo_id)
        if row is None or row.is_deleted or row.completed:
            return
        update(session, row, completed=True, completed_at=utcnow())
    mark_pending_write()


def load_diary(day: date) -> dict:
    """读某一天的日记。没有则只返回草稿，不建行。"""
    with session_scope() as session:
        row = session.scalar(
            select(Diary).where(
                Diary.owner_id == "local",
                Diary.date == day,
                Diary.is_deleted.is_(False),
            )
        )
        if row is None:
            return {"id": "", "title": diary_title(day), "content": ""}
        return {"id": row.id, "title": row.title, "content": row.content or ""}


def save_diary(content: str, day: date, diary_id: str = "") -> str:
    """有正文才建当天日记；空内容且当天还没有日记时不落库。"""
    text = content or ""
    written = False
    with session_scope() as session:
        item = session.get(Diary, diary_id) if diary_id else None
        if item is not None and item.is_deleted:
            item = None
        if item is None and text.strip():
            item = ensure_diary(session, day)
        if item is not None:
            update(session, item, content=text)
            diary_id = str(item.id)
            written = True
    if written:
        mark_pending_write()
    return diary_id or ""


def list_notes() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(
            select(Note)
            .where(Note.owner_id == "local", Note.is_deleted.is_(False))
            .order_by(Note.updated_at.desc())
        ).all()
        return [{"id": row.id, "title": row.title or "无标题"} for row in rows]


def read_note(note_id: str) -> dict | None:
    with session_scope() as session:
        row = session.get(Note, note_id)
        if row is None or row.is_deleted:
            return None
        return {"id": row.id, "title": row.title or "无标题", "content": row.content or ""}


def save_exercise(
    day: date,
    kind: str,
    minutes: float,
    distance_km: float | None,
    calories: float | None,
    note: str,
) -> str:
    if kind not in EXERCISE_KINDS:
        raise RecordError("请选择运动类型")
    mins = _required_number(minutes, "时长（分钟）", 1, 24 * 60)
    distance = _optional_number(distance_km, "距离", 0, 1000)
    burned = _optional_number(calories, "消耗热量", 0, 100_000)
    with session_scope() as session:
        if burned is None:
            burned = estimate_exercise_calories(kind, int(mins), latest_weight_kg(session))
        row = create(
            session,
            Exercise,
            date=day,
            kind=kind,
            minutes=int(mins),
            distance_km=distance,
            calories=burned,
            note=(note or "").strip(),
        )
        record_id = row.id
    mark_pending_write()
    return record_id


def save_diet(
    day: date,
    meal: str,
    food: str,
    grams: float,
    calories: float | None,
    note: str,
) -> str:
    name = (food or "").strip()
    if not name:
        raise RecordError("请填写食物")
    if meal not in MEALS:
        raise RecordError("请选择餐次")
    weight = _required_number(grams, "重量", 1, 10_000)
    kcal = _optional_number(calories, "热量", 0, 100_000)
    with session_scope() as session:
        if kcal is None:
            kcal = calorie_of(session, name, weight)
        row = create(
            session,
            Diet,
            date=day,
            meal=meal,
            food=name,
            grams=weight,
            calories=kcal,
            note=(note or "").strip(),
        )
        record_id = row.id
    mark_pending_write()
    return record_id


def save_weight(
    day: date,
    kg: float,
    waist_cm: float | None,
    body_fat: float | None,
) -> str:
    weight = _required_number(kg, "体重", 1, 500)
    waist = _optional_number(waist_cm, "腰围", 30, 200)
    fat = _optional_number(body_fat, "体脂率", 0, 100)
    with session_scope() as session:
        row = create(
            session,
            Weight,
            date=day,
            kg=weight,
            waist_cm=waist,
            body_fat=fat,
        )
        record_id = row.id
    mark_pending_write()
    return record_id


def save_sleep(day: date, bedtime: str, wake_time: str, quality: int) -> str:
    hours = parse_hours((bedtime or "").strip(), (wake_time or "").strip())
    if hours == 0:
        raise RecordError("时间格式应为 HH:MM")
    try:
        score = int(quality)
    except (TypeError, ValueError) as exc:
        raise RecordError("请选择睡眠质量") from exc
    if score < 1 or score > 5:
        raise RecordError("睡眠质量为 1 到 5")
    fields = {
        "date": day,
        "bedtime": bedtime.strip(),
        "wake_time": wake_time.strip(),
        "hours": hours,
        "quality": score,
    }
    with session_scope() as session:
        existing = session.scalar(
            select(Sleep).where(Sleep.is_deleted.is_(False), Sleep.date == day)
        )
        if existing is None:
            record_id = create(session, Sleep, **fields).id
        else:
            update(session, existing, **fields)
            record_id = existing.id
    mark_pending_write()
    return record_id


def list_accounts() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(
            select(Account)
            .where(Account.owner_id == "local", Account.is_deleted.is_(False))
            .order_by(Account.name)
        ).all()
        return [{"id": row.id, "name": row.name} for row in rows]


def save_transaction(
    kind: str,
    amount: float,
    category: str,
    account_id: str,
    day: date,
    note: str,
) -> str:
    if kind not in ("income", "expense"):
        raise RecordError("请选择收入或支出")
    accounts = list_accounts()
    if not accounts:
        raise RecordError("请先在电脑上建一个账户，同步后再记一笔")
    if account_id not in {item["id"] for item in accounts}:
        raise RecordError("请选择账户")
    allowed = INCOME_CATEGORIES if kind == "income" else EXPENSE_CATEGORIES
    if category not in allowed:
        raise RecordError("请选择分类")
    yuan = _required_number(amount, "金额", 0.01, 100_000_000)
    with session_scope() as session:
        row = create(
            session,
            Transaction,
            kind=kind,
            amount=yuan,
            category=category,
            account_id=account_id,
            date=day,
            note=(note or "").strip(),
        )
        record_id = row.id
    mark_pending_write()
    return record_id


def parse_day(raw: str | None) -> date:
    text = (raw or "").strip()
    if not text:
        return date.today()
    relative = parse_relative_day(text)
    if relative is not None:
        return relative
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RecordError("日期格式应为 YYYY-MM-DD") from exc


def _required_number(value: object, label: str, minimum: float, maximum: float) -> float:
    number = _optional_number(value, label, minimum, maximum)
    if number is None:
        raise RecordError(f"请填写{label}")
    return number


def _optional_number(value: object, label: str, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError as exc:
            raise RecordError(f"{label}格式不正确") from exc
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RecordError(f"{label}格式不正确") from exc
    if not math.isfinite(number):
        raise RecordError(f"{label}必须是有限数值")
    if number < minimum or number > maximum:
        raise RecordError(f"{label}超出范围")
    return number
