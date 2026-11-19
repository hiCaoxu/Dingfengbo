"""记账：账户、收支/转账记录、月度汇总与图表。"""

from __future__ import annotations

from datetime import date

import flet as ft
from sqlalchemy import select

from ..db.models import Account, Transaction
from ..db.repo import soft_delete
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.charts import line_chart, pie_chart
from ..services.finance import account_balances, money, month_summary, six_month_trend, total_assets
from ..services.sync import mark_pending_write
from ..theme import ACCENT, DIVIDER, PRIMARY, TEXT, TEXT_MUTED
from .forms import open_account_dialog, open_transaction_dialog
from .widgets import card, confirm, divider, dropdown, empty, icon_button, primary_button, section_header

_state = {"year": None, "month": 0, "page": 0, "accounts_expanded": False}

_PAGE_SIZE = 10


def _chart_placeholder() -> ft.Control:
    return ft.Container(
        content=ft.Text("加载中…", size=11, color=TEXT_MUTED),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def _delete(model, item_id: str, page, refresh) -> None:
    def action():
        with session_scope() as session:
            obj = session.get(model, item_id)
            if obj:
                soft_delete(session, obj)
        mark_pending_write()
        refresh()

    confirm(page, "确定删除该记录？", action)


def build(page: ft.Page, refresh) -> ft.Control:
    today = date.today()
    with session_scope() as session:
        summary = month_summary(session, today.year, today.month)
        balances = account_balances(session)
        assets = total_assets(session)
        accounts = list(session.scalars(select(Account).where(Account.is_deleted.is_(False))))
        transactions = list(session.scalars(
            select(Transaction).where(Transaction.is_deleted.is_(False)).order_by(Transaction.date.desc(), Transaction.id.desc())
        ))
        trend = six_month_trend(session, today)

    summary_cards = ft.Row(
        [
            card(ft.Text(money(summary["income"]), size=20, color=TEXT, weight="bold"), title="本月收入"),
            card(ft.Text(money(summary["expense"]), size=20, color=ACCENT, weight="bold"), title="本月支出"),
            card(ft.Text(money(summary["balance"]), size=20, color=TEXT, weight="bold"), title="月度结余"),
            card(ft.Text(money(assets), size=20, color=PRIMARY, weight="bold"), title="总资产"),
        ],
        spacing=12,
    )

    pie_img = ft.Image(src="", width=320, height=220, fit=ft.BoxFit.CONTAIN, error_content=_chart_placeholder())
    trend_img = ft.Image(src="", width=420, height=220, fit=ft.BoxFit.CONTAIN, error_content=_chart_placeholder())

    charts = ft.Row(
        [
            card(pie_img, title="支出占比"),
            card(trend_img, title="收支趋势"),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )

    def render_charts() -> None:
        pie_img.src = pie_chart(summary["categories"], "支出占比")
        trend_img.src = line_chart(
            [t["label"] for t in trend],
            [{"name": "收入", "values": [t["income"] for t in trend], "color": PRIMARY},
             {"name": "支出", "values": [t["expense"] for t in trend], "color": ACCENT}],
            "近六个月收支趋势",
        )
        try:
            page.update()
        except Exception:
            pass

    if page is not None:
        page.run_thread(render_charts)

    # 年月筛选 + 分页
    years = sorted({tx.date.year for tx in transactions}, reverse=True)
    year_options = ["全部"] + [str(y) for y in years]
    month_options = ["全年"] + [f"{m}月" for m in range(1, 13)]

    def matches(tx: Transaction) -> bool:
        if _state["year"] is not None and tx.date.year != _state["year"]:
            return False
        if _state["month"] != 0 and tx.date.month != _state["month"]:
            return False
        return True

    filtered = [tx for tx in transactions if matches(tx)]
    total = len(filtered)
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = min(_state["page"], total_pages - 1)
    page_items = filtered[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]

    def set_year(value: str) -> None:
        _state["year"] = None if value == "全部" else int(value)
        _state["page"] = 0
        refresh()

    def set_month(value: str) -> None:
        _state["month"] = 0 if value == "全年" else int(value.replace("月", ""))
        _state["page"] = 0
        refresh()

    year_dd = dropdown("年份", year_options, "全部" if _state["year"] is None else str(_state["year"]), width=110)
    month_dd = dropdown("月份", month_options, "全年" if _state["month"] == 0 else f"{_state['month']}月", width=110)
    year_dd.on_select = lambda e: set_year(e.control.value)
    month_dd.on_select = lambda e: set_month(e.control.value)

    filter_row = ft.Row(
        [year_dd, month_dd, ft.Container(expand=True), ft.Text(f"共 {total} 条", size=11, color=TEXT_MUTED)],
        spacing=8,
    )

    rows = []
    for tx in page_items:
        sign = {"income": "+", "expense": "-", "transfer": "⇄"}[tx.kind]
        color = PRIMARY if tx.kind == "income" else (ACCENT if tx.kind == "expense" else TEXT_MUTED)
        note = tx.category or ("转账" if tx.kind == "transfer" else "")
        rows.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Column([ft.Text(f"{sign} {money(tx.amount)}", size=14, color=color, weight="bold"),
                                   ft.Text(f"{tx.date} · {note} · {tx.note}", size=10, color=TEXT_MUTED)],
                                  spacing=3, expand=True),
                        icon_button(ACTIONS["edit"], lambda e, t=tx: open_transaction_dialog(page, t.kind, t, refresh), "编辑"),
                        icon_button(ACTIONS["delete"], lambda e, t=tx.id: _delete(Transaction, t, page, refresh), "删除"),
                    ],
                    spacing=8,
                ),
                border=ft.Border.all(1, DIVIDER), border_radius=10,
                padding=ft.Padding.symmetric(vertical=9, horizontal=12),
            )
        )

    pagination = ft.Row(
        [
            ft.TextButton("上一页", disabled=page == 0,
                          on_click=lambda e: (_state.__setitem__("page", max(0, page - 1)), refresh())),
            ft.Text(f"{page + 1} / {total_pages}", size=11, color=TEXT_MUTED),
            ft.TextButton("下一页", disabled=page >= total_pages - 1,
                          on_click=lambda e: (_state.__setitem__("page", min(total_pages - 1, page + 1)), refresh())),
        ],
        spacing=4,
        alignment=ft.MainAxisAlignment.CENTER,
    )

    records_card = card(
        ft.Column(
            [
                section_header("收支记录", primary_button("记一笔", lambda e: open_transaction_dialog(page, "expense", None, refresh), ACTIONS["add"])),
                divider(),
                filter_row,
                ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True) if rows else empty("暂无记录"),
                pagination,
            ],
            spacing=10,
            expand=True,
        ),
        expand=True,
    )

    # 账户列表：置于页面最下方，可收起/展开，默认收起
    account_rows = []
    for account in accounts:
        balance = balances.get(account.id, 0)
        account_rows.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Column([ft.Text(account.name, size=13, color=TEXT, weight="bold"),
                                   ft.Text(f"{account.kind} · 初始 {money(account.initial_balance)}", size=10, color=TEXT_MUTED)],
                                  spacing=3, expand=True),
                        ft.Text(money(balance), size=16, color=PRIMARY, weight="bold"),
                        icon_button(ACTIONS["edit"], lambda e, a=account: open_account_dialog(page, a, refresh), "编辑"),
                        icon_button(ACTIONS["delete"], lambda e, a=account.id: _delete(Account, a, page, refresh), "删除"),
                    ],
                    spacing=10,
                ),
                border=ft.Border.all(1, DIVIDER), border_radius=10,
                padding=ft.Padding.symmetric(vertical=10, horizontal=12),
            )
        )

    accounts_tile = ft.ExpansionTile(
        title=ft.Row(
            [
                ft.Text(f"账户列表（{len(accounts)}）", size=13, color=TEXT, weight="bold"),
                ft.Container(expand=True),
                primary_button("新增账户", lambda e: open_account_dialog(page, None, refresh), ACTIONS["add"]),
            ],
            spacing=8,
        ),
        expanded=_state["accounts_expanded"],
        controls=account_rows if account_rows else [empty("暂无账户")],
        on_change=lambda e: _state.__setitem__("accounts_expanded", e.control.expanded),
        maintain_state=True,
    )

    accounts_card = card(accounts_tile)

    return ft.Column(
        [section_header("记账"), summary_cards, charts, records_card, accounts_card],
        spacing=12,
        expand=True,
    )
