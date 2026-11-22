"""AI 助手数据上下文：表定义、字段序列化、@引用解析与最近 N 条明细读取。"""

from __future__ import annotations

import re
from datetime import date, datetime
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
from ..dates import local_datetime_from_utc

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
# 兼容旧写法：任务表曾叫「待办」，用户输入 @待办 仍应命中。
TABLE_BY_LABEL.setdefault("待办", "todos")

# 每次引用最多读取的记录条数。
CONTEXT_LIMIT = 30
# 字符预算：笔记/日记正文没有长度上限，30 条全文足以超出模型上下文，
# 或者在 120s 超时前先把请求撑成几 MB。超出的部分截断并明确标注。
_ROW_MAX_CHARS = 320
_TOTAL_MAX_CHARS = 12000

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
        if column.name in ("created_at", "updated_at") and isinstance(value, datetime):
            # 库里存 UTC，给模型看的应该是本地时间，否则「上周写的笔记」会被判成更早。
            value = local_datetime_from_utc(value).strftime("%Y-%m-%d %H:%M")
        label = _FIELD_LABELS.get(column.name, column.name)
        parts.append(f"{label}={value}")
    text = "；".join(parts)
    if len(text) > _ROW_MAX_CHARS:
        return text[:_ROW_MAX_CHARS] + "…（已截断）"
    return text


def read_context(
    table_names: list[str], limit: int = CONTEXT_LIMIT, since: date | None = None
) -> str:
    """读取引用表最近 limit 条非删除记录，返回供 AI 阅读的文本上下文。

    ``since`` 用于把范围收窄到某个日期之后（例如「本月消费分析」只发本月流水）：
    不加限定的话，「本月」这类指令会拿到最近 30 条——可能是上周也可能是三个月前。
    """
    sections: list[str] = []
    total = 0
    truncated = False
    with session_scope() as session:
        for name in table_names:
            entry = TABLE_BY_NAME.get(name)
            if entry is None:
                continue
            label, model = entry
            if total >= _TOTAL_MAX_CHARS:
                truncated = True
                break
            stmt = select(model).where(model.is_deleted.is_(False))
            if since is not None:
                column = next(
                    (
                        getattr(model, field)
                        for field in ("date", "due_date")
                        if hasattr(model, field)
                    ),
                    None,
                )
                if column is not None:
                    stmt = stmt.where(column >= since)
            rows = list(session.scalars(stmt.order_by(model.updated_at.desc()).limit(limit)))
            body = "\n".join(f"- {serialize_row(r)}" for r in rows) if rows else "（暂无数据）"
            chunk = f"【{label}】\n{body}"
            if total + len(chunk) > _TOTAL_MAX_CHARS:
                sections.append(f"【{label}】\n（上下文已达长度上限，余下记录省略）")
                truncated = True
                break
            sections.append(chunk)
            total += len(chunk)
    text = "\n\n".join(sections)
    if truncated:
        text += "\n\n（上下文已按长度上限截断，只发送了最近的一部分记录）"
    return text


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
