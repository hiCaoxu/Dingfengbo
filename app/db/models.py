"""SQLAlchemy 2.x 模型：本地 SQLite 与云端共用。

每张可同步记录都带 created_at / updated_at / is_deleted / sync_status 四字段。
owner_id 用于服务端按用户隔离；单机模式下统一为 "local"。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class SyncMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), default="local", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)


class Todo(SyncMixin, Base):
    __tablename__ = "todos"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    lunar_reminder: Mapped[str] = mapped_column(String(32), default="")
    repeat_rule: Mapped[str] = mapped_column(String(16), default="none")  # none/day/week/month/year
    priority: Mapped[str] = mapped_column(String(16), default="")  # '', low, medium, high
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
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    to_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, default="")


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
