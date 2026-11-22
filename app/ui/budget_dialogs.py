"""预算与周期记账弹窗：月度/分类预算、固定支出规则、一句话记账（规则版）。"""

from __future__ import annotations

import logging
import math
from datetime import date

import flet as ft
from sqlalchemy import select

from .. import theme as th
from ..db.models import Account, Transaction
from ..db.repo import create
from ..db.session import session_scope
from ..services import budget as budget_service
from ..services.expense_rules import parse_transaction
from ..services.finance import money
from ..services.sync import mark_pending_write
from .widgets import (
    close_modal,
    confirm,
    divider,
    dropdown,
    empty,
    form_field,
    modal,
    primary_button,
    snack,
)

# 预算金额上限：只为挡住明显的手误（多按了几个 0）与非有限值，不限制真实大额预算。
MAX_BUDGET_AMOUNT = 100_000_000.0

logger = logging.getLogger(__name__)

EXPENSE_CATEGORIES = [
    "餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "通讯", "人情", "其他",
]
INCOME_CATEGORIES = ["工资", "奖金", "理财", "兼职", "礼金", "报销", "其他"]


def _accounts() -> list[Account]:
    with session_scope() as session:
        return list(session.scalars(select(Account).where(Account.is_deleted.is_(False))))


def open_budget_dialog(page: ft.Page, refresh) -> ft.AlertDialog:
    month = date.today().isoformat()[:7]
    existing = {row.category: float(row.amount or 0) for row in budget_service.budgets(month)}
    total_field = form_field("月度总预算", f"{existing.get('', 0):g}" if existing.get("") else "")
    fields: dict[str, ft.TextField] = {}
    category_rows: list[ft.Control] = []
    for category in EXPENSE_CATEGORIES:
        field = form_field(category, f"{existing.get(category, 0):g}" if existing.get(category) else "",
                           width=180)
        fields[category] = field
        category_rows.append(field)

    def save(_) -> None:
        def amount_of(label: str, field: ft.TextField) -> float | None:
            """留空 = 不设置/清除该项（0 元）；填了但不是合法数字 → 报错并中止。

            旧实现把解析失败静默当成 0，而 ``upsert_budget`` 对 ≤0 是软删除——
            手误一次「1O00」就能无提示清空整月预算。
            """
            raw = (field.value or "").strip()
            if not raw:
                return 0.0
            try:
                value = float(raw)
            except ValueError:
                snack(page, f"「{label}」金额格式不正确，未保存", th.ACCENT)
                return None
            if not math.isfinite(value) or value < 0 or value > MAX_BUDGET_AMOUNT:
                snack(page, f"「{label}」金额需在 0 ~ {MAX_BUDGET_AMOUNT:g} 之间", th.ACCENT)
                return None
            return round(value, 2)

        total = amount_of("月度总预算", total_field)
        if total is None:
            return
        parsed: dict[str, float] = {}
        for category, field in fields.items():
            value = amount_of(category, field)
            if value is None:
                return
            parsed[category] = value

        try:
            budget_service.upsert_budget(month, "", total)
            for category, value in parsed.items():
                budget_service.upsert_budget(month, category, value)
        except Exception:
            logger.exception("保存预算失败")
            snack(page, "保存失败，详见日志", th.ACCENT)
            return
        mark_pending_write()
        close_modal(page, dialog)
        refresh()
        snack(page, f"已保存 {month} 预算", th.PRIMARY)

    dialog = modal(
        page,
        f"设置预算 · {month}",
        ft.Column(
            [
                total_field,
                divider(),
                ft.Text("分类预算（留空或 0 表示不限制）", size=th.FONT_CAPTION,
                        color=th.TEXT_MUTED),
                ft.Row(category_rows, wrap=True, spacing=8),
            ],
            spacing=10,
            tight=True,
        ),
        [ft.TextButton("取消", on_click=lambda e: close_modal(page, dialog)),
         primary_button("保存", save)],
        width=720,
    )
    return dialog


