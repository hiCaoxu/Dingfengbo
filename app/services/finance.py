"""记账汇总服务。

汇总一律下推 SQL 聚合（GROUP BY / SUM），避免把明细行搬进 Python 再累加：
交易量增长时，结果集只与「月份数 × 类型数 / 账户数」相关，与数据总量基本脱钩。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Account, Transaction
from ..services.dates import shift_month


def money(value: float) -> str:
    return f"¥{value:,.2f}"


def account_balance(session: Session, account: Account) -> float:
    return account_balances(session).get(account.id, 0.0)


def account_balances(session: Session) -> dict[str, float]:
    """一次性聚合全部账户余额：一条按 (账户, 类型) 分组的 SUM，加一条转入 SUM。"""
    accounts = session.scalars(select(Account).where(Account.is_deleted.is_(False)))
    balances = {account.id: float(account.initial_balance or 0) for account in accounts}
    rows = session.execute(
        select(Transaction.account_id, Transaction.kind, func.sum(Transaction.amount))
        .where(Transaction.is_deleted.is_(False))
        .group_by(Transaction.account_id, Transaction.kind)
    )
    for account_id, kind, total in rows:
        amount = float(total or 0)
        current = balances.get(account_id, 0.0)
        if kind == "income":
            balances[account_id] = current + amount
        elif kind in ("expense", "transfer"):
            balances[account_id] = current - amount
    incoming = session.execute(
        select(Transaction.to_account_id, func.sum(Transaction.amount))
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.kind == "transfer",
            Transaction.to_account_id.is_not(None),
        )
        .group_by(Transaction.to_account_id)
    )
    for account_id, total in incoming:
        if account_id is None:
            continue
        balances[account_id] = balances.get(account_id, 0.0) + float(total or 0)
    return {key: round(value, 2) for key, value in balances.items()}


def total_assets(session: Session) -> float:
    """总资产：仅统计非「借贷」类型账户的余额。"""
    asset_ids = {
        account.id
        for account in session.scalars(
            select(Account).where(Account.is_deleted.is_(False), Account.kind != "借贷")
        )
    }
    return round(sum(value for key, value in account_balances(session).items() if key in asset_ids), 2)


def month_summary(session: Session, year: int, month: int) -> dict[str, Any]:
    """当月收支：一条按 (类型, 分类) 分组的 SUM 得到全部数字。"""
    start = date(year, month, 1)
    ny, nm = shift_month(year, month, 1)
    end = date(ny, nm, 1)
    rows = session.execute(
        select(Transaction.kind, Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.date >= start,
            Transaction.date < end,
        )
        .group_by(Transaction.kind, Transaction.category)
    )
    income = expense = 0.0
    categories: dict[str, float] = defaultdict(float)
    for kind, category, total in rows:
        amount = float(total or 0)
        if kind == "income":
            income += amount
        elif kind == "expense":
            expense += amount
            categories[category or "其他"] += amount
    return {
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(income - expense, 2),
        "categories": {k: round(v, 2) for k, v in sorted(categories.items(), key=lambda x: -x[1])},
    }


def six_month_trend(session: Session, today: date) -> list[dict[str, Any]]:
    """近六个月收支趋势：一条 GROUP BY 取代六次 month_summary 全表扫描。"""
    start_year, start_month = shift_month(today.year, today.month, -5)
    start = date(start_year, start_month, 1)
    month_key = func.strftime("%Y-%m", Transaction.date)
    rows = session.execute(
        select(month_key, Transaction.kind, func.sum(Transaction.amount))
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.date >= start,
            Transaction.kind.in_(("income", "expense")),
        )
        .group_by(month_key, Transaction.kind)
    )
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for label, kind, total in rows:
        totals[(str(label), str(kind))] += float(total or 0)

    result: list[dict[str, Any]] = []
    for offset in range(-5, 1):
        year, month = shift_month(today.year, today.month, offset)
        key = f"{year:04d}-{month:02d}"
        result.append(
            {
                "label": f"{month}月",
                "income": round(totals.get((key, "income"), 0.0), 2),
                "expense": round(totals.get((key, "expense"), 0.0), 2),
            }
        )
    return result
