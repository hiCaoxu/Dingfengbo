"""AI 草稿写回：解析模型输出的结构化草稿 → 用户确认 → 走正常写入路径。

安全边界：模型永远不直接写库。这里只负责「解析 + 校验 + 生成待确认草稿」，
真正的落库由用户在预览弹窗点「保存」后调用 :func:`save`，与手工录入共用
``repo.create``，因此写入后会自动进入同步队列，并可整体撤销。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from ...db.models import Account, Entertainment, Note, Todo, Transaction, table_label
from ...db.repo import create
from ...db.session import session_scope
from ..expense_rules import suggest_category

logger = logging.getLogger(__name__)

# 允许 AI 产出的草稿类型 → 中文名
DRAFT_TYPES = {
    "todo": "任务",
    "note": "笔记",
    "transaction": "收支",
    "entertainment": "娱乐",
}

_TABLE_OF_TYPE = {
    "todo": "todos",
    "note": "notes",
    "transaction": "transactions",
    "entertainment": "entertainments",
}

_PRIORITIES = {"high", "medium", "low", "高", "中", "低"}


@dataclass
class Draft:
    """一条待确认草稿。"""

    type: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def table(self) -> str:
        return _TABLE_OF_TYPE.get(self.type, self.type)

    @property
    def label(self) -> str:
        return DRAFT_TYPES.get(self.type, self.type)

    @property
    def title(self) -> str:
        if self.type == "transaction":
            sign = "+" if self.fields.get("kind") == "income" else "-"
            return f"{self.fields.get('category') or '未分类'} {sign}¥{float(self.fields.get('amount') or 0):,.2f}"
        return str(self.fields.get("title") or self.fields.get("name") or "（无标题）")

    @property
    def valid(self) -> bool:
        return not self.reason


def _json_blocks(text: str) -> list[Any]:
    """从模型回复中取出所有 JSON 对象（优先 ```json 代码块）。"""
    payloads: list[Any] = []
    for match in re.finditer(r"```(?:json)?\s*(.+?)```", text or "", re.S):
        raw = match.group(1).strip()
        try:
            payloads.append(json.loads(raw))
        except ValueError:
            continue
    if payloads:
        return payloads
    stripped = (text or "").strip()
    if stripped.startswith(("{", "[")):
        try:
            return [json.loads(stripped)]
        except ValueError:
            return []
    # 兜底：找到第一个 { 到最后一个 } 的片段
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        try:
            return [json.loads(stripped[start:end + 1])]
        except ValueError:
            return []
    return []


def _flatten(payloads: list[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for payload in payloads:
        if isinstance(payload, dict) and isinstance(payload.get("drafts"), list):
            items.extend(item for item in payload["drafts"] if isinstance(item, dict))
        elif isinstance(payload, list):
            items.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            items.append(payload)
    return items


def _parse_date(value: Any, today: date) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text in ("今天", "today"):
        return today
    if text in ("明天", "tomorrow"):
        return today + timedelta(days=1)
    if text in ("后天",):
        return today + timedelta(days=2)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _tags(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = [part.strip() for part in re.split(r"[,，、]", str(value or "")) if part.strip()]
    return json.dumps(items, ensure_ascii=False)


def _normalize(item: dict[str, Any], today: date) -> Draft:
    kind = str(item.get("type") or item.get("table") or "").strip().lower()
    kind = {
        "todos": "todo",
        "notes": "note",
        "transactions": "transaction",
        "transaction": "transaction",
        "entertainments": "entertainment",
    }.get(kind, kind)
    if kind not in DRAFT_TYPES:
        return Draft(type=kind or "unknown", raw=item, reason=f"不支持的草稿类型：{kind or '空'}")

    if kind == "todo":
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            return Draft(kind, raw=item, reason="缺少标题")
        priority = str(item.get("priority") or "").strip()
        priority = {"高": "high", "中": "medium", "低": "low"}.get(priority, priority)
        if priority and priority not in _PRIORITIES:
            priority = ""
        due = _parse_date(item.get("due_date") or item.get("date"), today)
        fields = {
            "title": title[:200],
            "note": str(item.get("note") or "")[:2000],
            "due_date": due,
            "due_time": str(item.get("due_time") or "")[:5],
            "priority": priority,
            "tags": _tags(item.get("tags")),
            "completed": False,
        }
        return Draft(kind, fields=fields, raw=item)

    if kind == "note":
        title = str(item.get("title") or "").strip() or "AI 笔记"
        content = str(item.get("content") or item.get("body") or "").strip()
        if not content:
            return Draft(kind, raw=item, reason="缺少正文")
        return Draft(
            kind,
            fields={"title": title[:200], "content": content, "tags": _tags(item.get("tags"))},
            raw=item,
        )

    if kind == "transaction":
        try:
            amount = round(float(item.get("amount") or 0), 2)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            return Draft(kind, raw=item, reason="金额无效")
        tx_kind = str(item.get("kind") or "expense").strip().lower()
        tx_kind = {"支出": "expense", "收入": "income", "expense": "expense", "income": "income"}.get(
            tx_kind, "expense"
        )
        category = str(item.get("category") or "").strip() or suggest_category(
            str(item.get("note") or "")
        )
        fields = {
            "kind": tx_kind,
            "amount": amount,
            "category": category[:40],
            "date": _parse_date(item.get("date"), today) or today,
            "note": str(item.get("note") or "")[:2000],
            "account_id": str(item.get("account_id") or ""),
        }
        return Draft(kind, fields=fields, raw=item)

    # entertainment
    title = str(item.get("title") or "").strip()
    if not title:
        return Draft(kind, raw=item, reason="缺少标题")
    return Draft(
        kind,
        fields={
            "title": title[:200],
            "kind": str(item.get("kind") or "电影")[:24],
            "status": str(item.get("status") or "想看")[:16],
            "note": str(item.get("note") or "")[:2000],
            "cover": str(item.get("cover") or ""),
        },
        raw=item,
    )


def extract(text: str, today: date | None = None) -> list[Draft]:
    """从模型回复里解析草稿；解析不到返回空列表。"""
    today = today or date.today()
    items = _flatten(_json_blocks(text))
    drafts = [_normalize(item, today) for item in items]
    return [draft for draft in drafts if draft.type in DRAFT_TYPES]


def strip_json_blocks(text: str) -> str:
    """去掉回复里的 JSON 代码块，只留可读说明（草稿由按钮单独展示）。"""
    cleaned = re.sub(r"```(?:json)?\s*.+?```", "", text or "", flags=re.S)
    return cleaned.strip()


def describe(drafts: list[Draft]) -> str:
    return "、".join(f"{draft.label}「{draft.title}」" for draft in drafts)


def save(drafts: list[Draft]) -> list[dict[str, Any]]:
    """落库（只保存校验通过的草稿），返回 [{id, table, title}]，供撤销使用。"""
    saved: list[dict[str, Any]] = []
    with session_scope() as session:
        account = session.scalar(
            select(Account).where(Account.is_deleted.is_(False)).order_by(Account.created_at)
        )
        for draft in drafts:
            if not draft.valid:
                continue
            fields = dict(draft.fields)
            if draft.type == "todo":
                created = create(session, Todo, **fields)
            elif draft.type == "note":
                created = create(session, Note, **fields)
            elif draft.type == "transaction":
                account_id = fields.pop("account_id", "") or (account.id if account else "")
                if not account_id:
                    draft.reason = "没有可用账户，请先在记账里添加账户"
                    continue
                created = create(session, Transaction, account_id=account_id, **fields)
            elif draft.type == "entertainment":
                created = create(session, Entertainment, **fields)
            else:
                continue
            saved.append({"id": created.id, "table": draft.table, "title": draft.title})
    if saved:
        from ..sync import mark_pending_write

        mark_pending_write()
    return saved


def undo(saved: list[dict[str, Any]]) -> int:
    """撤销刚保存的草稿（软删除，仍走同步）。"""
    from ...db.models import MODEL_BY_TABLE
    from ...db.repo import soft_delete

    removed = 0
    with session_scope() as session:
        for item in saved:
            model = MODEL_BY_TABLE.get(item.get("table", ""))
            if model is None:
                continue
            obj = session.get(model, item.get("id"))
            if obj is not None:
                soft_delete(session, obj)
                removed += 1
    if removed:
        from ..sync import mark_pending_write

        mark_pending_write()
    return removed


def label_of(table_or_type: str) -> str:
    return table_label(_TABLE_OF_TYPE.get(table_or_type, table_or_type))
