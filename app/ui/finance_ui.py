"""记账：概览（汇总 + 图表 + 入口）、收支记录、账户列表三个视图。"""

from __future__ import annotations

import logging
from datetime import date

import flet as ft
from sqlalchemy import func, select

from ..db.models import Account, Transaction
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.charts import line_chart, pie_chart
from ..services.finance import account_balances, money, month_summary, six_month_trend, total_assets
from ..theme import ACCENT, DIVIDER, PRIMARY, TEXT, TEXT_MUTED
from .async_utils import background_section
from .forms import open_account_dialog, open_transaction_dialog
from .state import state
from .widgets import card, delete_with_confirm, divider, dropdown, empty, icon_button, primary_button, section_header

logger = logging.getLogger(__name__)

# view: overview / records / accounts
_state = state.finance

_PAGE_SIZE = 10


def _chart_placeholder() -> ft.Control:
    return ft.Container(
        content=ft.Text("加载中…", size=11, color=TEXT_MUTED),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def _chart_slot(width: int, height: int) -> ft.Container:
    """图表占位槽：初始显示加载提示，后台渲染完成后替换为 Image。"""
    return ft.Container(
        content=_chart_placeholder(),
        width=width,
        height=height,
        alignment=ft.Alignment.CENTER,
    )


def _delete(model, item_id: str, page, refresh) -> None:
    delete_with_confirm(page, model, item_id, refresh, "确定删除该记录？")


def _tx_row(tx: Transaction, page, refresh, show_actions: bool = True) -> ft.Control:
    sign = {"income": "+", "expense": "-", "transfer": "⇄"}[tx.kind]
    color = PRIMARY if tx.kind == "income" else (ACCENT if tx.kind == "expense" else TEXT_MUTED)
    note = tx.category or ("转账" if tx.kind == "transfer" else "")
    actions = []
    if show_actions:
        actions = [
            icon_button(ACTIONS["edit"], lambda e, t=tx: open_transaction_dialog(page, t.kind, t, refresh), "编辑"),
            icon_button(ACTIONS["delete"], lambda e, t=tx.id: _delete(Transaction, t, page, refresh), "删除"),
        ]
    return ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [ft.Text(f"{sign} {money(tx.amount)}", size=14, color=color, weight="bold"),
                     ft.Text(f"{tx.date} · {note} · {tx.note}", size=10, color=TEXT_MUTED)],
                    spacing=3, expand=True,
                ),
                *actions,
            ],
            spacing=8,
        ),
        border=ft.Border.all(1, DIVIDER), border_radius=10,
        padding=ft.Padding.symmetric(vertical=9, horizontal=12),
    )


def _go(page, refresh, view: str) -> None:
    _state["view"] = view
    _state["page"] = 0
    refresh()


def _back(page, refresh) -> ft.Control:
    return ft.TextButton("← 返回", on_click=lambda e: _go(page, refresh, "overview"))


