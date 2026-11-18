"""记账汇总服务。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Account, Transaction
from ..services.dates import shift_month


def money(value: float) -> str:
    return f"¥{value:,.2f}"


def account_balance(session: Session, account: Account) -> float:
    total = float(account.initial_balance or 0)
    rows = session.scalars(
        select(Transaction).where(
            Transaction.is_deleted.is_(False),
            Transaction.account_id == account.id,
        )
    )
    for tx in rows:
        if tx.kind == "income":
            total += tx.amount
        elif tx.kind == "expense":
            total -= tx.amount
        elif tx.kind == "transfer":
            total -= tx.amount
    incoming = session.scalars(
        select(Transaction).where(
            Transaction.is_deleted.is_(False),
            Transaction.to_account_id == account.id,
            Transaction.kind == "transfer",
        )
    )
    for tx in incoming:
        total += tx.amount
    return round(total, 2)


def total_assets(session: Session) -> float:
    accounts = session.scalars(
        select(Account).where(Account.is_deleted.is_(False))
    )
    return round(sum(account_balance(session, a) for a in accounts), 2)


def month_summary(session: Session, year: int, month: int) -> dict[str, Any]:
    start = date(year, month, 1)
    ny, nm = shift_month(year, month, 1)
    end = date(ny, nm, 1)
    rows = session.scalars(
        select(Transaction).where(
            Transaction.is_deleted.is_(False),
            Transaction.date >= start,
            Transaction.date < end,
        )
    )
    income = expense = 0.0
    categories: dict[str, float] = defaultdict(float)
    for tx in rows:
        if tx.kind == "income":
            income += tx.amount
        elif tx.kind == "expense":
            expense += tx.amount
            categories[tx.category or "其他"] += tx.amount
    return {
        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(income - expense, 2),
        "categories": {k: round(v, 2) for k, v in sorted(categories.items(), key=lambda x: -x[1])},
    }


def six_month_trend(session: Session, today: date) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for offset in range(-5, 1):
        year, month = shift_month(today.year, today.month, offset)
        summary = month_summary(session, year, month)
        result.append(
            {"label": f"{month}月", "income": summary["income"], "expense": summary["expense"]}
        )
    return result
