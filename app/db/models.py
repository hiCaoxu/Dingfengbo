"""SQLAlchemy 2.x 模型：本地 SQLite 与云端共用。

每张可同步记录都带 created_at / updated_at / is_deleted / sync_status 四字段。
owner_id 用于服务端按用户隔离；单机模式下统一为 "local"。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..services.money import cents as to_cents
from ..services.money import from_cents

logger = logging.getLogger(__name__)

class MoneyCents(TypeDecorator):
    """金额列：库里存**整数分**，ORM 层仍暴露「元」的 float。

    - 写：元 → 整数分（ROUND_HALF_UP），库里只有整数，SUM 精确；
    - 读：整数分 → 元，业务代码与同步载荷继续用元，旧客户端不受影响。

    这一点很关键：同步协议（JSON 载荷、CSV 导出）仍然是「元」的浮点数。若把协议
    也改成整数分，老版本客户端推送的 12.34 会被新端当成分读成 ¥0.12，跨版本数据
    直接写坏。换算只发生在数据库边界。
    """

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else to_cents(value)

    def process_result_value(self, value, dialect):
        return None if value is None else float(from_cents(value))

    @property
    def python_type(self):
        # 供 repo._parse / data_io._coerce 判断入站值类型：必须按「元」的 float 处理；
        # 若沿用 impl 的 int，12.34 会先被截成 12 再乘 100。
        return float


def utcnow() -> datetime:
    # 保持 naive UTC（与历史数据一致），改用非弃用写法。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_sync_id() -> str:
    return str(uuid4())


def column_fill_value(column) -> Any:
    """非空列在「缺值 / 收到 NULL」时的兜底值。

    模型里 ``Mapped[str]`` 的列在新建库上是 NOT NULL，但历史云端库是由更早的模型
    建出来的，这些列可以是 NULL（SQLite 的 ``ALTER TABLE ADD COLUMN`` 也无法补
    NOT NULL）。同步载荷把 NULL 带进来时，写进本地非空列会直接 IntegrityError，
    因此迁移回填与入站解析都要按「模型默认值 → 类型零值」的顺序取兜底。
    """
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False):
        argument = getattr(default, "arg", None)
        if argument is not None and not callable(argument):
            return argument
    try:
        python_type = column.type.python_type
    except (NotImplementedError, AttributeError) as exc:
        logger.debug("column_fill_value：忽略 %s", type(exc).__name__)
        python_type = str
    if python_type is bool:
        return False
    if python_type in (int, float):
        return 0
    if python_type is str:
        return ""
    if python_type is datetime:
        return utcnow()
    if python_type is date:
        return date.today()
    return ""


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


class PurgeMark(Base):
    """本地辅助表（不参与同步）：墓碑物理清理的删除高水位。

    墓碑被物理清理后，「这条记录曾经存在、且已被删除」的知识就彻底消失了：离线超过
    保留窗口的设备再来推送时，服务端看到的是一个「不存在的 id」，会把它当成新记录
    插回去——已删除的数据被复活。记下清理时的截止时间后，服务端可以拒绝这类
    「比墓碑还老」的推送，要求对方先全量重拉。``owner_id="*"`` 表示全局一条。
    """

    __tablename__ = "purge_marks"

    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    purged_before: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    purged_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    purged_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


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
    # 四象限：空=按优先级/到期自动推导，q1..q4=用户在编辑里手选
    quadrant: Mapped[str] = mapped_column(String(4), default="")
    # 提前提醒：可多选，remind_days 为 JSON 数组如 [1,3,7]（0=到期当天，空=不提醒）
    # remind_days_before 保留为旧版单值字段（新版写最大值，便于旧端理解）
    remind_days: Mapped[str] = mapped_column(Text, default="[]")
    remind_days_before: Mapped[int] = mapped_column(Integer, default=-1)
    remind_time: Mapped[str] = mapped_column(String(5), default="")
    pomodoro_count: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)


class Diary(SyncMixin, Base):
    """日记：每天一篇（软删除后允许同日重建）。

    约束必须是**部分唯一索引**（``WHERE is_deleted = 0``），不能是全表
    ``UniqueConstraint``：删除后墓碑仍占着 ``(owner_id, date)``，而 ``ensure_diary``
    只查活跃行，在墓碑被物理清理前同日重建必然撞唯一约束。
    """

    __tablename__ = "diaries"
    __table_args__ = (
        Index(
            "uq_diaries_owner_date_active",
            "owner_id",
            "date",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
        ),
    )

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
    initial_balance: Mapped[float] = mapped_column(MoneyCents, default=0)


class Transaction(SyncMixin, Base):
    __tablename__ = "transactions"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # income/expense/transfer
    amount: Mapped[float] = mapped_column(MoneyCents, nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="")
    account_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    to_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Budget(SyncMixin, Base):
    """预算：``category`` 为空表示月度总预算，否则为分类预算。"""

    __tablename__ = "budgets"

    month: Mapped[str] = mapped_column(String(7), default="", index=True)  # YYYY-MM
    category: Mapped[str] = mapped_column(String(40), default="")
    amount: Mapped[float] = mapped_column(MoneyCents, default=0)
    note: Mapped[str] = mapped_column(Text, default="")


class RecurringRule(SyncMixin, Base):
    """周期记账规则：固定支出/订阅，到期自动生成交易草稿。"""

    __tablename__ = "recurring_rules"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="expense")  # income/expense
    amount: Mapped[float] = mapped_column(MoneyCents, default=0)
    category: Mapped[str] = mapped_column(String(40), default="")
    account_id: Mapped[str] = mapped_column(String(36), default="")
    period: Mapped[str] = mapped_column(String(8), default="month")  # month/week
    day: Mapped[int] = mapped_column(Integer, default=1)  # 月内日号（1-31）或周内（1=周一）
    next_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    last_generated: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")


class Habit(SyncMixin, Base):
    """习惯定义：每周目标次数 + 单次目标分钟数。"""

    __tablename__ = "habits"

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    target_per_week: Mapped[int] = mapped_column(Integer, default=7)
    target_minutes: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class HabitLog(SyncMixin, Base):
    """习惯打卡记录：同一天同一习惯只保留一条（由服务层 upsert 保证）。

    唯一索引只约束未删除行（部分索引）：并发 push 或历史脏数据可能在库里留下
    重复的墓碑行，全表唯一会让迁移直接失败；而「同一天只保留一条活跃记录」才是
    业务承诺。旧库的重复行在 migrations._ensure_indexes 里收敛。
    """

    __tablename__ = "habit_logs"
    __table_args__ = (
        Index(
            "uq_habit_logs_active",
            "owner_id",
            "habit_id",
            "date",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
        ),
    )

    habit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    done: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")


class RecordLink(SyncMixin, Base):
    """跨模块关联：把一条记录指向另一条记录（如笔记关联待办）。"""

    __tablename__ = "record_links"

    from_table: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    from_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    to_table: Mapped[str] = mapped_column(String(32), nullable=False)
    to_id: Mapped[str] = mapped_column(String(36), nullable=False)
    label: Mapped[str] = mapped_column(String(60), default="")


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


class SyncConflict(Base):
    """同步冲突记录（本地辅助表，不参与同步）。

    LWW 合并时远端版本更旧、本次拉取被丢弃，就记一条，供「同步明细 → 冲突」
    展示与人工核对；同一记录只保留最近一次，避免长期使用时无限膨胀。
    """

    __tablename__ = "sync_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    local_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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


class LifeEvent(SyncMixin, Base):
    """人生大事：长期目标/里程碑清单，完成时间留痕，不设「到期」压力。"""

    __tablename__ = "life_events"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


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
    Budget,
    RecurringRule,
    Habit,
    HabitLog,
    RecordLink,
    LifeEvent,
)

MODEL_BY_TABLE = {m.__tablename__: m for m in SYNC_MODELS}

# 同步的两条高频查询都是 (owner_id, 单列) 组合：
#   pull:  WHERE owner_id=? AND sync_seq > ? ORDER BY sync_seq
#   queue: WHERE owner_id=? AND sync_status = ?
# 只有单列索引时 SQLite 会先按 sync_seq 扫全局、再过滤租户，多租户服务端等于
# 每张表都全表扫一遍。这里补上复合索引，新建库由 create_all 直接建。
for _model in SYNC_MODELS:
    Index(f"ix_{_model.__tablename__}_owner_sync_seq", _model.owner_id, _model.sync_seq)
    Index(
        f"ix_{_model.__tablename__}_owner_sync_status",
        _model.owner_id,
        _model.sync_status,
    )
del _model

# 表名 → 中文名：界面展示与 AI 上下文共用同一份，避免两处各自维护而漂移。
TABLE_LABELS = {
    "todos": "任务",
    "diaries": "日记",
    "notes": "笔记",
    "exercises": "锻炼",
    "diets": "饮食",
    "weights": "体重",
    "sleeps": "睡眠",
    "entertainments": "娱乐",
    "accounts": "账户",
    "transactions": "收支",
    "budgets": "预算",
    "recurring_rules": "固定支出",
    "habits": "习惯",
    "habit_logs": "打卡",
    "record_links": "关联",
    "life_events": "人生大事",
}


def table_label(name: str) -> str:
    return TABLE_LABELS.get(name, name)
