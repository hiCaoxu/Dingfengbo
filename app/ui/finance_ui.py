"""记账：账户、收支/转账记录、月度汇总与图表。"""

from __future__ import annotations

from datetime import date

import flet as ft
from sqlalchemy import select

from ..db.models import Account, Transaction
from ..db.repo import soft_delete
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.charts import bar_chart, line_chart, pie_chart
from ..services.finance import account_balances, money, month_summary, six_month_trend
from ..services.sync import mark_pending_write
from ..theme import ACCENT, DIVIDER, PRIMARY, TEXT, TEXT_MUTED
from .forms import open_account_dialog, open_transaction_dialog
from .widgets import card, confirm, divider, empty, icon_button, primary_button, section_header

_state = {"tab": "记录"}


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
        assets = sum(balances.values())
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
    account_img = ft.Image(src="", width=320, height=220, fit=ft.BoxFit.CONTAIN, error_content=_chart_placeholder())

    charts = ft.Row(
        [
            card(pie_img, title="支出占比"),
            card(trend_img, title="收支趋势"),
            card(account_img, title="账户余额"),
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
        account_img.src = bar_chart(
            [a.name for a in accounts], [balances.get(a.id, 0) for a in accounts], "账户余额", PRIMARY,
        )
        try:
            page.update()
        except Exception:
            pass

    if page is not None:
        page.run_thread(render_charts)

    tabs = ft.Row(
        [
            ft.TextButton(("● " if _state["tab"] == "记录" else "") + "收支记录",
                          on_click=lambda e: (_state.__setitem__("tab", "记录"), refresh())),
            ft.TextButton(("● " if _state["tab"] == "账户" else "") + "账户",
                          on_click=lambda e: (_state.__setitem__("tab", "账户"), refresh())),
        ],
        spacing=4,
    )

    if _state["tab"] == "账户":
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
        body = card(
            ft.Column(
                [section_header("账户列表", primary_button("新增账户", lambda e: open_account_dialog(page, None, refresh), ACTIONS["add"])),
                 divider(),
                 ft.Column(account_rows, spacing=8) if account_rows else empty("暂无账户")],
                spacing=10,
            ),
            expand=True,
        )
    else:
        rows = []
        for tx in transactions[:200]:
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
        body = card(
            ft.Column(
                [
                    section_header("收支记录", ft.Row([
                        primary_button("记支出", lambda e: open_transaction_dialog(page, "expense", None, refresh), ACTIONS["add"]),
                        ft.TextButton("记收入", on_click=lambda e: open_transaction_dialog(page, "income", None, refresh)),
                        ft.TextButton("转账", on_click=lambda e: open_transaction_dialog(page, "transfer", None, refresh)),
                    ], spacing=4)),
                    divider(),
                    ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True) if rows else empty("暂无记录"),
                ],
                spacing=10,
                expand=True,
            ),
            expand=True,
        )

    return ft.Column(
        [section_header("记账", tabs), summary_cards, charts, body],
        spacing=12,
        expand=True,
    )
