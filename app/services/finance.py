"""记账汇总服务。

汇总一律下推 SQL 聚合（GROUP BY / SUM），避免把明细行搬进 Python 再累加：
交易量增长时，结果集只与「月份数 × 类型数 / 账户数」相关，与数据总量基本脱钩。

金额在 Python 侧统一用 Decimal 累加（见 ``services.money``）：SQL 的 SUM 结果本身
是浮点，多笔相加/比较时改用精确十进制，最后再量化到分返回 float。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Account, Transaction
from .dates import shift_month
from .money import ZERO, format_money, from_cents, to_decimal, to_float


def money(value: float) -> str:
    return format_money(value)


def _balances_decimal(session: Session) -> dict[str, Decimal]:
    """一次性聚合全部账户余额（Decimal 精度），供 total_assets 复用。"""
    accounts = session.scalars(select(Account).where(Account.is_deleted.is_(False)))
    balances = {account.id: to_decimal(account.initial_balance) for account in accounts}
    rows = session.execute(
        select(Transaction.account_id, Transaction.kind, func.sum(Transaction.amount))
        .where(Transaction.is_deleted.is_(False))
        .group_by(Transaction.account_id, Transaction.kind)
    )
    for account_id, kind, total in rows:
        amount = to_decimal(total)
        current = balances.get(account_id, ZERO)
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
        balances[account_id] = balances.get(account_id, ZERO) + to_decimal(total)
    return balances


def account_balances(session: Session) -> dict[str, float]:
    """全部账户余额（量化到分的 float，供界面与同步计算使用）。"""
    return {key: to_float(value) for key, value in _balances_decimal(session).items()}


def total_assets(session: Session) -> float:
    """总资产：仅统计非「借贷」类型账户的余额。"""
    asset_ids = {
        account.id
        for account in session.scalars(
            select(Account).where(Account.is_deleted.is_(False), Account.kind != "借贷")
        )
    }
    total = ZERO
    for account_id, balance in _balances_decimal(session).items():
        if account_id in asset_ids:
            total += balance
    return to_float(total)


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
    income = ZERO
    expense = ZERO
    categories: dict[str, Decimal] = defaultdict(Decimal)
    for kind, category, total in rows:
        amount = to_decimal(total)
        if kind == "income":
            income += amount
        elif kind == "expense":
            expense += amount
            categories[category or "其他"] += amount
    return {
        "income": to_float(income),
        "expense": to_float(expense),
        "balance": to_float(income - expense),
        "categories": {
            key: to_float(value)
            for key, value in sorted(categories.items(), key=lambda item: -item[1])
        },
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
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for label, kind, total in rows:
        totals[(str(label), str(kind))] += to_decimal(total)

    result: list[dict[str, Any]] = []
    for offset in range(-5, 1):
        year, month = shift_month(today.year, today.month, offset)
        key = f"{year:04d}-{month:02d}"
        result.append(
            {
                "label": f"{month}月",
                "income": to_float(totals.get((key, "income"), Decimal(0))),
                "expense": to_float(totals.get((key, "expense"), Decimal(0))),
            }
        )
    return result
