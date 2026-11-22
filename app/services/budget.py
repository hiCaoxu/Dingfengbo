"""预算、周期记账（固定支出/订阅）与消费异常提示。

预算按「月份 + 分类」存一行：``month`` 为空表示长期有效，``category`` 为空
表示月度总预算。周期规则到期后自动生成真实交易，生成逻辑幂等（靠
``last_generated`` 与 ``next_date`` 推进），重复启动不会重复记账。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Account, Budget, RecurringRule, Transaction
from ..db.repo import create, update
from ..db.session import session_scope
from .money import ZERO, from_cents, is_over, percent, quantize, to_float, total

PERIOD_LABELS = {"month": "每月", "week": "每周"}
MAX_CATCH_UP = 12  # 一次最多补生成 12 期，避免长期未启动后爆量


def _cents(value: float | None) -> int:
    """金额换算成整数分（兼容旧调用；新代码统一用 ``services.money``）。"""
    return int(round(float(value or 0) * 100))


def occurrence_id(rule_id: str, due: date) -> str:
    """周期交易的确定性主键：同一 (规则, 到期日) 在任何设备都会算出同一个 id。

    ``next_date`` 只能保证本机不重复生成；两台设备先后离线启动时各自 ``create``
    会得到两笔不同 UUID 的「本月房租」，同步后双份入账。改用确定性 id 后，
    生成变成「每个 (规则, 到期日) 全宇宙只可能发生一次」。
    """
    return str(uuid5(NAMESPACE_URL, f"recur:{rule_id}:{due.isoformat()}"))


def month_key(day: date | None = None) -> str:
    day = day or date.today()
    return f"{day.year:04d}-{day.month:02d}"


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    return start, day


def budgets(month: str | None = None) -> list[Budget]:
    """读取某月预算（含长期条目 month="")。"""
    month = month or month_key()
    with session_scope() as session:
        return list(
            session.scalars(
                select(Budget).where(
                    Budget.is_deleted.is_(False),
                    (Budget.month == month) | (Budget.month == ""),
                )
            )
        )


def upsert_budget(month: str, category: str, amount: float, note: str = "") -> None:
    """同一月份同一分类只保留一条（手动查再改，避免唯一约束在同步时冲突）。

    ``month`` 为空表示长期条目；长期与月度同时存在时由 ``overview`` 按「月度优先」
    取一条，写入侧不做合并，避免用户想改长期值时被月度值挡住。
    """
    month = month or month_key()
    category = (category or "").strip()
    with session_scope() as session:
        row = session.scalar(
            select(Budget).where(
                Budget.is_deleted.is_(False),
                Budget.month == month,
                Budget.category == category,
            )
        )
        if amount <= 0:
            if row is not None:
                from ..db.repo import soft_delete

                soft_delete(session, row)
            return
        if row is None:
            create(session, Budget, month=month, category=category, amount=amount, note=note)
        else:
            update(session, row, amount=amount, note=note)


def spending_by_category(session: Session, start: date, end: date) -> dict[str, float]:
    rows = session.execute(
        select(Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.kind == "expense",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .group_by(Transaction.category)
    )
    # SUM 拿到的是整数分，必须换算回元
    return {str(category or "其他"): to_float(total) for category, total in rows}


def overview(today: date | None = None) -> dict[str, Any]:
    """预算执行情况：总额、已用、剩余、各分类进度。"""
    today = today or date.today()
    start, end = _month_bounds(today)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(Budget).where(
                    Budget.is_deleted.is_(False),
                    (Budget.month == month_key(today)) | (Budget.month == ""),
                )
            )
        )
        actual = spending_by_category(session, start, end)
        month_expense = total(actual.values())

        # 同一分类可能同时存在「长期」与「本月」两条：月度条目覆盖长期条目，
        # 不能相加，否则长期 1000 + 本月 1500 会变成 2500 的假上限。
        monthly: dict[str, Decimal] = {}
        standing: dict[str, Decimal] = {}
        total_monthly = ZERO
        total_standing = ZERO
        for row in rows:
            amount = quantize(row.amount)
            if row.category:
                bucket = monthly if row.month else standing
                bucket[row.category] = bucket.get(row.category, ZERO) + amount
            elif row.month:
                total_monthly += amount
            else:
                total_standing += amount
        categories = {**standing, **monthly}
        total_budget = total_monthly or total_standing

    items = []
    for category, amount in sorted(categories.items(), key=lambda item: -item[1]):
        used = quantize(actual.get(category, 0.0))
        items.append(
            {
                "category": category,
                "budget": to_float(amount),
                "used": to_float(used),
                "remaining": to_float(amount - used),
                "percent": percent(used, amount),
                "over": used > amount,
            }
        )
    return {
        "month": month_key(today),
        "total_budget": to_float(total_budget),
        "expense": to_float(month_expense),
        "remaining": to_float(total_budget - month_expense) if total_budget else 0.0,
        "percent": percent(month_expense, total_budget),
        "categories": items,
        "uncategorized": to_float(
            total(
                value
                for category, value in actual.items()
                if category not in categories
            )
        ),
    }


def alerts(today: date | None = None) -> list[str]:
    """超支/接近上限提示，供今日聚焦与记账页展示。"""
    data = overview(today)
    messages: list[str] = []
    if data["total_budget"] and is_over(data["expense"], data["total_budget"]):
        messages.append(
            f"本月总预算已超支 ¥{quantize(data['expense']) - quantize(data['total_budget']):.2f}"
        )
    elif data["percent"] >= 80:
        messages.append(f"本月预算已用 {data['percent']}%")
    for item in data["categories"]:
        if item["over"]:
            messages.append(f"「{item['category']}」超预算 ¥{abs(item['remaining']):.2f}")
    return messages


# ------------------------------------------------------------ 周期记账


def rules() -> list[RecurringRule]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(RecurringRule)
                .where(RecurringRule.is_deleted.is_(False))
                .order_by(RecurringRule.next_date)
            )
        )


def upcoming(days: int = 7, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    edge = today + timedelta(days=days)
    result = []
    for rule in rules():
        if not rule.active or rule.next_date is None:
            continue
        if rule.next_date <= edge:
            result.append(
                {
                    "id": rule.id,
                    "name": rule.name,
                    "amount": to_float(rule.amount),
                    "next_date": rule.next_date,
                    "period": rule.period,
                }
            )
    return result


def next_occurrence(rule: RecurringRule, base: date) -> date:
    """按规则推算下一次扣款日（月末自动夹到当月最后一天）。"""
    if rule.period == "week":
        return base + timedelta(days=7)
    year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
    day = min(max(1, int(rule.day or 1)), calendar.monthrange(year, month)[1])
    return date(year, month, day)


def initial_next_date(rule_day: int, period: str, today: date | None = None) -> date:
    """新建规则时推算首次执行日：本月还没到就本月，否则下月。"""
    today = today or date.today()
    if period == "week":
        target = today.weekday() + 1
        delta = (max(1, min(7, rule_day)) - target) % 7
        return today + timedelta(days=delta)
    day = min(max(1, int(rule_day or 1)), calendar.monthrange(today.year, today.month)[1])
    candidate = date(today.year, today.month, day)
    if candidate < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        day = min(max(1, int(rule_day or 1)), calendar.monthrange(year, month)[1])
        candidate = date(year, month, day)
    return candidate


def generate_due(today: date | None = None) -> int:
    """为到期的周期规则生成交易，返回生成条数。"""
    today = today or date.today()
    created = 0
    from ..services.sync import mark_pending_write

    with session_scope() as session:
        fallback_account = session.scalar(
            select(Account).where(Account.is_deleted.is_(False)).order_by(Account.created_at)
        )
        rows = list(
            session.scalars(
                select(RecurringRule).where(
                    RecurringRule.is_deleted.is_(False),
                    RecurringRule.active.is_(True),
                    RecurringRule.next_date.is_not(None),
                    RecurringRule.next_date <= today,
                )
            )
        )
        for rule in rows:
            account_id = rule.account_id or (fallback_account.id if fallback_account else "")
            if not account_id:
                continue
            guard = 0
            while rule.next_date and rule.next_date <= today and guard < MAX_CATCH_UP:
                due = rule.next_date
                record_id = occurrence_id(rule.id, due)
                # 先查再建：另一台设备可能已经用同一个 id 生成过这笔账。
                if session.get(Transaction, record_id) is None:
                    create(
                        session,
                        Transaction,
                        id=record_id,
                        kind=rule.kind or "expense",
                        amount=to_float(rule.amount),
                        category=rule.category or "",
                        account_id=account_id,
                        date=due,
                        note=f"[周期] {rule.name}",
                    )
                    created += 1
                # 走 repo.update 而不是直接赋属性：直接改不会 mark_dirty，
                # 规则行会停在 clean 状态、永远不会被推送到服务端，
                # 另一台设备就按旧的 next_date 反复进入补生成流程。
                update(session, rule, last_generated=due, next_date=next_occurrence(rule, due))
                guard += 1
            if guard == MAX_CATCH_UP:
                # 长期未启动：直接跳到下一个未来日期，避免下次启动继续补历史。
                while rule.next_date and rule.next_date <= today:
                    update(session, rule, next_date=next_occurrence(rule, rule.next_date))
    if created:
        mark_pending_write()
    return created


def save_rule(
    rule_id: str | None,
    *,
    name: str,
    amount: float,
    kind: str,
    category: str,
    account_id: str,
    period: str,
    day: int,
    active: bool,
    note: str = "",
) -> None:
    with session_scope() as session:
        if rule_id:
            rule = session.get(RecurringRule, rule_id)
            if rule is None:
                return
            update(
                session,
                rule,
                name=name,
                amount=amount,
                kind=kind,
                category=category,
                account_id=account_id,
                period=period,
                day=day,
                active=active,
                note=note,
            )
            if rule.next_date is None or rule.next_date < date.today():
                rule.next_date = initial_next_date(day, period)
        else:
            create(
                session,
                RecurringRule,
                name=name,
                amount=amount,
                kind=kind,
                category=category,
                account_id=account_id,
                period=period,
                day=day,
                active=active,
                note=note,
                next_date=initial_next_date(day, period),
            )


def delete_rule(rule_id: str) -> None:
    from ..db.repo import soft_delete

    with session_scope() as session:
        rule = session.get(RecurringRule, rule_id)
        if rule is not None:
            soft_delete(session, rule)


def monthly_fixed_total() -> float:
    """固定支出合计（周付按 4.33 折算成月），供记账概览展示。"""
    total_amount = ZERO
    for rule in rules():
        if not rule.active or rule.kind != "expense":
            continue
        amount = quantize(rule.amount)
        total_amount += amount * (Decimal("4.33") if rule.period == "week" else 1)
    return to_float(total_amount)
