"""SQLAlchemy 2.x 模型：本地 SQLite 与云端共用。

每张可同步记录都带 created_at / updated_at / is_deleted / sync_status 四字段。
owner_id 用于服务端按用户隔离；单机模式下统一为 "local"。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    # 保持 naive UTC（与历史数据一致），改用非弃用写法。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_sync_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class SyncMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_sync_id)
    owner_id: Mapped[str] = mapped_column(String(64), default="local", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    sync_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)


class SyncSequence(Base):
    """服务端全局同步序列：单行 (id=1)，value 单调递增，用于原子分配 sync_seq。"""

    __tablename__ = "sync_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Todo(SyncMixin, Base):
    __tablename__ = "todos"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_time: Mapped[str] = mapped_column(String(5), default="")  # HH:MM，空=不限时间
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    lunar_reminder: Mapped[str] = mapped_column(String(32), default="")
    repeat_rule: Mapped[str] = mapped_column(String(16), default="none")  # none/day/week/month/year/irregular/custom
    repeat_config: Mapped[str] = mapped_column(Text, default="")  # custom 规则的 JSON 参数
    priority: Mapped[str] = mapped_column(String(16), default="")  # '', low, medium, high
    # 提前提醒：-1=不提醒，0=到期当天，N=提前 N 天；remind_time 为空则用 due_time/09:00
    remind_days_before: Mapped[int] = mapped_column(Integer, default=-1)
    remind_time: Mapped[str] = mapped_column(String(5), default="")
    pomodoro_count: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)


class Diary(SyncMixin, Base):
    __tablename__ = "diaries"
    __table_args__ = (UniqueConstraint("owner_id", "date", name="uq_diary_owner_date"),)

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")


class Note(SyncMixin, Base):
    __tablename__ = "notes"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="[]")


class Exercise(SyncMixin, Base):
    __tablename__ = "exercises"

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="跑步")
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class Diet(SyncMixin, Base):
    __tablename__ = "diets"

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meal: Mapped[str] = mapped_column(String(16), default="早餐")
    food: Mapped[str] = mapped_column(String(120), nullable=False)
    grams: Mapped[float] = mapped_column(Float, default=100)
    calories: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class Weight(SyncMixin, Base):
    __tablename__ = "weights"

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kg: Mapped[float] = mapped_column(Float, nullable=False)
    body_fat: Mapped[float | None] = mapped_column(Float, nullable=True)


class Sleep(SyncMixin, Base):
    __tablename__ = "sleeps"

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    bedtime: Mapped[str] = mapped_column(String(8), default="23:00")
    wake_time: Mapped[str] = mapped_column(String(8), default="07:00")
    hours: Mapped[float] = mapped_column(Float, default=0)
    quality: Mapped[int] = mapped_column(Integer, default=3)


class Food(Base):
    """内置精简食物库，非用户同步数据。"""

    __tablename__ = "foods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), default="其他")
    kcal_per_100g: Mapped[float] = mapped_column(Float, default=0)


class Entertainment(SyncMixin, Base):
    __tablename__ = "entertainments"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="电影")
    status: Mapped[str] = mapped_column(String(16), default="想看")  # 想看/在看/看完
    cover: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Account(SyncMixin, Base):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="银行卡")
    initial_balance: Mapped[float] = mapped_column(Float, default=0)


class Transaction(SyncMixin, Base):
    __tablename__ = "transactions"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # income/expense/transfer
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="")
    account_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    to_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, default="")


class SyncIssue(Base):
    """被服务端拒收的同步记录（本地辅助表，不参与同步）。

    只保存「最近一次拒收原因」，供设置页展示明细与「全部重试」使用；
    记录重新上传成功（``mark_clean`` 命中）或用户重试后即被删除。
    """

    __tablename__ = "sync_issues"

    table_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class TodoReminder(Base):
    """待办提醒的去重记录（本地辅助表，不参与同步）。

    ``(todo_id, fire_at)`` 为主键：同一次提醒只推一次系统通知；待办被修改或
    重复规则推进后 ``fire_at`` 变化，自然形成新的提醒。
    """

    __tablename__ = "todo_reminders"

    todo_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fire_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


SYNC_MODELS = (
    Todo,
    Diary,
    Note,
    Exercise,
    Diet,
    Weight,
    Sleep,
    Entertainment,
    Account,
    Transaction,
)

MODEL_BY_TABLE = {m.__tablename__: m for m in SYNC_MODELS}

# 表名 → 中文名：界面展示与 AI 上下文共用同一份，避免两处各自维护而漂移。
TABLE_LABELS = {
    "todos": "待办",
    "diaries": "日记",
    "notes": "笔记",
    "exercises": "锻炼",
    "diets": "饮食",
    "weights": "体重",
    "sleeps": "睡眠",
    "entertainments": "娱乐",
    "accounts": "账户",
    "transactions": "收支",
}


def table_label(name: str) -> str:
    return TABLE_LABELS.get(name, name)
