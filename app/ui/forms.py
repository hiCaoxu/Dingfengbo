"""各类录入/编辑弹窗。所有写操作先落本地库，再安排同步。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime

import flet as ft

from sqlalchemy import select

from ..config import DATA_DIR, load_settings, save_settings
from ..db.models import (
    Account,
    Diet,
    Entertainment,
    Exercise,
    Note,
    Sleep,
    Todo,
    Transaction,
    Weight,
)
from ..db.repo import create, update
from ..db.session import session_scope
from ..icons import ico
from ..services.dates import diary_title, lunar_to_gregorian, parse_hours, parse_lunar
from ..services.health import calorie_of, estimate_exercise_calories, food_options
from ..services.sync import mark_pending_write
from ..theme import DIVIDER, TEXT, TEXT_MUTED
from .widgets import close_modal, dropdown, form_field, modal, primary_button, snack


def _tags(value: str) -> str:
    parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
    return json.dumps(parts, ensure_ascii=False)


def tags_text(raw: str | None) -> str:
    try:
        return ", ".join(json.loads(raw or "[]"))
    except (ValueError, TypeError):
        return ""


def _date(value: str | None, default: date | None = None) -> date | None:
    value = (value or "").strip()
    if not value:
        return default
    return date.fromisoformat(value)


def _close_and_refresh(page, dialog, on_done):
    dialog.open = False
    page.update()
    if on_done:
        on_done()


# ---------------------------------------------------------------- 待办

def open_todo_dialog(page: ft.Page, todo: Todo | None = None, on_done: Callable | None = None):
    title = form_field("标题 *", todo.title if todo else "")
    note = form_field("备注", todo.note if todo else "", multiline=True)
    is_lunar = bool(todo and todo.lunar_reminder)
    due_type = dropdown("日期类型", ["公历", "农历"], "农历" if is_lunar else "公历", width=120)
    due = form_field(
        "农历日期（如 九月初九）" if is_lunar else "到期日期 YYYY-MM-DD",
        (todo.lunar_reminder if is_lunar else (todo.due_date.isoformat() if todo and todo.due_date else "")),
        width=380,
    )
    tags = form_field("标签（逗号分隔）", tags_text(todo.tags) if todo else "")
    repeat = dropdown("循环规则", ["不循环", "不定期", "按天", "按周", "按月", "按年"],
                      {"none": "不循环", "irregular": "不定期", "day": "按天", "week": "按周", "month": "按月", "year": "按年"}.get(todo.repeat_rule, "不循环") if todo else "不循环")
    priority = dropdown("优先级", ["", "低", "中", "高"], todo.priority if todo and todo.priority else "")

    def on_type_change(e):
        lunar = e.control.value == "农历"
        due.label = "农历日期（如 九月初九）" if lunar else "到期日期 YYYY-MM-DD"
        due.hint_text = "九月初九 / 9-9" if lunar else "2026-12-31"
        due.update()

    due_type.on_select = on_type_change

    def save(_):
        if not title.value or not title.value.strip():
            snack(page, "请填写标题", "#C97B63")
            return
        if due_type.value == "农历":
            raw = (due.value or "").strip()
            if not raw:
                due_date, lunar_reminder = None, ""
            else:
                parsed = parse_lunar(raw)
                if not parsed:
                    snack(page, "农历日期格式不正确，例如「九月初九」或「9-9」", "#C97B63")
                    return
                due_date = lunar_to_gregorian(*parsed)
                if due_date is None:
                    snack(page, "农历日期无效", "#C97B63")
                    return
                lunar_reminder = raw
        else:
            try:
                due_date = _date(due.value)
            except ValueError:
                snack(page, "到期日期格式不正确", "#C97B63")
                return
            lunar_reminder = ""
        rule_map = {"不循环": "none", "不定期": "irregular", "按天": "day", "按周": "week", "按月": "month", "按年": "year"}
        fields = dict(
            title=title.value.strip(),
            note=note.value or "",
            due_date=due_date,
            tags=_tags(tags.value or ""),
            lunar_reminder=lunar_reminder,
            repeat_rule=rule_map.get(repeat.value or "不循环", "none"),
            priority=priority.value or "",
        )
        with session_scope() as session:
            if todo:
                target = session.get(Todo, todo.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Todo, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page,
        "编辑待办" if todo else "新增待办",
        ft.Column([title, note, ft.Row([due_type, due], spacing=12), tags, ft.Row([repeat, priority], spacing=12)], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
    )
    return dialog


# ---------------------------------------------------------------- 笔记

def open_note_dialog(page: ft.Page, note: Note | None = None, on_done: Callable | None = None):
    title = form_field("标题 *", note.title if note else "")
    tags = form_field("标签（逗号分隔）", tags_text(note.tags) if note else "")
    content = form_field("Markdown 正文", note.content if note else "", multiline=True)

    def save(_):
        if not (title.value or "").strip():
            snack(page, "请填写标题", "#C97B63")
            return
        with session_scope() as session:
            fields = dict(title=title.value.strip(), tags=_tags(tags.value or ""), content=content.value or "")
            if note:
                target = session.get(Note, note.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Note, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page,
        "编辑笔记" if note else "新增笔记",
        ft.Column([title, tags, content], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
        width=640,
    )
    return dialog


# ---------------------------------------------------------------- 记账

def open_account_dialog(page: ft.Page, account: Account | None = None, on_done: Callable | None = None):
    name = form_field("账户名称 *", account.name if account else "")
    kind = dropdown("类型", ["现金", "银行卡", "电子钱包", "储蓄", "借贷"], account.kind if account else "银行卡")
    balance = form_field("初始余额", str(account.initial_balance) if account else "0.00")

    def save(_):
        if not (name.value or "").strip():
            snack(page, "请填写账户名称", "#C97B63")
            return
        try:
            amount = float(balance.value or 0)
        except ValueError:
            snack(page, "余额格式不正确", "#C97B63")
            return
        with session_scope() as session:
            fields = dict(name=name.value.strip(), kind=kind.value, initial_balance=amount)
            if account:
                target = session.get(Account, account.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Account, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "编辑账户" if account else "新增账户",
        ft.Column([name, kind, balance], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
    )
    return dialog


def open_transaction_dialog(page: ft.Page, kind: str = "expense", tx: Transaction | None = None, on_done: Callable | None = None):
    with session_scope() as session:
        accounts = [(a.id, a.name) for a in session.scalars(select(Account).where(Account.is_deleted.is_(False)))]
    if not accounts:
        snack(page, "请先新增账户", "#C97B63")
        return None
    account_ids = [str(account_id) for account_id, _ in accounts]
    kind_value = tx.kind if tx else kind
    kind_dd = dropdown("类型", ["支出", "收入", "转账"], {"expense": "支出", "income": "收入", "transfer": "转账"}[kind_value])
    amount = form_field("金额 *", str(tx.amount) if tx else "")
    category = form_field("分类", tx.category if tx else "", width=220)
    initial_account = str(tx.account_id) if tx and tx.account_id in account_ids else account_ids[0]
    initial_to = str(tx.to_account_id) if tx and tx.to_account_id in account_ids else next(
        (account_id for account_id in account_ids if account_id != initial_account), initial_account
    )

    def account_dropdown(label: str, value: str) -> ft.Dropdown:
        control = dropdown(label, account_ids, value, width=220)
        control.options = [
            ft.dropdown.Option(key=str(account_id), text=name) for account_id, name in accounts
        ]
        return control

    account_dd = account_dropdown("账户", initial_account)
    to_dd = account_dropdown("转入账户", initial_to)
    date_field = form_field("日期 YYYY-MM-DD", tx.date.isoformat() if tx else date.today().isoformat(), width=220)
    note = form_field("备注", tx.note if tx else "")

    def save(_):
        try:
            value = float(amount.value or 0)
        except ValueError:
            snack(page, "金额格式不正确", "#C97B63")
            return
        if value <= 0:
            snack(page, "金额需大于 0", "#C97B63")
            return
        try:
            record_date = _date(date_field.value, date.today()) or date.today()
        except ValueError:
            snack(page, "日期格式不正确", "#C97B63")
            return
        mapped = {"支出": "expense", "收入": "income", "转账": "transfer"}[kind_dd.value]
        account_id = str(account_dd.value)
        if mapped == "transfer" and (
            len(accounts) < 2 or not to_dd.value or str(to_dd.value) == account_id
        ):
            snack(page, "转账需要选择两个不同账户", "#C97B63")
            return
        fields = dict(
            kind=mapped,
            amount=value,
            category=category.value or ("转账" if mapped == "transfer" else "其他"),
            account_id=account_id,
            to_account_id=str(to_dd.value) if mapped == "transfer" else None,
            date=record_date,
            note=note.value or "",
        )
        with session_scope() as session:
            if tx:
                target = session.get(Transaction, tx.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Transaction, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "编辑记录" if tx else "新增记录",
        ft.Column(
            [ft.Row([kind_dd, amount], spacing=12), ft.Row([category, account_dd, to_dd], spacing=12),
             ft.Row([date_field], spacing=12), note],
            spacing=10, tight=True,
        ),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
        width=640,
    )
    return dialog


# ---------------------------------------------------------------- 健康

def open_exercise_dialog(page: ft.Page, item: Exercise | None = None, on_done: Callable | None = None):
    kinds = ["跑步", "快走", "骑行", "游泳", "力量训练", "瑜伽", "跳绳", "爬楼"]
    kind = dropdown("运动类型", kinds, item.kind if item else "跑步")
    minutes = form_field("时长（分钟）", str(item.minutes) if item else "30")
    distance = form_field("距离 km（可选）", str(item.distance_km) if item and item.distance_km else "")
    calories = form_field("消耗 kcal（可手填）", str(item.calories) if item else "")
    note = form_field("备注", item.note if item else "")
    day = form_field("日期 YYYY-MM-DD", item.date.isoformat() if item else date.today().isoformat())

    def save(_):
        try:
            mins = int(float(minutes.value or 0))
        except ValueError:
            snack(page, "时长格式不正确", "#C97B63")
            return
        try:
            kcal = float(calories.value) if calories.value else estimate_exercise_calories(kind.value, mins)
            record_date = _date(day.value, date.today()) or date.today()
            distance_km = float(distance.value) if distance.value else None
        except ValueError:
            snack(page, "日期、距离或热量格式不正确", "#C97B63")
            return
        with session_scope() as session:
            fields = dict(
                date=record_date, kind=kind.value, minutes=mins,
                distance_km=distance_km,
                calories=kcal, note=note.value or "",
            )
            if item:
                target = session.get(Exercise, item.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Exercise, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "记录锻炼" if not item else "编辑锻炼",
        ft.Column([ft.Row([kind, minutes], spacing=12), ft.Row([distance, calories], spacing=12), day, note], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
    )
    return dialog


def open_diet_dialog(page: ft.Page, item: Diet | None = None, on_done: Callable | None = None):
    with session_scope() as session:
        options = [f.name for f in food_options(session)]
    meal = dropdown("餐次", ["早餐", "午餐", "晚餐", "加餐"], item.meal if item else "早餐")
    food = dropdown("食物", options or ["米饭"], item.food if item and item.food in options else (options[0] if options else "米饭"))
    grams = form_field("重量 g", str(int(item.grams)) if item else "100")
    calories = form_field("热量 kcal", str(item.calories) if item else "")
    note = form_field("备注", item.note if item else "")
    day = form_field("日期 YYYY-MM-DD", item.date.isoformat() if item else date.today().isoformat())

    def recalc(_):
        with session_scope() as session:
            try:
                calories.value = str(calorie_of(session, food.value or "", float(grams.value or 0)))
            except ValueError:
                calories.value = "0"
        calories.error_text = None
        page.update()

    def save(_):
        try:
            weight = float(grams.value or 0)
            record_date = _date(day.value, date.today()) or date.today()
            if (calories.value or "").strip():
                kcal = float(calories.value or 0)
            else:
                with session_scope() as session:
                    kcal = calorie_of(session, food.value or "", weight)
        except ValueError:
            snack(page, "请输入正确的日期、重量和热量", "#C97B63")
            return
        with session_scope() as session:
            fields = dict(date=record_date, meal=meal.value,
                          food=food.value, grams=weight, calories=kcal, note=note.value or "")
            if item:
                target = session.get(Diet, item.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Diet, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "记录饮食" if not item else "编辑饮食",
        ft.Column([ft.Row([meal, food], spacing=12), ft.Row([grams, calories], spacing=12), day, note,
                   ft.Text("热量按《中国食物成分表（第 6 版）》每 100g 可食部自动计算，可手动覆盖。", size=11, color=TEXT_MUTED)],
                  spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)),
         ft.TextButton("重算热量", on_click=recalc), primary_button("保存", save)],
    )
    return dialog


def open_weight_dialog(page: ft.Page, item: Weight | None = None, on_done: Callable | None = None):
    kg = form_field("体重 kg *", str(item.kg) if item else "")
    fat = form_field("体脂率 %（可选）", str(item.body_fat) if item and item.body_fat else "")
    day = form_field("日期 YYYY-MM-DD", item.date.isoformat() if item else date.today().isoformat())

    def save(_):
        try:
            weight = float(kg.value or 0)
        except ValueError:
            snack(page, "体重格式不正确", "#C97B63")
            return
        if weight <= 0:
            snack(page, "体重需大于 0", "#C97B63")
            return
        try:
            record_date = _date(day.value, date.today()) or date.today()
            body_fat = float(fat.value) if fat.value else None
        except ValueError:
            snack(page, "日期或体脂率格式不正确", "#C97B63")
            return
        with session_scope() as session:
            fields = dict(date=record_date, kg=weight, body_fat=body_fat)
            if item:
                target = session.get(Weight, item.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Weight, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "记录体重" if not item else "编辑体重",
        ft.Column([kg, fat, day], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
    )
    return dialog


def open_sleep_dialog(page: ft.Page, item: Sleep | None = None, on_done: Callable | None = None):
    bedtime = form_field("入睡时间 HH:MM", item.bedtime if item else "23:00")
    wake = form_field("醒来时间 HH:MM", item.wake_time if item else "07:00")
    quality = dropdown("质量", ["1", "2", "3", "4", "5"], str(item.quality) if item else "3")
    day = form_field("日期 YYYY-MM-DD", item.date.isoformat() if item else date.today().isoformat())

    def save(_):
        hours = parse_hours(bedtime.value or "", wake.value or "")
        if hours == 0:
            dialog.open = False
            page.update()
            snack(page, "时间格式应为 HH:MM", "#C97B63")
            return
        try:
            record_date = _date(day.value, date.today()) or date.today()
        except ValueError:
            dialog.open = False
            page.update()
            snack(page, "日期格式不正确", "#C97B63")
            return
        try:
            quality_val = int(quality.value)
        except (TypeError, ValueError):
            quality_val = 3
        with session_scope() as session:
            fields = dict(date=record_date, bedtime=bedtime.value or "",
                          wake_time=wake.value or "", hours=hours, quality=quality_val)
            if item:
                target = session.get(Sleep, item.id)
                if target:
                    update(session, target, **fields)
            else:
                existing = session.scalar(
                    select(Sleep).where(Sleep.is_deleted.is_(False), Sleep.date == record_date)
                )
                if existing:
                    update(session, existing, **fields)
                else:
                    create(session, Sleep, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "记录睡眠" if not item else "编辑睡眠",
        ft.Column([ft.Row([bedtime, wake], spacing=12), quality, day], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
    )
    return dialog


# ---------------------------------------------------------------- 娱乐

def open_entertainment_dialog(page: ft.Page, item: Entertainment | None = None, on_done: Callable | None = None):
    title = form_field("标题 *", item.title if item else "")
    kind = dropdown("类型", ["电影", "电视剧", "纪录片", "播客", "书籍"], item.kind if item else "电影")
    status = dropdown("状态", ["想看", "在看", "看完"], item.status if item else "想看")
    cover = form_field("封面 URL 或本地路径", item.cover if item else "")
    rating = form_field("评分（0-10，可选）", str(item.rating) if item and item.rating else "")
    note = form_field("简短备注", item.note if item else "", multiline=True)

    def save(_):
        if not (title.value or "").strip():
            snack(page, "请填写标题", "#C97B63")
            return
        try:
            score = float(rating.value) if rating.value else None
        except ValueError:
            score = None
        with session_scope() as session:
            fields = dict(title=title.value.strip(), kind=kind.value, status=status.value,
                          cover=cover.value or "", rating=score, note=note.value or "")
            if item:
                target = session.get(Entertainment, item.id)
                if target:
                    update(session, target, **fields)
            else:
                create(session, Entertainment, **fields)
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "编辑条目" if item else "新增条目",
        ft.Column([title, ft.Row([kind, status, rating], spacing=12), cover, note], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
        width=620,
    )
    return dialog


# ---------------------------------------------------------------- 设置

def open_settings_dialog(page: ft.Page, on_done: Callable | None = None, refresh_status=None):
    settings = load_settings()
    server = form_field("同步服务地址", settings.get("server_url", ""))
    token = form_field("同步令牌", settings.get("sync_token", ""))
    height = form_field("身高 cm", str(settings.get("height_cm", 165)))
    gender = dropdown("性别", ["男", "女"], settings.get("gender", "男"))
    enabled = ft.Checkbox(label="启用自动同步", value=bool(settings.get("sync_enabled", True)))

    def save(_):
        try:
            h = float(height.value or 165)
        except ValueError:
            h = 165.0
        save_settings(dict(server_url=server.value or "", sync_token=token.value or "",
                           height_cm=h, gender=gender.value, sync_enabled=enabled.value))
        if refresh_status:
            refresh_status()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "设置",
        ft.Column([server, token, ft.Row([height, gender], spacing=12), enabled,
                   ft.Text(f"数据目录：{DATA_DIR}", size=11, color=TEXT_MUTED)],
                  spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
    )
    return dialog


def open_quick_action(page: ft.Page, action: str, on_done: Callable | None = None):
    return {
        "todo": open_todo_dialog,
        "note": open_note_dialog,
        "finance": lambda p, _item=None, on_done=None: open_transaction_dialog(p, "expense", None, on_done),
        "exercise": open_exercise_dialog,
        "diet": open_diet_dialog,
        "weight": open_weight_dialog,
        "sleep": open_sleep_dialog,
    }[action](page, None, on_done)
