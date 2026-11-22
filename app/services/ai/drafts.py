"""AI 草稿写回：解析模型输出的结构化草稿 → 用户确认 → 走正常写入路径。

安全边界：模型永远不直接写库。这里只负责「解析 + 校验 + 生成待确认草稿」，
真正的落库由用户在预览弹窗点「保存」后调用 :func:`save`，与手工录入共用
``repo.create``，因此写入后会自动进入同步队列，并可整体撤销。
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from ...db.models import Account, Entertainment, Note, Todo, Transaction, table_label
from ...db.repo import create
from ...db.session import session_scope
from ..covers import is_safe_remote_url
from ..expense_rules import suggest_category
from ..money import to_float

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
# 单条 AI 笔记正文上限：超过就明确拒绝（不截断），避免一次对话把几十万字写进库。
_MAX_NOTE_CONTENT = 20_000


@dataclass
class Draft:
    """一条待确认草稿。"""

    type: str
    fields: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    # 已经保存过：避免同一批草稿被重复点「保存为记录」写多份。
    saved: bool = False

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
        except ValueError as exc:
            # 不再静默丢弃：模型偶尔输出带注释/尾逗号的 JSON，留一条日志便于排查
            # （只记异常类型与长度，不记正文，避免把用户内容写进日志）。
            logger.warning("草稿 JSON 块解析失败（%s，长度 %d）", type(exc).__name__, len(raw))
            continue
    if payloads:
        return payloads
    stripped = (text or "").strip()
    if stripped.startswith(("{", "[")):
        try:
            return [json.loads(stripped)]
        except ValueError as exc:
            logger.warning("草稿 JSON 解析失败（%s，长度 %d）", type(exc).__name__, len(stripped))
            return []
    # 兜底：找到第一个 { 到最后一个 } 的片段
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        try:
            return [json.loads(stripped[start:end + 1])]
        except ValueError as exc:
            logger.warning("草稿 JSON 片段解析失败（%s）", type(exc).__name__)
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
    except ValueError as exc:
        logger.debug("_parse_date：忽略 %s", type(exc).__name__)
        return None


def _parse_time(value: Any) -> str:
    """草稿里的到期时间：只接受 HH:MM / H:MM，其余一律清空。

    模型输出可能带时区、秒或中文（"下午3点"），直接截前 5 个字符会写进 "下午3点"
    或 "15:00:00" 这类脏值，列表按文本排序、提醒解析都会跟着出错。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


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
            "due_time": _parse_time(item.get("due_time")),
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
        if len(content) > _MAX_NOTE_CONTENT:
            # 落库前明确拒绝，而不是截断后静默保存：用户确认的与最终写入的必须一致。
            return Draft(
                kind, raw=item,
                reason=f"正文过长（超过 {_MAX_NOTE_CONTENT} 字），请让 AI 精简后再试",
            )
        return Draft(
            kind,
            fields={"title": title[:200], "content": content, "tags": _tags(item.get("tags"))},
            raw=item,
        )

    if kind == "transaction":
        try:
            raw_amount = float(item.get("amount") or 0)
        except (TypeError, ValueError) as exc:
            logger.debug("_normalize：忽略 %s", type(exc).__name__)
            raw_amount = 0.0
        # NaN/Inf 能通过 float()，但 NaN 写库会触发 NOT NULL（SQLite 把 NaN 存成 NULL），
        # Inf 会污染后续所有 SUM/报表，必须在入口拦掉。
        if not math.isfinite(raw_amount):
            return Draft(kind, raw=item, reason="金额不是有限数值")
        amount = to_float(raw_amount)
        if amount <= 0:
            return Draft(kind, raw=item, reason="金额无效")
        if amount > 1_000_000_000:
            return Draft(kind, raw=item, reason="金额过大")
        raw_kind = str(item.get("kind") or "expense").strip().lower()
        tx_kind = {
            "支出": "expense", "收入": "income",
            "expense": "expense", "income": "income",
        }.get(raw_kind, "")
        if not tx_kind:
            # 以前未知类型（含模型幻觉出的 transfer）会静默变成支出，账目就错了。
            return Draft(kind, raw=item, reason=f"不支持的类型：{raw_kind}")
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
    # 封面会由列表页自动发起 GET：模型输出（可被 @引用里的提示注入诱导）不能
    # 直接把应用变成任意地址的请求器，非公网/非 http(s) 一律丢弃。
    cover = str(item.get("cover") or "")
    if cover and not is_safe_remote_url(cover):
        cover = ""
    return Draft(
        kind,
        fields={
            "title": title[:200],
            "kind": str(item.get("kind") or "电影")[:24],
            "status": str(item.get("status") or "想看")[:16],
            "note": str(item.get("note") or "")[:2000],
            "cover": cover,
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
    """落库（只保存校验通过的草稿），返回 [{id, table, title}]，供撤销使用。

    已保存过的草稿会被跳过并打上 ``saved`` 标记：同一个 AI 回复上重复点
    「保存为记录」不应该再插入第二份任务/笔记/交易。
    """
    saved: list[dict[str, Any]] = []
    written: list[Draft] = []
    with session_scope() as session:
        account = session.scalar(
            select(Account).where(Account.is_deleted.is_(False)).order_by(Account.created_at)
        )
        for draft in drafts:
            if not draft.valid or draft.saved:
                continue
            fields = dict(draft.fields)
            if draft.type == "todo":
                created = create(session, Todo, **fields)
            elif draft.type == "note":
                created = create(session, Note, **fields)
            elif draft.type == "transaction":
                account_id = str(fields.pop("account_id", "") or "")
                if account_id:
                    chosen = session.get(Account, account_id)
                    if chosen is None or chosen.is_deleted:
                        # 模型给的账户 id 可能不存在（无外键约束），直接落库会变成孤儿交易，
                        # 报表按账户聚合时静默算错。
                        draft.reason = "草稿指定的账户不存在，请手动选择账户后再保存"
                        continue
                else:
                    account_id = account.id if account else ""
                    if not account_id:
                        draft.reason = "没有可用账户，请先在记账里添加账户"
                        continue
                created = create(session, Transaction, account_id=account_id, **fields)
            elif draft.type == "entertainment":
                created = create(session, Entertainment, **fields)
            else:
                continue
            saved.append({"id": created.id, "table": draft.table, "title": draft.title})
            written.append(draft)
    # 提交成功后才置位：事务回滚时草稿仍应可再次保存。
    for draft in written:
        draft.saved = True
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
