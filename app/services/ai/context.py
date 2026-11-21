"""AI 助手数据上下文：表定义、字段序列化、@引用解析与最近 N 条明细读取。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import (
    TABLE_LABELS,
    Account,
    Diet,
    Diary,
    Entertainment,
    Exercise,
    Note,
    Sleep,
    Todo,
    Transaction,
    Weight,
    table_label,
)
from ...db.session import session_scope

# (表名, 中文名, 模型)；中文名统一取自 models.TABLE_LABELS，顺序即快捷指令展示顺序。
TABLES: list[tuple[str, str, Any]] = [
    (name, TABLE_LABELS[name], model)
    for name, model in (
        ("todos", Todo),
        ("diaries", Diary),
        ("notes", Note),
        ("exercises", Exercise),
        ("diets", Diet),
        ("weights", Weight),
        ("sleeps", Sleep),
        ("entertainments", Entertainment),
        ("accounts", Account),
        ("transactions", Transaction),
    )
]

TABLE_BY_NAME = {name: (label, model) for name, label, model in TABLES}
TABLE_BY_LABEL = {label: name for name, label, _model in TABLES}

# 每次引用最多读取的记录条数。
CONTEXT_LIMIT = 30

# 同步元数据字段：序列化时跳过，避免噪音。
_SKIP_FIELDS = {"id", "owner_id", "is_deleted", "sync_status", "sync_seq"}

# 字段中文标签（其余字段保留原名）。
_FIELD_LABELS = {
    "title": "标题", "note": "备注", "content": "内容", "tags": "标签",
    "date": "日期", "kind": "类型", "status": "状态", "name": "名称",
    "category": "分类", "amount": "金额", "priority": "优先级", "due_date": "到期",
    "completed": "已完成", "meal": "餐次", "food": "食物", "grams": "克",
    "calories": "热量", "minutes": "分钟", "kg": "体重kg", "body_fat": "体脂率",
    "bedtime": "入睡", "wake_time": "醒来", "hours": "时长", "quality": "质量",
    "rating": "评分", "initial_balance": "初始余额", "account_id": "账户",
    "to_account_id": "转入账户", "distance_km": "距离km", "lunar_reminder": "农历提醒",
    "repeat_rule": "重复", "is_template": "模板", "completed_at": "完成时间",
    "created_at": "创建时间", "updated_at": "更新时间", "cover": "封面",
}


def serialize_row(obj) -> str:
    """把一行 ORM 对象序列化为「字段=值」的可读文本。"""
    parts: list[str] = []
    for column in obj.__table__.columns:
        if column.name in _SKIP_FIELDS:
            continue
        value = getattr(obj, column.name)
        if value is None or value == "" or value == "[]":
            continue
        label = _FIELD_LABELS.get(column.name, column.name)
        parts.append(f"{label}={value}")
    return "；".join(parts)


def read_context(table_names: list[str], limit: int = CONTEXT_LIMIT) -> str:
    """读取引用表最近 limit 条非删除记录，返回供 AI 阅读的文本上下文。"""
    sections: list[str] = []
    with session_scope() as session:
        for name in table_names:
            entry = TABLE_BY_NAME.get(name)
            if entry is None:
                continue
            label, model = entry
            rows = list(
                session.scalars(
                    select(model)
                    .where(model.is_deleted.is_(False))
                    .order_by(model.updated_at.desc())
                    .limit(limit)
                )
            )
            body = "\n".join(f"- {serialize_row(r)}" for r in rows) if rows else "（暂无数据）"
            sections.append(f"【{label}】\n{body}")
    return "\n\n".join(sections)


def parse_refs(text: str) -> list[str]:
    """解析输入文本中的 @中文表名 引用，返回表名列表（去重、保持顺序）。"""
    refs: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"@([\u4e00-\u9fa5]+)", text):
        name = TABLE_BY_LABEL.get(match.group(1))
        if name and name not in seen:
            seen.add(name)
            refs.append(name)
    return refs


def get_label(name: str) -> str:
    return table_label(name)
