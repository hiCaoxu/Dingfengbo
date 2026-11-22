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
from ..services import budget as budget_service
from .. import theme as th
from .async_utils import background_section
from .forms import open_account_dialog, open_transaction_dialog
from .budget_dialogs import (
    open_budget_dialog,
    open_quick_parse_dialog,
    open_recurring_dialog,
)
from .state import state
from .widgets import (
    card,
    delete_with_confirm,
    divider,
    dropdown,
    empty,
    equal_height_row,
    icon_button,
    metric_card,
    page_header,
    primary_button,
)

logger = logging.getLogger(__name__)

# view: overview / records / accounts
_state = state.finance

_PAGE_SIZE = 10


def _chart_placeholder() -> ft.Control:
    return ft.Container(
        content=ft.Text("图表加载中…", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def _chart_slot() -> ft.Container:
    """图表占位槽：初始显示加载提示，后台渲染完成后替换为 Image。

    尺寸交给父容器弹性分配：窗口更宽时图表跟着变宽，卡片右侧不会留出一条空白。
    """
    return ft.Container(
        content=_chart_placeholder(),
        expand=True,
        alignment=ft.Alignment.CENTER,
    )


def _chart_image(src: bytes, width: int, height: int) -> ft.Image:
    """图表位图：保持生成时的清晰尺寸，由外层居中槽位摆放。

    卡片宽度改成了按比例分配（窗口变宽时三列一起变宽），图片本身仍按原像素
    渲染并居中——放大位图只会更糊，居中留白比整行右侧空一条更耐看。
    """
    return ft.Image(src=src, width=width, height=height, fit=ft.BoxFit.CONTAIN)


def _delete(model, item_id: str, page, refresh) -> None:
    delete_with_confirm(page, model, item_id, refresh, "确定删除该记录？")


# 历史数据/导入可能带中文 kind（支出/收入/转账），同步载荷也可能来自旧客户端：
# 一律先归一化再取符号，直接下标会 KeyError 把整页渲染打挂。
_KIND_BY_LABEL = {"支出": "expense", "收入": "income", "转账": "transfer"}
_KIND_SIGNS = {"income": "+", "expense": "-", "transfer": "⇄"}


def _kind_code(raw: str | None) -> str:
    code = str(raw or "").strip()
    return _KIND_BY_LABEL.get(code, code) or "expense"


def _kind_sign(raw: str | None) -> str:
    return _KIND_SIGNS.get(_kind_code(raw), "-")


def _tx_row(tx: Transaction, page, refresh, show_actions: bool = True) -> ft.Control:
    kind = _kind_code(tx.kind)
    sign = _kind_sign(kind)
    color = th.PRIMARY if kind == "income" else (th.ACCENT if kind == "expense" else th.TEXT_MUTED)
    note = tx.category or ("转账" if kind == "transfer" else "")
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
                    [
                        ft.Text(f"{sign} {money(tx.amount)}", size=14, color=color, weight="bold",
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"{tx.date} · {note} · {tx.note}", size=th.FONT_CAPTION,
                                color=th.TEXT_MUTED, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                    ],
                    spacing=3, expand=True,
                ),
                *actions,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        border=ft.Border.all(1, th.DIVIDER), border_radius=10,
        padding=ft.Padding.symmetric(vertical=9, horizontal=12),
    )


def _go(page, refresh, view: str) -> None:
    _state["view"] = view
    _state["page"] = 0
    refresh()


def _back(page, refresh) -> ft.Control:
    return ft.TextButton("← 返回", on_click=lambda e: _go(page, refresh, "overview"))


def _overview_query() -> dict:
    """概览数据（后台线程执行）：汇总、总资产、六个月趋势与最新 5 条。"""
    today = date.today()
    with session_scope() as session:
        return {
            "summary": month_summary(session, today.year, today.month),
            "assets": total_assets(session),
            "trend": six_month_trend(session, today),
            "recent_txs": list(
                session.scalars(
                    select(Transaction).where(Transaction.is_deleted.is_(False))
                    .order_by(Transaction.date.desc(), Transaction.id.desc())
                    .limit(5)
                )
            ),
            "tx_max_updated": session.scalar(
                select(func.max(Transaction.updated_at)).where(Transaction.is_deleted.is_(False))
            ),
            "budget": budget_service.overview(today),
            "budget_alerts": budget_service.alerts(today),
            "fixed_total": budget_service.monthly_fixed_total(),
            "upcoming": budget_service.upcoming(30, today)[:4],
        }


def _build_overview(page: ft.Page, refresh) -> ft.Control:
    # 汇总查询在后台线程；图表仍由 _render_overview 内部异步渲染。
    return background_section(
        page, _overview_query, lambda data: _render_overview(page, refresh, data)
    )


def _render_overview(page: ft.Page, refresh, data) -> ft.Control:
    summary = data["summary"]
    assets = data["assets"]
    trend = data["trend"]
    recent_txs = data["recent_txs"]
    tx_max_updated = data["tx_max_updated"]

    summary_cards = equal_height_row(
        [
            metric_card("本月收入", money(summary["income"]), height=96, expand=1),
            metric_card("本月支出", money(summary["expense"]), value_color=th.ACCENT,
                        height=96, expand=1),
            metric_card("月度结余", money(summary["balance"]), height=96, expand=1),
            metric_card("总资产", money(assets), value_color=th.PRIMARY, height=96, expand=1),
        ],
        spacing=12,
        height=96,
    )

    def entry(icon: str, title: str, subtitle: str, target: str) -> ft.Control:
        return card(
            ft.Column(
                [
                    ft.Icon(ico(icon), size=26, color=th.PRIMARY),
                    ft.Text(title, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold",
                            text_align=ft.TextAlign.CENTER, max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(subtitle, size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                            text_align=ft.TextAlign.CENTER, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                # 入口卡的高度来自父容器的弹性分配（不是 card 的固定 height），
                # 正文撑满卡片，CENTER 才能真正把图标与文字居中。
                expand=True,
            ),
            on_click=lambda e: _go(page, refresh, target),
            expand=1,
            # 正文撑满卡片，图标与两行文字才会在整张卡里居中
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    # 收支记录 / 账户列表：两张入口卡上下排列，紧接在「收支趋势」右侧，
    # 与两张图表同高、各占一半（136）；宽度用 expand 吸收多余空间，
    # 窗口变宽时不会在行尾留下空白。
    #
    # horizontal_alignment 必须是 STRETCH：Column 默认按子控件自身宽度（START）摆放，
    # 卡片会缩成文字那么窄，列里剩下的宽度就成了右侧那条空白。
    nav_stack = ft.Column(
        [
            entry("RECEIPT_LONG_OUTLINED", "收支记录", "查看、筛选与新增", "records"),
            entry("ACCOUNT_BALANCE_WALLET_OUTLINED", "账户列表", "管理账户与余额", "accounts"),
        ],
        spacing=12,
        expand=2,
        height=284,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    pie_slot = _chart_slot()
    trend_slot = _chart_slot()
    charts = ft.Row(
        [
            card(pie_slot, title="支出占比", expand=3, height=284),
            card(trend_slot, title="收支趋势", expand=4, height=284),
            nav_stack,
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        height=284,
    )

    def render_charts() -> None:
        pie_slot.content = _chart_image(
            pie_chart(summary["categories"], "支出占比", cache_key=tx_max_updated),
            320, 220,
        )
        trend_slot.content = _chart_image(
            line_chart(
                [t["label"] for t in trend],
                [{"name": "收入", "values": [t["income"] for t in trend], "color": th.PRIMARY},
                 {"name": "支出", "values": [t["expense"] for t in trend], "color": th.ACCENT}],
                "近六个月收支趋势",
                cache_key=tx_max_updated,
            ),
            420, 220,
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
        kind = _kind_code(tx.kind)
        sign = _kind_sign(kind)
        color = th.PRIMARY if kind == "income" else (th.ACCENT if kind == "expense" else th.TEXT_MUTED)
        note_label = tx.category or ("转账" if kind == "transfer" else "")
        recent_rows.append(
            ft.Row(
                [
                    ft.Text(f"{sign} {money(tx.amount)}", size=th.FONT_BODY, color=color,
                            weight="bold"),
                    ft.Text(note_label, size=th.FONT_CAPTION, color=th.TEXT_MUTED, expand=True,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(tx.date.isoformat(), size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
    recent_card = card(
        ft.Column(recent_rows, spacing=6, scroll=ft.ScrollMode.AUTO)
        if recent_rows
        else empty("暂无收支记录"),
        title="最新收支",
        expand=1,
    )

    budget_data = data.get("budget") or {}
    budget_used = budget_data.get("percent", 0)
    budget_card = card(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            f"预算 {money(budget_data.get('total_budget', 0))}",
                            size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
                        ),
                        ft.Text(f"{budget_used}%", size=th.FONT_CAPTION,
                                color=th.DANGER if budget_used >= 100 else th.PRIMARY),
                    ]
                ),
                ft.ProgressBar(
                    value=min(1.0, budget_used / 100),
                    color=th.DANGER if budget_used >= 100 else th.PRIMARY,
                    bgcolor=th.DIVIDER,
                ),
                ft.Text(
                    f"已用 {money(budget_data.get('expense', 0))} · "
                    f"剩余 {money(budget_data.get('remaining', 0))}"
                    if budget_data.get("total_budget")
                    else "还没有设置本月预算，设置后可在首页看到进度",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                *[
                    ft.Text(f"⚠ {message}", size=th.FONT_CAPTION, color=th.DANGER,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
                    for message in (data.get("budget_alerts") or [])[:2]
                ],
                ft.Row(
                    [
                        ft.TextButton("设置预算", on_click=lambda e: open_budget_dialog(page, refresh)),
                        ft.TextButton("一句话记账",
                                      on_click=lambda e: open_quick_parse_dialog(page, refresh)),
                    ],
                    spacing=4,
                    wrap=True,
                    run_spacing=4,
                ),
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        title="本月预算",
        expand=1,
    )

    upcoming_rows = [
        ft.Row(
            [
                ft.Text(item["name"], size=th.FONT_BODY, color=th.TEXT, expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(item["next_date"].isoformat(), size=th.FONT_CAPTION,
                        color=th.TEXT_MUTED),
            ],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        for item in (data.get("upcoming") or [])
    ]
    fixed_card = card(
        ft.Column(
            [
                ft.Text(
                    f"按月合计 {money(data.get('fixed_total', 0))}",
                    size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold",
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text("即将扣款", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                *(upcoming_rows or [ft.Text("最近 30 天没有固定支出", size=th.FONT_CAPTION,
                                            color=th.TEXT_MUTED)]),
                ft.Row(
                    [
                        ft.TextButton("管理固定支出",
                                      on_click=lambda e: open_recurring_dialog(page, refresh)),
                    ],
                    spacing=4,
                    wrap=True,
                ),
            ],
            spacing=6,
            scroll=ft.ScrollMode.AUTO,
        ),
        title="固定支出与订阅",
        expand=1,
    )

    return ft.Column(
        [
            page_header(
                "记账",
                f"本月概览 · 总资产 {money(assets)}",
                actions=[
                    primary_button(
                        "记一笔",
                        lambda e: open_transaction_dialog(page, "expense", None, refresh),
                        ACTIONS["add"],
                    )
                ],
            ),
            summary_cards,
            charts,
            # 最新收支 / 本月预算 / 固定支出与订阅：一行三卡，高度一致
            equal_height_row([recent_card, budget_card, fixed_card], spacing=12, height=210),
        ],
        spacing=12,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
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
        [
            year_dd,
            month_dd,
            ft.Container(expand=True),
            ft.Text(f"共 {total} 条", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    rows = [_tx_row(tx, page, refresh) for tx in page_items]

    pagination = ft.Row(
        [
            ft.TextButton("上一页", disabled=current_page == 0,
                          on_click=lambda e: (_state.__setitem__("page", max(0, current_page - 1)), refresh())),
            ft.Text(f"{current_page + 1} / {total_pages}", size=th.FONT_CAPTION,
                    color=th.TEXT_MUTED),
            ft.TextButton("下一页", disabled=current_page >= total_pages - 1,
                          on_click=lambda e: (_state.__setitem__("page", min(total_pages - 1, current_page + 1)), refresh())),
        ],
        spacing=4,
        alignment=ft.MainAxisAlignment.CENTER,
    )

    return ft.Column(
        [
            page_header(
                "记账",
                f"收支记录 · 共 {total} 条",
                actions=[
                    _back(page, refresh),
                    primary_button(
                        "记一笔",
                        lambda e: open_transaction_dialog(page, "expense", None, refresh),
                        ACTIONS["add"],
                    ),
                ],
            ),
            filter_row,
            divider(),
            ft.Column(rows, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True) if rows else empty("暂无记录"),
            pagination,
        ],
        spacing=10,
        expand=True,
    )


def _accounts_query() -> tuple[list, dict[str, float]]:
    with session_scope() as session:
        return (
            list(session.scalars(select(Account).where(Account.is_deleted.is_(False)))),
            account_balances(session),
        )


def _build_accounts(page: ft.Page, refresh) -> ft.Control:
    return background_section(
        page, _accounts_query, lambda data: _render_accounts(page, refresh, data)
    )


def _render_accounts(page: ft.Page, refresh, data) -> ft.Control:
    accounts, balances = data
    account_cards = []
    for account in accounts:
        balance = balances.get(account.id, 0)
        account_cards.append(
            card(
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(account.name, size=th.FONT_CARD_TITLE, color=th.TEXT,
                                        weight="bold", max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(
                                    f"{account.kind} · 初始 {money(account.initial_balance)}",
                                    size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=3, expand=True,
                        ),
                        ft.Text(money(balance), size=16, color=th.PRIMARY, weight="bold",
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        icon_button(ACTIONS["edit"], lambda e, a=account: open_account_dialog(page, a, refresh), "编辑"),
                        icon_button(ACTIONS["delete"], lambda e, a=account.id: _delete(Account, a, page, refresh), "删除"),
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                width=300,
                height=96,
            )
        )

    return ft.Column(
        [
            page_header(
                "记账",
                f"账户列表 · 共 {len(accounts)} 个账户",
                actions=[
                    _back(page, refresh),
                    primary_button(
                        "新增账户",
                        lambda e: open_account_dialog(page, None, refresh),
                        ACTIONS["add"],
                    ),
                ],
            ),
            ft.Row(
                account_cards,
                spacing=12,
                run_spacing=12,
                wrap=True,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
            if account_cards
            else empty("暂无账户"),
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