def open_rule_form(page: ft.Page, rule=None, on_done=None) -> ft.AlertDialog:
    accounts = _accounts()
    account_options = {
        f"{account.name}（{account.kind}）": account.id for account in accounts
    }
    default_account = (
        next(iter(account_options)) if account_options else None
    )
    if rule is not None:
        current_account = next(
            (label for label, value in account_options.items() if value == rule.account_id),
            default_account,
        )
    else:
        current_account = default_account

    name = form_field("名称 *", rule.name if rule else "")
    amount = form_field("金额", f"{float(rule.amount):g}" if rule else "", width=140)
    kind = dropdown("类型", ["支出", "收入"], "收入" if rule and rule.kind == "income" else "支出",
                    width=120)
    category = dropdown("分类", EXPENSE_CATEGORIES + INCOME_CATEGORIES,
                        (rule.category if rule and rule.category else "居住"), width=140)
    period = dropdown("周期", ["每月", "每周"],
                      "每周" if rule and rule.period == "week" else "每月", width=120)
    day = form_field("执行日（1-31 或周几 1-7）", str(rule.day) if rule else "1", width=200)
    account = dropdown("账户", list(account_options) or ["（无账户）"], current_account, width=220)
    active = ft.Checkbox(label="启用", value=bool(rule.active) if rule else True,
                         label_style=th.checkbox_label_style())
    note = form_field("备注", rule.note if rule else "")

    def save(_) -> None:
        title = (name.value or "").strip()
        if not title:
            snack(page, "请填写名称", th.ACCENT)
            return
        try:
            value = float((amount.value or "").strip())
        except ValueError:
            snack(page, "金额格式不正确", th.ACCENT)
            return
        if not math.isfinite(value) or value <= 0:
            snack(page, "金额需大于 0 且为有限数值", th.ACCENT)
            return
        if value > MAX_BUDGET_AMOUNT:
            snack(page, f"金额不能大于 {MAX_BUDGET_AMOUNT:g}", th.ACCENT)
            return
        is_week = period.value == "每周"
        day_max = 7 if is_week else 31
        try:
            day_value = int(float(day.value or 1))
        except ValueError:
            snack(page, "执行日格式不正确", th.ACCENT)
            return
        # 按周期夹取：每周只能 1-7（旧实现一律夹到 31，周规则会存进 day=20 这种非法值）
        day_value = max(1, min(day_max, day_value))
        account_id = account_options.get(account.value or "", "")
        try:
            budget_service.save_rule(
                rule.id if rule else None,
                name=title,
                amount=value,
                kind="income" if kind.value == "收入" else "expense",
                category=category.value or "",
                account_id=account_id,
                period="week" if is_week else "month",
                day=day_value,
                active=bool(active.value),
                note=note.value or "",
            )
        except Exception:
            logger.exception("保存周期规则失败")
            snack(page, "保存失败，详见日志", th.ACCENT)
            return
        close_modal(page, dialog)
        if on_done is not None:
            on_done()

    dialog = modal(
        page,
        "编辑固定支出" if rule else "新增固定支出",
        ft.Column(
            [
                name,
                ft.Row([amount, kind, category], spacing=12),
                ft.Row([period, day, account], spacing=12),
                active,
                note,
                ft.Text(
                    "到期后会自动生成真实交易（标注「周期」），最多补生成 12 期。",
                    size=th.FONT_CAPTION,
                    color=th.TEXT_MUTED,
                ),
            ],
            spacing=10,
            tight=True,
        ),
        [ft.TextButton("取消", on_click=lambda e: close_modal(page, dialog)),
         primary_button("保存", save)],
        width=680,
    )
    return dialog


