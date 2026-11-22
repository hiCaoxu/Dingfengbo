"""全局搜索：跨待办/笔记/日记/收支/娱乐的关键字检索 + 自然语言过滤。

命令面板（Ctrl+K）与时间轴共用这一层，避免把 SQL 分散到 UI 里。
检索走 LIKE 模糊匹配（``autoescape=True`` 防用户输入里的 %/_ 变成通配符），
结果按「标题命中 > 正文命中」与更新时间排序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from ..db.models import Diary, Entertainment, Note, Todo, Transaction
from ..db.session import session_scope

# 模块中文名 → 表名（自然语言过滤用）
MODULE_WORDS = {
    "待办": "todos",
    "任务": "todos",
    "笔记": "notes",
    "日记": "diaries",
    "收支": "transactions",
    "记账": "transactions",
    "交易": "transactions",
    "娱乐": "entertainments",
    "电影": "entertainments",
    "剧": "entertainments",
    "书": "entertainments",
}

PRIORITY_WORDS = {
    "高优先级": "high",
    "高优先": "high",
    "紧急": "high",
    "中优先级": "medium",
    "低优先级": "low",
}

# 表名 → 结果跳转的模块键
MODULE_OF_TABLE = {
    "todos": "todo",
    "notes": "notes",
    "diaries": "diary",
    "transactions": "finance",
    "entertainments": "fun",
}


@dataclass
class Query:
    """解析后的检索条件。"""

    keywords: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    priority: str | None = None
    since: date | None = None
    until: date | None = None
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.keywords or self.tables or self.priority or self.since or self.until)


@dataclass
class Hit:
    module: str
    table: str
    record_id: str
    title: str
    subtitle: str = ""
    snippet: str = ""
    date: date | None = None
    score: int = 0
    payload: Any = None

    @property
    def label(self) -> str:
        return {
            "todo": "任务",
            "notes": "笔记",
            "diary": "日记",
            "finance": "收支",
            "fun": "娱乐",
        }.get(self.module, self.module)


def _day_range(word: str, today: date) -> tuple[date, date] | None:
    if word == "今天":
        return today, today
    if word == "昨天":
        return today - timedelta(days=1), today - timedelta(days=1)
    if word == "明天":
        return today + timedelta(days=1), today + timedelta(days=1)
    if word == "本周":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if word == "上周":
        end = today - timedelta(days=today.weekday() + 1)
        return end - timedelta(days=6), end
    if word == "本月":
        start = today.replace(day=1)
        next_month = (start + timedelta(days=32)).replace(day=1)
        return start, next_month - timedelta(days=1)
    return None


# 关键字条数与单条长度上限：每条关键字都会多生成一组 LIKE 条件，
# 把一整段文章粘进搜索框会让 5 张表 × 多列的条件数暴涨（SQLite 也会撞参数上限）。
MAX_KEYWORDS = 8
MAX_KEYWORD_LEN = 64


def parse_query(text: str, today: date | None = None) -> Query:
    """把「待办 今天 高优先级 报告」这类输入拆成结构化条件。

    中文不分词：未知片段整体保留为关键字，因此「体检报告」不会被拆坏。
    """
    today = today or date.today()
    query = Query(raw=text or "")
    for token in (text or "").replace("，", " ").replace(",", " ").split():
        if token in MODULE_WORDS:
            table = MODULE_WORDS[token]
            if table not in query.tables:
                query.tables.append(table)
            continue
        if token in PRIORITY_WORDS:
            query.priority = PRIORITY_WORDS[token]
            continue
        span = _day_range(token, today)
        if span is not None:
            query.since, query.until = span
            continue
        if token.lower() in ("todo", "note", "diary"):
            table = {"todo": "todos", "note": "notes", "diary": "diaries"}[token.lower()]
            if table not in query.tables:
                query.tables.append(table)
            continue
        if len(query.keywords) < MAX_KEYWORDS:
            query.keywords.append(token[:MAX_KEYWORD_LEN])
    return query


def _snippet(text: str | None, keywords: list[str], size: int = 80) -> str:
    body = (text or "").replace("\n", " ").strip()
    if not body:
        return ""
    lowered = body.lower()
    for keyword in keywords:
        index = lowered.find(keyword.lower())
        if index >= 0:
            start = max(0, index - size // 3)
            piece = body[start:start + size]
            return ("…" if start else "") + piece + ("…" if start + size < len(body) else "")
    return body[:size] + ("…" if len(body) > size else "")


def _score(title: str, body: str, keywords: list[str]) -> int:
    if not keywords:
        return 1
    score = 0
    lowered_title = (title or "").lower()
    lowered_body = (body or "").lower()
    for keyword in keywords:
        needle = keyword.lower()
        if needle in lowered_title:
            score += 10
        if needle in lowered_body:
            score += 3
    return score


def _like(column, keyword: str):
    return column.contains(keyword, autoescape=True)


def _title_first(column, keywords: list[str]):
    """标题命中优先，下推到 SQL 的 ``ORDER BY``。

    每张表只取 ``limit * 3`` 行候选再在 Python 里按分数截断。候选窗口按时间排序时，
    正文命中但标题不命中的新记录会把标题命中的老记录挤出窗口——分数再高也看不到。
    这里先把标题命中的行排到前面，窗口才真的覆盖「最可能相关」的那批记录。
    """
    if not keywords:
        return None
    return case((or_(*[_like(column, k) for k in keywords]), 0), else_=1)


def _search_todos(session: Session, query: Query, limit: int) -> list[Hit]:
    stmt = select(Todo).where(Todo.is_deleted.is_(False), Todo.is_template.is_(False))
    if query.keywords:
        stmt = stmt.where(
            or_(*[_like(Todo.title, k) for k in query.keywords])
            | or_(*[_like(Todo.note, k) for k in query.keywords])
            | or_(*[_like(Todo.tags, k) for k in query.keywords])
        )
    if query.priority:
        stmt = stmt.where(Todo.priority == query.priority)
    if query.since:
        stmt = stmt.where(Todo.due_date >= query.since)
    if query.until:
        stmt = stmt.where(Todo.due_date <= query.until)
    order: list = [Todo.completed]
    title_first = _title_first(Todo.title, query.keywords)
    if title_first is not None:
        order.append(title_first)
    order += [Todo.due_date, Todo.updated_at.desc()]
    stmt = stmt.order_by(*order).limit(limit * 3)
    hits: list[Hit] = []
    for item in session.scalars(stmt):
        status = "已完成" if item.completed else ("有截止日" if item.due_date else "无截止日")
        due = item.due_date.isoformat() if item.due_date else ""
        hits.append(
            Hit(
                module="todo",
                table="todos",
                record_id=item.id,
                title=item.title,
                subtitle=" · ".join(x for x in (due, item.due_time, status) if x),
                snippet=_snippet(item.note or item.tags, query.keywords),
                date=item.due_date,
                score=_score(item.title, item.note or "", query.keywords) + (0 if item.completed else 1),
                payload=item,
            )
        )
    return hits


def _search_notes(session: Session, query: Query, limit: int) -> list[Hit]:
    stmt = select(Note).where(Note.is_deleted.is_(False))
    if query.keywords:
        stmt = stmt.where(
            or_(*[_like(Note.title, k) for k in query.keywords])
            | or_(*[_like(Note.content, k) for k in query.keywords])
            | or_(*[_like(Note.tags, k) for k in query.keywords])
        )
    # 日期过滤也要对笔记生效：此前 only todos/diaries/transactions 支持
    # 「本周/上月」这类限定，笔记会无视时间范围全部返回，结果与提示不符。
    if query.since:
        stmt = stmt.where(Note.updated_at >= datetime.combine(query.since, time.min))
    if query.until:
        stmt = stmt.where(
            Note.updated_at <= datetime.combine(query.until, time.max)
        )
    order: list = []
    title_first = _title_first(Note.title, query.keywords)
    if title_first is not None:
        order.append(title_first)
    order.append(Note.updated_at.desc())
    stmt = stmt.order_by(*order).limit(limit * 3)
    return [
        Hit(
            module="notes",
            table="notes",
            record_id=item.id,
            title=item.title or "未命名笔记",
            subtitle=(item.tags or "").replace('["', "").replace('"]', "").replace('", "', " · "),
            snippet=_snippet(item.content, query.keywords),
            score=_score(item.title, item.content or "", query.keywords),
            payload=item,
        )
        for item in session.scalars(stmt)
    ]


def _search_diaries(session: Session, query: Query, limit: int) -> list[Hit]:
    stmt = select(Diary).where(Diary.is_deleted.is_(False))
    if query.keywords:
        stmt = stmt.where(
            or_(*[_like(Diary.title, k) for k in query.keywords])
            | or_(*[_like(Diary.content, k) for k in query.keywords])
        )
    if query.since:
        stmt = stmt.where(Diary.date >= query.since)
    if query.until:
        stmt = stmt.where(Diary.date <= query.until)
    order: list = []
    title_first = _title_first(Diary.title, query.keywords)
    if title_first is not None:
        order.append(title_first)
    order.append(Diary.date.desc())
    stmt = stmt.order_by(*order).limit(limit * 3)
    return [
        Hit(
            module="diary",
            table="diaries",
            record_id=item.id,
            title=item.title,
            subtitle=item.date.isoformat(),
            snippet=_snippet(item.content, query.keywords),
            date=item.date,
            score=_score(item.title, item.content or "", query.keywords),
            payload=item,
        )
        for item in session.scalars(stmt)
    ]


def _search_transactions(session: Session, query: Query, limit: int) -> list[Hit]:
    stmt = select(Transaction).where(Transaction.is_deleted.is_(False))
    if query.keywords:
        stmt = stmt.where(
            or_(*[_like(Transaction.note, k) for k in query.keywords])
            | or_(*[_like(Transaction.category, k) for k in query.keywords])
        )
    if query.since:
        stmt = stmt.where(Transaction.date >= query.since)
    if query.until:
        stmt = stmt.where(Transaction.date <= query.until)
    order: list = []
    title_first = _title_first(Transaction.category, query.keywords)
    if title_first is not None:
        order.append(title_first)
    order += [Transaction.date.desc(), Transaction.updated_at.desc()]
    stmt = stmt.order_by(*order).limit(limit * 3)
    hits: list[Hit] = []
    for item in session.scalars(stmt):
        sign = "+" if item.kind == "income" else "-"
        hits.append(
            Hit(
                module="finance",
                table="transactions",
                record_id=item.id,
                title=f"{item.category or '未分类'} {sign}¥{float(item.amount or 0):,.2f}",
                subtitle=item.date.isoformat(),
                snippet=_snippet(item.note, query.keywords),
                date=item.date,
                score=_score(item.category or "", item.note or "", query.keywords),
                payload=item,
            )
        )
    return hits


def _search_entertainments(session: Session, query: Query, limit: int) -> list[Hit]:
    stmt = select(Entertainment).where(Entertainment.is_deleted.is_(False))
    if query.keywords:
        stmt = stmt.where(
            or_(*[_like(Entertainment.title, k) for k in query.keywords])
            | or_(*[_like(Entertainment.note, k) for k in query.keywords])
        )
    # 与笔记一致：日期过滤对娱乐同样生效（否则「上个月看的电影」会把全部记录带出来）。
    if query.since:
        stmt = stmt.where(
            Entertainment.updated_at >= datetime.combine(query.since, time.min)
        )
    if query.until:
        stmt = stmt.where(
            Entertainment.updated_at <= datetime.combine(query.until, time.max)
        )
    order: list = []
    title_first = _title_first(Entertainment.title, query.keywords)
    if title_first is not None:
        order.append(title_first)
    order.append(Entertainment.updated_at.desc())
    stmt = stmt.order_by(*order).limit(limit * 3)
    return [
        Hit(
            module="fun",
            table="entertainments",
            record_id=item.id,
            title=item.title,
            subtitle=f"{item.kind} · {item.status}"
            + (f" · {item.rating:g} 分" if item.rating else ""),
            snippet=_snippet(item.note, query.keywords),
            score=_score(item.title, item.note or "", query.keywords),
            payload=item,
        )
        for item in session.scalars(stmt)
    ]


_SEARCHERS = {
    "todos": _search_todos,
    "notes": _search_notes,
    "diaries": _search_diaries,
    "transactions": _search_transactions,
    "entertainments": _search_entertainments,
}


def search(
    text: str,
    limit_per_table: int = 6,
    tables: list[str] | None = None,
    today: date | None = None,
) -> dict[str, list[Hit]]:
    """返回 {表名: 命中列表}，表内按分数与时间排序。

    没有关键字（例如只输入「待办 今天」）时按过滤条件列记录，
    这样自然语言过滤本身就能当「看今天的高优先级待办」用。
    """
    query = parse_query(text, today=today)
    if tables:
        selected = [name for name in tables if name in _SEARCHERS]
    elif query.tables:
        selected = query.tables
    else:
        selected = list(_SEARCHERS)
    if not query.is_empty and not (query.keywords or query.tables or query.priority):
        # 只有日期条件时也允许全局浏览该时间段的记录
        selected = list(_SEARCHERS)
    result: dict[str, list[Hit]] = {}
    with session_scope() as session:
        for name in selected:
            hits = _SEARCHERS[name](session, query, limit_per_table)
            hits.sort(key=lambda hit: (-hit.score, -(hit.date.toordinal() if hit.date else 0)))
            if hits:
                result[name] = hits[:limit_per_table]
    return result


def flatten(grouped: dict[str, list[Hit]]) -> list[Hit]:
    hits: list[Hit] = []
    for items in grouped.values():
        hits.extend(items)
    hits.sort(key=lambda hit: -hit.score)
    return hits