def _build_overview(page: ft.Page, refresh) -> ft.Control:
    today = date.today()
    with session_scope() as session:
        summary = month_summary(session, today.year, today.month)
        assets = total_assets(session)
        trend = six_month_trend(session, today)
        recent_txs = list(
            session.scalars(
                select(Transaction).where(Transaction.is_deleted.is_(False))
                .order_by(Transaction.date.desc(), Transaction.id.desc())
                .limit(5)
            )
        )
        tx_max_updated = session.scalar(
            select(func.max(Transaction.updated_at)).where(Transaction.is_deleted.is_(False))
        )

    summary_cards = ft.Row(
        [
            card(ft.Text(money(summary["income"]), size=20, color=TEXT, weight="bold"), title="本月收入"),
            card(ft.Text(money(summary["expense"]), size=20, color=ACCENT, weight="bold"), title="本月支出"),
            card(ft.Text(money(summary["balance"]), size=20, color=TEXT, weight="bold"), title="月度结余"),
            card(ft.Text(money(assets), size=20, color=PRIMARY, weight="bold"), title="总资产"),
        ],
        spacing=12,
    )

    pie_slot = _chart_slot(320, 220)
    trend_slot = _chart_slot(420, 220)
    charts = ft.Row(
        [card(pie_slot, title="支出占比"), card(trend_slot, title="收支趋势")],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )

    def render_charts() -> None:
        pie_slot.content = ft.Image(
            src=pie_chart(summary["categories"], "支出占比", cache_key=tx_max_updated),
            width=320, height=220, fit=ft.BoxFit.CONTAIN,
        )
        trend_slot.content = ft.Image(
            src=line_chart(
                [t["label"] for t in trend],
                [{"name": "收入", "values": [t["income"] for t in trend], "color": PRIMARY},
                 {"name": "支出", "values": [t["expense"] for t in trend], "color": ACCENT}],
                "近六个月收支趋势",
                cache_key=tx_max_updated,
            ),
            width=420, height=220, fit=ft.BoxFit.CONTAIN,
        )
        try:
            page.update()
        except Exception:
            logger.exception("记账图表渲染后刷新页面失败")

    if page is not None:
        page.run_thread(render_charts)

    # 最新收支：5 条纯展示，不支持筛选
    recent_rows = []
    for tx in recent_txs:
        sign = {"income": "+", "expense": "-", "transfer": "⇄"}[tx.kind]
        color = PRIMARY if tx.kind == "income" else (ACCENT if tx.kind == "expense" else TEXT_MUTED)
        note_label = tx.category or ("转账" if tx.kind == "transfer" else "")
        recent_rows.append(
            ft.Row(
                [
                    ft.Text(f"{sign} {money(tx.amount)}", size=12, color=color, weight="bold"),
                    ft.Text(note_label, size=11, color=TEXT_MUTED, expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(tx.date.isoformat(), size=10, color=TEXT_MUTED),
                ],
                spacing=8,
            )
        )
    recent_card = card(
        ft.Column(recent_rows, spacing=6) if recent_rows else empty("暂无收支记录"),
        title="最新收支",
        expand=2,
    )

    def entry(icon: str, title: str, subtitle: str, target: str) -> ft.Control:
        return card(
            ft.Column(
                [
                    ft.Icon(ico(icon), size=26, color=PRIMARY),
                    ft.Text(title, size=13, color=TEXT, weight="bold"),
                    ft.Text(subtitle, size=10, color=TEXT_MUTED),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            on_click=lambda e: _go(page, refresh, target),
            expand=1,
        )

    # 最新收支、收支记录、账户列表 一行三卡片
    entries = ft.Row(
        [
            recent_card,
            entry("RECEIPT_LONG_OUTLINED", "收支记录", "查看、筛选与新增", "records"),
            entry("ACCOUNT_BALANCE_WALLET_OUTLINED", "账户列表", "管理账户与余额", "accounts"),
        ],
        spacing=12,
        height=170,
    )

    return ft.Column(
        [
            section_header("记账", primary_button("记一笔", lambda e: open_transaction_dialog(page, "expense", None, refresh), ACTIONS["add"])),
            summary_cards,
            charts,
            entries,
        ],
        spacing=12,
        expand=True,
    )


def _records_query():
    """收支记录页的数据查询（后台线程执行）：年份去重、计数与分页全部下推 SQL。"""
    conditions = [Transaction.is_deleted.is_(False)]
    if _state["year"] is not None:
        conditions.append(func.strftime("%Y", Transaction.date) == str(_state["year"]))
    if _state["month"] != 0:
        conditions.append(func.strftime("%m", Transaction.date) == f"{_state['month']:02d}")

    with session_scope() as session:
        # 年份选项：SQL 去重，不再把全部日期搬进 Python 再 `set`。
        years = sorted(
            (
                int(year)
                for year in session.scalars(
                    select(func.distinct(func.strftime("%Y", Transaction.date))).where(
                        Transaction.is_deleted.is_(False)
                    )
                )
                if year
            ),
            reverse=True,
        )
        total = session.scalar(select(func.count()).select_from(Transaction).where(*conditions)) or 0
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        current_page = min(_state["page"], total_pages - 1)
        page_items = list(
            session.scalars(
                select(Transaction)
                .where(*conditions)
                .order_by(Transaction.date.desc(), Transaction.id.desc())
                .limit(_PAGE_SIZE)
                .offset(current_page * _PAGE_SIZE)
            )
        )
    return years, total, total_pages, current_page, page_items


def _build_records(page: ft.Page, refresh) -> ft.Control:
    return background_section(
        page, _records_query, lambda data: _render_records(page, refresh, data)
    )


def _render_records(page: ft.Page, refresh, data) -> ft.Control:
    years, total, total_pages, current_page, page_items = data
    year_options = ["全部"] + [str(y) for y in years]
    month_options = ["全年"] + [f"{m}月" for m in range(1, 13)]

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

    rows = [_tx_row(tx, page, refresh) for tx in page_items]

    pagination = ft.Row(
        [
            ft.TextButton("上一页", disabled=current_page == 0,
                          on_click=lambda e: (_state.__setitem__("page", max(0, current_page - 1)), refresh())),
            ft.Text(f"{current_page + 1} / {total_pages}", size=11, color=TEXT_MUTED),
            ft.TextButton("下一页", disabled=current_page >= total_pages - 1,
                          on_click=lambda e: (_state.__setitem__("page", min(total_pages - 1, current_page + 1)), refresh())),
        ],
        spacing=4,
        alignment=ft.MainAxisAlignment.CENTER,
    )

    return ft.Column(
        [
            section_header("收支记录", primary_button("记一笔", lambda e: open_transaction_dialog(page, "expense", None, refresh), ACTIONS["add"])),
            _back(page, refresh),
            filter_row,
            divider(),
            ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True) if rows else empty("暂无记录"),
            pagination,
        ],
        spacing=10,
        expand=True,
    )


def _build_accounts(page: ft.Page, refresh) -> ft.Control:
    with session_scope() as session:
        accounts = list(session.scalars(select(Account).where(Account.is_deleted.is_(False))))
        balances = account_balances(session)

    account_cards = []
    for account in accounts:
        balance = balances.get(account.id, 0)
        account_cards.append(
            card(
                ft.Row(
                    [
                        ft.Column(
                            [ft.Text(account.name, size=13, color=TEXT, weight="bold"),
                             ft.Text(f"{account.kind} · 初始 {money(account.initial_balance)}", size=10, color=TEXT_MUTED)],
                            spacing=3, expand=True,
                        ),
                        ft.Text(money(balance), size=16, color=PRIMARY, weight="bold"),
                        icon_button(ACTIONS["edit"], lambda e, a=account: open_account_dialog(page, a, refresh), "编辑"),
                        icon_button(ACTIONS["delete"], lambda e, a=account.id: _delete(Account, a, page, refresh), "删除"),
                    ],
                    spacing=10,
                ),
                width=300,
            )
        )

    return ft.Column(
        [
            section_header("账户列表", primary_button("新增账户", lambda e: open_account_dialog(page, None, refresh), ACTIONS["add"])),
            _back(page, refresh),
            divider(),
            ft.Row(account_cards, spacing=12, wrap=True, scroll=ft.ScrollMode.AUTO, expand=True) if account_cards else empty("暂无账户"),
        ],
        spacing=10,
        expand=True,
    )


def build(page: ft.Page, refresh) -> ft.Control:
    view = _state.get("view", "overview")
    if view == "records":
        return _build_records(page, refresh)
    if view == "accounts":
        return _build_accounts(page, refresh)
    return _build_overview(page, refresh)