def open_recurring_dialog(page: ft.Page, refresh) -> ft.AlertDialog:
    list_box = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=300)

    def reload() -> None:
        rules = budget_service.rules()
        if not rules:
            list_box.controls = [empty("还没有固定支出规则")]
        else:
            list_box.controls = [
                ft.Container(
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(rule.name, size=th.FONT_CARD_TITLE,
                                            color=th.TEXT, weight="bold",
                                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(
                                        f"{money(rule.amount)} · "
                                        f"{'每周' if rule.period == 'week' else '每月'} "
                                        f"{(f'周{rule.day}' if rule.period == 'week' else rule.day)} 日 · "
                                        f"下次 {rule.next_date.isoformat() if rule.next_date else '未定'}"
                                        + ("" if rule.active else " · 已停用"),
                                        size=th.FONT_CAPTION,
                                        color=th.TEXT_MUTED,
                                        max_lines=2,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=2,
                                expand=True,
                                tight=True,
                            ),
                            ft.TextButton(
                                "编辑",
                                on_click=lambda e, r=rule: open_rule_form(page, r, lambda: (reload(), refresh())),
                            ),
                            ft.TextButton(
                                "删除",
                                on_click=lambda e, r=rule: remove(r.id),
                            ),
                        ],
                        spacing=6,
                    ),
                    padding=ft.Padding.all(8),
                    border=ft.Border.all(1, th.DIVIDER),
                    border_radius=8,
                )
                for rule in rules
            ]
        page.update()

    def remove(rule_id: str) -> None:
        # 与任务/笔记/记账的删除保持一致：先确认再软删。
        confirm(
            page,
            "删除这条周期规则？已生成的交易不受影响。",
            lambda: _remove_confirmed(rule_id),
        )

    def _remove_confirmed(rule_id: str) -> None:
        budget_service.delete_rule(rule_id)
        reload()
        refresh()

    def generate_now(_) -> None:
        created = budget_service.generate_due()
        snack(page, f"已生成 {created} 条交易" if created else "没有到期的规则", th.PRIMARY)
        reload()
        refresh()

    dialog = modal(
        page,
        "固定支出与订阅",
        ft.Column(
            [
                ft.Text(
                    f"按月合计约 {money(budget_service.monthly_fixed_total())}",
                    size=th.FONT_BODY,
                    color=th.TEXT_MUTED,
                ),
                list_box,
                ft.Row(
                    [
                        primary_button(
                            "新增规则",
                            lambda e: open_rule_form(page, None, lambda: (reload(), refresh())),
                            "add",
                        ),
                        ft.TextButton("立即生成到期交易", on_click=generate_now),
                    ],
                    spacing=8,
                ),
            ],
            spacing=10,
            tight=True,
        ),
        [ft.TextButton("关闭", on_click=lambda e: close_modal(page, dialog))],
        width=680,
    )
    reload()
    return dialog


def open_quick_parse_dialog(page: ft.Page, refresh) -> ft.AlertDialog:
    """规则版「一句话记账」：不依赖 AI，解析金额/分类/日期后确认保存。"""
    text = form_field("用一句话记账，例如「今天午饭花了 32 元」", "", multiline=True)
    preview = ft.Text("", size=th.FONT_BODY, color=th.TEXT_MUTED, max_lines=2,
                      overflow=ft.TextOverflow.ELLIPSIS)

    def parse(_) -> dict | None:
        data = parse_transaction(text.value or "")
        if data is None:
            preview.value = "没有识别到金额，请补充数字（如 32 元）"
            preview.color = th.ACCENT
            page.update()
            return None
        preview.value = (
            f"分类：{data['category']} · 金额：{money(data['amount'])} · "
            f"日期：{data['date']} · {'收入' if data['kind'] == 'income' else '支出'}"
        )
        preview.color = th.PRIMARY
        page.update()
        return data

    def save(_) -> None:
        data = parse(_)
        if data is None:
            return
        with session_scope() as session:
            account = session.scalar(
                select(Account).where(Account.is_deleted.is_(False)).order_by(Account.created_at)
            )
            if account is None:
                snack(page, "请先在记账里添加一个账户", th.ACCENT)
                return
            create(
                session,
                Transaction,
                kind=data["kind"],
                amount=data["amount"],
                category=data["category"],
                account_id=account.id,
                date=date.fromisoformat(data["date"]),
                note=data["note"],
            )
        mark_pending_write()
        close_modal(page, dialog)
        refresh()
        snack(page, f"已记录 {money(data['amount'])}", th.PRIMARY)

    dialog = modal(
        page,
        "一句话记账",
        ft.Column([text, preview], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: close_modal(page, dialog)),
         ft.TextButton("识别", on_click=parse),
         primary_button("保存", save)],
        width=560,
    )
    return dialog
