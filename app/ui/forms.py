"""各类录入/编辑弹窗。所有写操作先落本地库，再安排同步。"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Callable
from datetime import date, datetime

import flet as ft

from sqlalchemy import select

from ..config import (
    AI_PROVIDERS,
    DATA_DIR,
    DEFAULT_HEIGHT_CM,
    DEFAULT_SERVER_URL,
    InsufficientSecretProtection,
    SettingsUnreadable,
    dpapi_available,
    load_settings,
    save_settings,
    secrets_protection,
)
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
    table_label,
)
from ..db.backup import BACKUP_DIR, backup_database
from ..db.repo import (
    create,
    list_sync_issues,
    retry_rejected,
    sync_queue_stats,
    update,
)
from ..db.session import session_scope
from ..icons import ico
from ..services.ai.client import AIConfigError, chat
from ..services.ai.provider import AIError
from ..services.todo_views import normalize_priority
from ..services.dates import (
    diary_title,
    lunar_to_gregorian,
    parse_hours,
    parse_lunar,
    valid_hm,
)
from ..services.reminders import parse_offsets as reminder_offsets
from ..services.recurrence import (
    MONTH_MODE_LABELS,
    NTH_BY_LABEL,
    NTH_LABELS,
    UNIT_BY_LABEL,
    UNIT_LABELS,
    WEEKDAY_INDEX,
    WEEKDAY_LABELS,
    WEEKDAY_SHORT,
    YEAR_MODE_LABELS,
    normalize_config,
)
from ..services.todo_rules import (
    REPEAT_LABELS,
    RULE_BY_LABEL,
    RULE_OPTIONS,
    parse_config,
    serialize_config,
)
from ..services.covers import normalize as normalize_cover
from ..services.images import (
    UploadError as ImageUploadError,
    absolute_url as absolute_image_url,
    import_remote as import_remote_image,
)
from ..services.health import (
    calorie_of,
    estimate_exercise_calories,
    food_options,
    latest_weight_kg,
)
from ..services.sync import mark_pending_write, probe
from .. import theme as th
from .uploads import upload_button
from .widgets import (
    close_modal,
    dropdown,
    form_field,
    modal,
    primary_button,
    snack,
    tag_chip,
)

logger = logging.getLogger(__name__)

# 娱乐类型单一来源：录入弹窗与看板筛选共用，避免两处漏改。
ENTERTAINMENT_KINDS = ["电影", "电视剧", "纪录片", "播客", "书籍"]


def _tags(value: str) -> str:
    parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
    return json.dumps(parts, ensure_ascii=False)


def tags_text(raw: str | None) -> str:
    try:
        return ", ".join(json.loads(raw or "[]"))
    except (ValueError, TypeError) as exc:
        logger.debug("tags_text：忽略 %s", type(exc).__name__)
        return ""


def _date(value: str | None, default: date | None = None) -> date | None:
    value = (value or "").strip()
    if not value:
        return default
    return date.fromisoformat(value)


MAX_NUMBER = 1_000_000.0


def _number(
    raw: str | None,
    *,
    label: str,
    minimum: float = 0.0,
    maximum: float = MAX_NUMBER,
    allow_empty: bool = True,
) -> tuple[float | None, str | None]:
    """解析数值输入，返回 ``(值, 错误说明)``。

    空输入：``allow_empty=True`` → ``(None, None)``（由调用方决定用默认值）；
    ``allow_empty=False``（必填）→ ``(None, "<label>不能为空")``。
    非法 / 非有限 / 越界 → ``(None, 错误)``。``float()`` 会接受 "nan" 与 "inf"，
    必须显式挡掉：nan 会一路污染金额与统计，inf 在取整（``int(inf)``）时还会直接抛
    OverflowError。
    """
    text = str(raw or "").strip()
    if not text:
        # 必填字段（allow_empty=False）必须返回**错误**而不是 0.0：返 0 会让
        # 调用方拿到一个"合法"的 0 继续往下走（例如金额 0、体重 0 落库），
        # 也会让 round(None)/int(None) 这类路径在别的字段上炸掉。
        return (None, None) if allow_empty else (None, f"{label}不能为空")
    try:
        value = float(text)
    except ValueError as exc:
        logger.debug("_number：忽略 %s", type(exc).__name__)
        return None, f"{label}格式不正确"
    if not math.isfinite(value):
        return None, f"{label}必须是有限数值"
    if value < minimum:
        return None, f"{label}不能小于 {minimum:g}"
    if value > maximum:
        return None, f"{label}不能大于 {maximum:g}"
    return value, None


def _close_and_refresh(page, dialog, on_done):
    # close_modal 会同时把弹窗移出 overlay，避免开窗次数增长后控件树膨胀。
    close_modal(page, dialog)
    if on_done:
        on_done()


class SaveTargetMissing(RuntimeError):
    """编辑目标已不存在或已删除：不能静默新建一条「看起来保存成功」的记录。"""


def save_record(model, record_id: str | None, fields: dict, resolve=None) -> str:
    """统一的「取-改 / 建 + 标脏」骨架，返回记录 id。

    ``resolve`` 只在**新建**时按业务键查找既有记录（如睡眠按日期去重）。
    编辑（``record_id`` 非空）时目标必须存在且未删除，否则抛 ``SaveTargetMissing``：
    以前这种情况会照常 update 墓碑行（保存成功但列表里看不到），或直接新建一条
    重复记录（用户以为改的是原来那条）。
    """
    with session_scope() as session:
        if record_id:
            target = session.get(model, record_id)
            if target is None or target.is_deleted:
                raise SaveTargetMissing(
                    f"{table_label(model.__tablename__)}记录已不存在（可能已被删除），"
                    "请取消后重新创建"
                )
            update(session, target, **fields)
            return str(target.id)
        target = resolve(session) if resolve is not None else None
        if target is not None:
            update(session, target, **fields)
            return str(target.id)
        return str(create(session, model, **fields).id)


def _save_or_alert(page, model, record_id, fields, resolve=None) -> bool:
    """保存并就地提示失败（目标已失效），返回是否成功。

    失败时**不标脏、不关弹窗**：用户还能看到自己填的内容，可以直接取消或重建。
    """
    try:
        save_record(model, record_id, fields, resolve=resolve)
    except SaveTargetMissing as exc:
        logger.debug("_save_or_alert：忽略 %s", type(exc).__name__)
        snack(page, str(exc), th.ACCENT)
        return False
    return True


# ---------------------------------------------------------------- 待办

def open_todo_dialog(page: ft.Page, todo: Todo | None = None, on_done: Callable | None = None):
    title = form_field("标题 *", todo.title if todo else "")
    note = form_field("备注", todo.note if todo else "", multiline=True)
    is_lunar = bool(todo and todo.lunar_reminder)
    due_type = dropdown("日期类型", ["公历", "农历"], "农历" if is_lunar else "公历", width=120)
    due = form_field(
        "农历日期（如 九月初九）" if is_lunar else "到期日期 YYYY-MM-DD",
        (todo.lunar_reminder if is_lunar else (todo.due_date.isoformat() if todo and todo.due_date else "")),
        width=270,
    )
    due_time = form_field("到期时间 HH:MM（可选）", todo.due_time if todo else "", width=160)
    # 优先级：库里存英文码，界面显示中文（normalize_priority 兼容历史的「高/中/低」）
    priority = dropdown(
        "优先级",
        [
            ft.dropdown.Option(key=code, text=label)
            for code, label in (("", "无"), ("low", "低"), ("medium", "中"), ("high", "高"))
        ],
        normalize_priority(todo.priority if todo else ""),
        width=130,
    )
    tags = form_field("标签（逗号分隔）", tags_text(todo.tags) if todo else "", width=220)
    # 四象限不再手选：统一按「优先级高/中 = 重要，今明到期/逾期 = 紧急」自动推导。

    cfg = normalize_config(parse_config(todo.repeat_config)) if todo else {}
    repeat = dropdown("循环规则", RULE_OPTIONS, REPEAT_LABELS.get(todo.repeat_rule, "不循环") if todo else "不循环", width=160)

    # ---- 自定义循环配置（参考「到期重复」面板：每 N 天/周/月/年 + 跳过双休日/节假日）
    selected: dict = {
        "weekdays": {
            int(day) for day in cfg.get("weekdays") or [] if str(day).isdigit()
        },
        "month_days": {
            int(day) for day in cfg.get("month_days") or [] if str(day).isdigit()
        },
        "month_last_day": bool(cfg.get("month_last_day")),
        "month_mode": str(cfg.get("month_mode") or "day"),
        "year_mode": str(cfg.get("year_mode") or "date"),
    }
    if not selected["weekdays"] and todo and todo.due_date is not None:
        selected["weekdays"] = {todo.due_date.weekday()}

    interval_field = form_field("间隔", str(cfg.get("interval") or 1), width=100)
    unit_dd = dropdown(
        "单位", list(UNIT_LABELS.values()), UNIT_LABELS.get(str(cfg.get("unit") or "day"), "天"), width=120
    )
    skip_weekends_cb = ft.Checkbox(label="跳过双休日", value=bool(cfg.get("skip_weekends")),
                                   label_style=th.checkbox_label_style())
    skip_holidays_cb = ft.Checkbox(label="跳过法定节假日", value=bool(cfg.get("skip_holidays")),
                                   label_style=th.checkbox_label_style())

    weekday_row = ft.Row(spacing=6, wrap=True)
    month_day_row = ft.Row(spacing=6, wrap=True)
    month_mode_row = ft.Row(spacing=6, wrap=True)
    year_mode_row = ft.Row(spacing=6, wrap=True)

    month_nth_dd = dropdown(
        "序次", list(NTH_LABELS.values()),
        NTH_LABELS.get(str(cfg.get("month_nth") or "first"), "第一个"), width=140,
    )
    month_weekday_dd = dropdown(
        "星期", WEEKDAY_LABELS,
        WEEKDAY_LABELS[int(cfg.get("month_weekday") or 0) % 7], width=130,
    )
    workday_nth_dd = dropdown(
        "工作日", ["第一个工作日", "最后一个工作日"],
        "最后一个工作日" if str(cfg.get("month_nth")) == "last" else "第一个工作日", width=180,
    )
    year_nth_dd = dropdown(
        "序次", list(NTH_LABELS.values()),
        NTH_LABELS.get(str(cfg.get("year_nth") or "first"), "第一个"), width=140,
    )
    year_weekday_dd = dropdown(
        "星期", WEEKDAY_LABELS,
        WEEKDAY_LABELS[int(cfg.get("year_weekday") or 0) % 7], width=130,
    )
    today_ref = todo.due_date if todo and todo.due_date else date.today()
    year_month_dd = dropdown(
        "月份", [f"{m}月" for m in range(1, 13)],
        f"{int(cfg.get('year_month') or today_ref.month)}月", width=110,
    )
    year_day_dd = dropdown(
        "日期", [f"{d}日" for d in range(1, 32)],
        f"{int(cfg.get('year_day') or today_ref.day)}日", width=110,
    )

    def render_weekdays() -> None:
        weekday_row.controls = [
            tag_chip(
                WEEKDAY_SHORT[index],
                index in selected["weekdays"],
                lambda e, i=index: toggle_weekday(i),
            )
            for index in range(7)
        ]

    def toggle_weekday(index: int) -> None:
        if index in selected["weekdays"]:
            selected["weekdays"].discard(index)
        else:
            selected["weekdays"].add(index)
        render_weekdays()
        weekday_row.update()
        page.update()

    def render_month_days() -> None:
        controls: list[ft.Control] = [
            tag_chip(
                str(day),
                day in selected["month_days"],
                lambda e, d=day: toggle_month_day(d),
            )
            for day in range(1, 32)
        ]
        controls.append(
            tag_chip("最后一天", selected["month_last_day"], lambda e: toggle_last_day())
        )
        month_day_row.controls = controls

    def toggle_month_day(day: int) -> None:
        if day in selected["month_days"]:
            selected["month_days"].discard(day)
        else:
            selected["month_days"].add(day)
        render_month_days()
        month_day_row.update()
        page.update()

    def toggle_last_day() -> None:
        selected["month_last_day"] = not selected["month_last_day"]
        render_month_days()
        month_day_row.update()
        page.update()

    def render_month_modes() -> None:
        month_mode_row.controls = [
            tag_chip(label, selected["month_mode"] == mode, lambda e, m=mode: set_month_mode(m))
            for mode, label in MONTH_MODE_LABELS.items()
        ]

    def set_month_mode(mode: str) -> None:
        selected["month_mode"] = mode
        render_month_modes()
        update_panels()
        page.update()

    def render_year_modes() -> None:
        year_mode_row.controls = [
            tag_chip(label, selected["year_mode"] == mode, lambda e, m=mode: set_year_mode(m))
            for mode, label in YEAR_MODE_LABELS.items()
        ]

    def set_year_mode(mode: str) -> None:
        selected["year_mode"] = mode
        render_year_modes()
        update_panels()
        page.update()

    render_weekdays()
    render_month_days()
    render_month_modes()
    render_year_modes()

    month_day_panel = ft.Column([month_day_row], spacing=6)
    month_weekday_panel = ft.Row([month_nth_dd, month_weekday_dd], spacing=12)
    month_workday_panel = ft.Row([workday_nth_dd], spacing=12)
    month_panel = ft.Column(
        [month_mode_row, month_day_panel, month_weekday_panel, month_workday_panel],
        spacing=8,
    )
    year_date_panel = ft.Row([year_month_dd, year_day_dd], spacing=12)
    year_weekday_panel = ft.Row([year_nth_dd, year_weekday_dd], spacing=12)
    year_panel = ft.Column([year_mode_row, year_date_panel, year_weekday_panel], spacing=8)
    weekday_panel = ft.Row([weekday_row], spacing=8)

    custom_panel = ft.Column(
        [
            ft.Row([interval_field, unit_dd], spacing=12),
            weekday_panel,
            month_panel,
            year_panel,
            ft.Row([skip_weekends_cb, skip_holidays_cb], spacing=12, wrap=True),
        ],
        spacing=8,
    )

    panel_holder: dict[str, ft.Container | None] = {"panel": None}

    def update_panels() -> None:
        unit = UNIT_BY_LABEL.get(unit_dd.value or "天", "day")
        weekday_panel.visible = unit == "week"
        month_panel.visible = unit == "month"
        year_panel.visible = unit == "year"
        month_mode = selected["month_mode"]
        month_day_panel.visible = month_mode == "day"
        month_weekday_panel.visible = month_mode == "weekday"
        month_workday_panel.visible = month_mode == "workday"
        year_date_panel.visible = selected["year_mode"] == "date"
        year_weekday_panel.visible = selected["year_mode"] == "weekday"
        custom_panel.visible = repeat.value == "自定义"
        # 展开自定义面板时固定高度并内部滚动，否则「不循环」时弹窗会无故变很高。
        panel = panel_holder["panel"]
        if panel is not None:
            panel.height = 520 if custom_panel.visible else None

    update_panels()
    repeat.on_select = lambda e: (update_panels(), page.update())
    unit_dd.on_select = lambda e: (update_panels(), page.update())

    # 提前提醒：多选（到期当天 / 提前 1 / 3 / 7 天），不选即不提醒
    remind_offsets: set[int] = set(reminder_offsets(todo)) if todo is not None else set()
    remind_options = [(0, "到期当天"), (1, "提前 1 天"), (3, "提前 3 天"), (7, "提前 7 天")]
    remind_chip_row = ft.Row(spacing=6, wrap=True)

    def render_remind_chips() -> None:
        remind_chip_row.controls = [
            tag_chip(
                label,
                offset in remind_offsets,
                lambda e, value=offset: toggle_remind(value),
            )
            for offset, label in remind_options
        ]

    def toggle_remind(offset: int) -> None:
        if offset in remind_offsets:
            remind_offsets.discard(offset)
        else:
            remind_offsets.add(offset)
        render_remind_chips()
        remind_chip_row.update()
        page.update()

    render_remind_chips()
    remind_time = form_field("提醒时间 HH:MM（可选）", todo.remind_time if todo else "", width=190)

    def on_type_change(e):
        lunar = e.control.value == "农历"
        due.label = "农历日期（如 九月初九）" if lunar else "到期日期 YYYY-MM-DD"
        due.hint_text = "九月初九 / 9-9" if lunar else "2026-12-31"
        due.update()

    due_type.on_select = on_type_change

    def save(_):
        if not title.value or not title.value.strip():
            snack(page, "请填写标题", th.ACCENT)
            return
        due_time_value = (due_time.value or "").strip()
        if due_time_value and not valid_hm(due_time_value):
            snack(page, "到期时间格式应为 HH:MM（如 09:30）", th.ACCENT)
            return
        remind_time_value = (remind_time.value or "").strip()
        if remind_time_value and not valid_hm(remind_time_value):
            snack(page, "提醒时间格式应为 HH:MM（如 09:00）", th.ACCENT)
            return
        offsets = sorted(remind_offsets)
        days_before = max(offsets) if offsets else -1
        remind_days_json = json.dumps(offsets) if offsets else "[]"
        if due_type.value == "农历":
            raw = (due.value or "").strip()
            if not raw:
                due_date, lunar_reminder = None, ""
            else:
                parsed = parse_lunar(raw)
                if not parsed:
                    snack(page, "农历日期格式不正确，例如「九月初九」或「9-9」", th.ACCENT)
                    return
                due_date = lunar_to_gregorian(*parsed)
                if due_date is None:
                    snack(page, "农历日期无效", th.ACCENT)
                    return
                lunar_reminder = raw
        else:
            try:
                due_date = _date(due.value)
            except ValueError as exc:
                logger.debug("open_todo_dialog：忽略 %s", type(exc).__name__)
                snack(page, "到期日期格式不正确", th.ACCENT)
                return
            lunar_reminder = ""
        rule = RULE_BY_LABEL.get(repeat.value or "不循环", "none")
        config_raw = ""
        if rule == "custom":
            unit = UNIT_BY_LABEL.get(unit_dd.value or "天", "day")
            try:
                interval = int((interval_field.value or "1").strip())
            except ValueError as exc:
                logger.debug("open_todo_dialog：忽略 %s", type(exc).__name__)
                snack(page, "间隔需为数字", th.ACCENT)
                return
            if interval < 1:
                snack(page, "间隔至少为 1", th.ACCENT)
                return
            custom_config: dict = {"unit": unit, "interval": interval}
            if skip_weekends_cb.value:
                custom_config["skip_weekends"] = True
            if skip_holidays_cb.value:
                custom_config["skip_holidays"] = True
            if unit == "week":
                if not selected["weekdays"]:
                    snack(page, "请至少选择一个星期", th.ACCENT)
                    return
                custom_config["weekdays"] = sorted(selected["weekdays"])
            elif unit == "month":
                mode = selected["month_mode"]
                custom_config["month_mode"] = mode
                if mode == "day":
                    if not selected["month_days"] and not selected["month_last_day"]:
                        snack(page, "请选择每月日期或「最后一天」", th.ACCENT)
                        return
                    custom_config["month_days"] = sorted(selected["month_days"])
                    custom_config["month_last_day"] = bool(selected["month_last_day"])
                elif mode == "weekday":
                    custom_config["month_nth"] = NTH_BY_LABEL.get(
                        month_nth_dd.value or "第一个", "first"
                    )
                    custom_config["month_weekday"] = WEEKDAY_INDEX.get(
                        month_weekday_dd.value or "周一", 0
                    )
                else:
                    custom_config["month_nth"] = (
                        "last" if (workday_nth_dd.value or "").startswith("最后") else "first"
                    )
            elif unit == "year":
                custom_config["year_mode"] = selected["year_mode"]
                custom_config["year_month"] = int((year_month_dd.value or "1月").replace("月", ""))
                if selected["year_mode"] == "date":
                    custom_config["year_day"] = int((year_day_dd.value or "1日").replace("日", ""))
                else:
                    custom_config["year_nth"] = NTH_BY_LABEL.get(
                        year_nth_dd.value or "第一个", "first"
                    )
                    custom_config["year_weekday"] = WEEKDAY_INDEX.get(
                        year_weekday_dd.value or "周一", 0
                    )
            config_raw = serialize_config(custom_config)
        fields = dict(
            title=title.value.strip(),
            note=note.value or "",
            due_date=due_date,
            due_time=due_time_value,
            tags=_tags(tags.value or ""),
            lunar_reminder=lunar_reminder,
            repeat_rule=rule,
            repeat_config=config_raw,
            remind_days=remind_days_json,
            remind_days_before=days_before,
            remind_time=remind_time_value,
            priority=normalize_priority(priority.value),
            # 四象限由优先级与到期日自动推导，手选值不再写入（列保留仅作兼容）。
            quadrant="",
        )
        if not _save_or_alert(page, Todo, todo.id if todo else None, fields):
            return
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    panel = ft.Container(
        content=ft.Column(
            [
                title,
                note,
                ft.Row([due_type, due, due_time], spacing=12),
                ft.Row([priority, tags], spacing=12),
                repeat,
                custom_panel,
                ft.Column(
                    [
                        ft.Text("提前提醒（可多选）", size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                        remind_chip_row,
                        remind_time,
                    ],
                    spacing=6,
                ),
            ],
            spacing=10,
            tight=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        height=520 if custom_panel.visible else None,
    )
    panel_holder["panel"] = panel

    dialog = modal(
        page,
        "编辑任务" if todo else "新增任务",
        panel,
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
        width=660,
    )
    return dialog


# ---------------------------------------------------------------- 笔记

def open_note_dialog(page: ft.Page, note: Note | None = None, on_done: Callable | None = None):
    title = form_field("标题 *", note.title if note else "")
    tags = form_field("标签（逗号分隔）", tags_text(note.tags) if note else "")
    content = form_field("Markdown 正文", note.content if note else "", multiline=True)

    def save(_):
        if not (title.value or "").strip():
            snack(page, "请填写标题", th.ACCENT)
            return
        fields = dict(title=title.value.strip(), tags=_tags(tags.value or ""), content=content.value or "")
        if not _save_or_alert(page, Note, note.id if note else None, fields):
            return
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

_EXPENSE_CATEGORIES = ["餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "通讯", "人情", "其他"]
_INCOME_CATEGORIES = ["工资", "奖金", "理财", "兼职", "礼金", "报销", "其他"]
# 交易类型：库内存英文码，界面显示中文；同时兼容历史中文值，避免下标 KeyError。
_KIND_LABELS = {
    "expense": "支出", "income": "收入", "transfer": "转账",
    "支出": "支出", "收入": "收入", "转账": "转账",
}


def open_account_dialog(page: ft.Page, account: Account | None = None, on_done: Callable | None = None):
    name = form_field("账户名称 *", account.name if account else "")
    kind = dropdown("类型", ["现金", "银行卡", "电子钱包", "储蓄", "借贷"], account.kind if account else "银行卡")
    balance = form_field("初始余额", str(account.initial_balance) if account else "0.00")

    def save(_):
        if not (name.value or "").strip():
            snack(page, "请填写账户名称", th.ACCENT)
            return
        try:
            amount = round(float(balance.value or 0), 2)
        except ValueError as exc:
            logger.debug("open_account_dialog：忽略 %s", type(exc).__name__)
            snack(page, "余额格式不正确", th.ACCENT)
            return
        fields = dict(name=name.value.strip(), kind=kind.value, initial_balance=amount)
        if not _save_or_alert(page, Account, account.id if account else None, fields):
            return
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
        # 编辑历史交易时保留已被软删的原账户选项，避免静默把记录改挂到别的账户。
        missing: list[tuple[str, str]] = []
        for account_id in {str(tx.account_id), str(tx.to_account_id or "")} if tx else set():
            if not account_id or any(account_id == known for known, _ in accounts):
                continue
            deleted = session.get(Account, account_id)
            if deleted is not None:
                missing.append((account_id, f"{deleted.name}（已删除）"))
    account_options = accounts + missing
    if not account_options:
        snack(page, "请先新增账户", th.ACCENT)
        return None
    account_ids = [str(account_id) for account_id, _ in account_options]
    kind_value = tx.kind if tx else kind
    # kind 可能是历史值/脏数据（"" 或 "支出"这类标签）：直接下标会 KeyError，
    # 打开编辑弹窗就整块崩掉，这里统一兜底成「支出」。
    kind_label = _KIND_LABELS.get(str(kind_value or ""), "支出")
    kind_dd = dropdown("类型", ["支出", "收入", "转账"], kind_label)
    amount = form_field("金额 *", str(tx.amount) if tx else "")

    def category_options(kind_key: str) -> list[str]:
        return _INCOME_CATEGORIES if kind_key == "income" else _EXPENSE_CATEGORIES

    initial_kind = {"支出": "expense", "收入": "income", "转账": "transfer"}[kind_dd.value]
    cats = category_options(initial_kind)
    category = dropdown("分类", cats, (tx.category if tx and tx.category in cats else None))

    initial_account = str(tx.account_id) if tx and str(tx.account_id) in account_ids else account_ids[0]
    initial_to = str(tx.to_account_id) if tx and tx.to_account_id and str(tx.to_account_id) in account_ids else next(
        (account_id for account_id in account_ids if account_id != initial_account), initial_account
    )

    def account_dropdown(label: str, value: str) -> ft.Dropdown:
        control = dropdown(label, account_ids, value, width=220)
        control.options = [
            ft.dropdown.Option(key=str(account_id), text=name) for account_id, name in account_options
        ]
        return control

    account_dd = account_dropdown("账户", initial_account)
    to_dd = account_dropdown("转入账户", initial_to)
    date_field = form_field("日期 YYYY-MM-DD", tx.date.isoformat() if tx else date.today().isoformat(), width=220)
    note = form_field("备注", tx.note if tx else "")

    def apply_kind(kind_key: str) -> None:
        cats = category_options(kind_key)
        category.options = [ft.dropdown.Option(c) for c in cats]
        category.value = (tx.category if tx and tx.category in cats else None) or cats[0]
        category.visible = kind_key != "transfer"
        to_dd.visible = kind_key == "transfer"

    apply_kind(initial_kind)
    kind_dd.on_select = lambda e: (
        apply_kind({"支出": "expense", "收入": "income", "转账": "transfer"}[e.control.value]),
        page.update(),
    )

    def save(_):
        parsed, error = _number(
            amount.value, label="金额", minimum=0.01, maximum=MAX_NUMBER,
            allow_empty=False,
        )
        if error:
            snack(page, error, th.ACCENT)
            return
        value = round(parsed, 2)
        try:
            record_date = _date(date_field.value, date.today()) or date.today()
        except ValueError as exc:
            logger.debug("open_transaction_dialog：忽略 %s", type(exc).__name__)
            snack(page, "日期格式不正确", th.ACCENT)
            return
        mapped = {"支出": "expense", "收入": "income", "转账": "transfer"}.get(
            kind_dd.value, "expense"
        )
        account_id = str(account_dd.value)
        if mapped == "transfer" and (
            len(account_ids) < 2 or not to_dd.value or str(to_dd.value) == account_id
        ):
            snack(page, "转账需要选择两个不同账户", th.ACCENT)
            return
        fields = dict(
            kind=mapped,
            amount=value,
            category="转账" if mapped == "transfer" else (category.value or "其他"),
            account_id=account_id,
            to_account_id=str(to_dd.value) if mapped == "transfer" else None,
            date=record_date,
            note=note.value or "",
        )
        if not _save_or_alert(page, Transaction, tx.id if tx else None, fields):
            return
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
        minutes_value, error = _number(
            minutes.value, label="时长（分钟）", minimum=1, maximum=24 * 60,
            allow_empty=False,
        )
        if error:
            snack(page, error, th.ACCENT)
            return
        mins = int(minutes_value)
        distance_value, error = _number(
            distance.value, label="距离", minimum=0, maximum=1000
        )
        if error:
            snack(page, error, th.ACCENT)
            return
        kcal, error = _number(calories.value, label="消耗热量", minimum=0, maximum=100_000)
        if error:
            snack(page, error, th.ACCENT)
            return
        try:
            record_date = _date(day.value, date.today()) or date.today()
        except ValueError as exc:
            logger.debug("open_exercise_dialog：忽略 %s", type(exc).__name__)
            snack(page, "日期格式不正确", th.ACCENT)
            return
        distance_km = distance_value
        if kcal is None:
            # 估算需要体重：优先取最近一次体重记录，没有才退回默认值。
            with session_scope() as session:
                kcal = estimate_exercise_calories(kind.value, mins, latest_weight_kg(session))
        fields = dict(
            date=record_date, kind=kind.value, minutes=mins,
            distance_km=distance_km,
            calories=kcal, note=note.value or "",
        )
        if not _save_or_alert(page, Exercise, item.id if item else None, fields):
            return
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
            except ValueError as exc:
                logger.debug("open_diet_dialog：忽略 %s", type(exc).__name__)
                calories.value = "0"
        calories.error_text = None
        page.update()

    def save(_):
        weight, error = _number(
            grams.value, label="重量", minimum=0.1, maximum=100_000, allow_empty=False
        )
        if error:
            snack(page, error, th.ACCENT)
            return
        kcal, error = _number(calories.value, label="热量", minimum=0, maximum=100_000)
        if error:
            snack(page, error, th.ACCENT)
            return
        try:
            record_date = _date(day.value, date.today()) or date.today()
        except ValueError as exc:
            logger.debug("open_diet_dialog：忽略 %s", type(exc).__name__)
            snack(page, "日期格式不正确", th.ACCENT)
            return
        if kcal is None:
            with session_scope() as session:
                kcal = calorie_of(session, food.value or "", weight)
        fields = dict(date=record_date, meal=meal.value,
                      food=food.value, grams=weight, calories=kcal, note=note.value or "")
        if not _save_or_alert(page, Diet, item.id if item else None, fields):
            return
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "记录饮食" if not item else "编辑饮食",
        ft.Column([ft.Row([meal, food], spacing=12), ft.Row([grams, calories], spacing=12), day, note,
                   ft.Text("热量按《中国食物成分表（第 6 版）》每 100g 可食部自动计算，可手动覆盖。",
                           size=th.FONT_CAPTION, color=th.TEXT_MUTED)],
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
        weight, error = _number(
            kg.value, label="体重", minimum=1, maximum=500, allow_empty=False
        )
        if error:
            snack(page, error, th.ACCENT)
            return
        body_fat, error = _number(fat.value, label="体脂率", minimum=0, maximum=100)
        if error:
            snack(page, error, th.ACCENT)
            return
        try:
            record_date = _date(day.value, date.today()) or date.today()
        except ValueError as exc:
            logger.debug("open_weight_dialog：忽略 %s", type(exc).__name__)
            snack(page, "日期格式不正确", th.ACCENT)
            return
        fields = dict(date=record_date, kg=weight, body_fat=body_fat)
        if not _save_or_alert(page, Weight, item.id if item else None, fields):
            return
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
            # 校验失败保留弹窗，避免用户已填内容丢失。
            snack(page, "时间格式应为 HH:MM", th.ACCENT)
            return
        try:
            record_date = _date(day.value, date.today()) or date.today()
        except ValueError as exc:
            logger.debug("open_sleep_dialog：忽略 %s", type(exc).__name__)
            snack(page, "日期格式不正确", th.ACCENT)
            return
        try:
            quality_val = int(quality.value)
        except (TypeError, ValueError) as exc:
            logger.debug("open_sleep_dialog：忽略 %s", type(exc).__name__)
            quality_val = 3
        fields = dict(date=record_date, bedtime=bedtime.value or "",
                      wake_time=wake.value or "", hours=hours, quality=quality_val)
        if not _save_or_alert(
            page,
            Sleep,
            item.id if item else None,
            fields,
            # 同一天只保留一条睡眠记录：**新建**时按日期复用既有行（编辑不走这个分支）。
            resolve=lambda session: session.scalar(
                select(Sleep).where(Sleep.is_deleted.is_(False), Sleep.date == record_date)
            ),
        ):
            return
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
    kind = dropdown("类型", ENTERTAINMENT_KINDS, item.kind if item else "电影")
    status = dropdown("状态", ["想看", "在看", "看完"], item.status if item else "想看")
    # 历史数据里可能存着 http://127.0.0.1:8000/... 这类只有本机能打开的地址：
    # 打开弹窗时按当前同步服务地址纠正显示，保存后即被修正。
    initial_cover = absolute_image_url(item.cover or "") if item else ""
    cover = form_field("封面 URL 或本地路径", initial_cover)
    rating = form_field("评分（0-10，可选）", str(item.rating) if item and item.rating else "")
    note = form_field("简短备注", item.note if item else "", multiline=True)

    def fill_cover(url: str) -> None:
        cover.value = url
        cover.update()

    # 选本地图片 → 自动压缩上传 → 字段里填服务器链接
    cover.expand = True
    cover_row = ft.Row(
        [cover, upload_button(page, fill_cover, "上传", "选择封面图片")],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    rehost = {"busy": False}

    def rehost_remote(_=None) -> None:
        """把外链封面转存到同步服务器：外链图床会防盗链/过期，换设备也未必能看。

        只对「外部 http(s) 图片」动手；原本就在本服务器上的地址（改一下主机名即可）
        直接改，不重新下载。失败保留原地址并提示，不阻断保存。
        """
        raw = normalize_cover(cover.value or "")
        if not raw.lower().startswith(("http://", "https://")):
            return
        target = absolute_image_url(raw)
        if target != raw:
            fill_cover(target)
            return
        if rehost["busy"]:
            return
        rehost["busy"] = True
        snack(page, "正在把网络封面转存到服务器…", th.TEXT_MUTED)

        def worker() -> None:
            try:
                url = import_remote_image(raw)
            except ImageUploadError as exc:
                async def fail() -> None:
                    rehost["busy"] = False
                    snack(page, str(exc), th.ACCENT)
                page.run_task(fail)
                return
            except Exception:
                logger.exception("转存封面失败")

                async def fail_unexpected() -> None:
                    rehost["busy"] = False
                    snack(page, "转存封面失败，已保留原地址", th.ACCENT)
                page.run_task(fail_unexpected)
                return

            async def apply() -> None:
                rehost["busy"] = False
                fill_cover(url)
                snack(page, "封面已转存到服务器", th.PRIMARY)

            page.run_task(apply)

        page.run_thread(worker)

    cover.on_blur = rehost_remote

    def save(_):
        if not (title.value or "").strip():
            snack(page, "请填写标题", th.ACCENT)
            return
        try:
            score = float(rating.value) if (rating.value or "").strip() else None
        except ValueError as exc:
            logger.debug("open_entertainment_dialog：忽略 %s", type(exc).__name__)
            snack(page, "评分需为 0-10 的数字", th.ACCENT)
            return
        if score is not None and not 0 <= score <= 10:
            snack(page, "评分需在 0-10 之间", th.ACCENT)
            return
        fields = dict(
            title=title.value.strip(), kind=kind.value, status=status.value,
            # 清洗「复制文件地址」带来的首尾引号、file:// 前缀等，否则渲染必然失败；
            # 再把「只有本机能开」的地址换成当前同步服务地址，保证多端都能看到封面。
            cover=absolute_image_url(normalize_cover(cover.value or "")),
            rating=score, note=note.value or "",
        )
        if not _save_or_alert(page, Entertainment, item.id if item else None, fields):
            return
        mark_pending_write()
        _close_and_refresh(page, dialog, on_done)

    dialog = modal(
        page, "编辑条目" if item else "新增条目",
        ft.Column([title, ft.Row([kind, status, rating], spacing=12), cover_row, note], spacing=10, tight=True),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
        width=620,
    )
    return dialog


# ---------------------------------------------------------------- 设置

def open_settings_dialog(page: ft.Page, on_done: Callable | None = None, refresh_status=None):
    settings = load_settings()

    def _protection_note() -> str:
        """把「敏感字段到底怎么存的」直接告诉用户，而不是只在日志里警告。"""
        level = secrets_protection()
        if level == "dpapi":
            return "同步令牌与 AI Key 已用 Windows DPAPI 按当前用户加密保存。"
        if level == "base64":
            return (
                "⚠ 令牌 / Key 目前以 base64 保存（等同明文），建议清空后重新填写以启用 DPAPI。"
            )
        if not dpapi_available():
            return (
                "⚠ 当前系统不支持 DPAPI：默认拒绝保存令牌 / Key，可勾选下方弱保护开关（不推荐）。"
            )
        return "尚未配置同步令牌 / AI Key。"

    server = form_field("同步服务地址", settings.get("server_url", DEFAULT_SERVER_URL))
    token = form_field("同步令牌", settings.get("sync_token", ""))
    token.password = True
    token.can_reveal_password = True
    allow_weak = ft.Checkbox(
        label="允许弱保护存储（DPAPI 不可用时以 base64 保存令牌/Key，不推荐）",
        value=bool(settings.get("allow_weak_secret_storage", False)),
        label_style=th.checkbox_label_style(),
    )
    height = form_field("身高 cm", str(settings.get("height_cm", DEFAULT_HEIGHT_CM)))
    gender = dropdown("性别", ["男", "女"], settings.get("gender", "男"))
    enabled = ft.Checkbox(label="启用自动同步", value=bool(settings.get("sync_enabled", True)),
                          label_style=th.checkbox_label_style())

    # ---- 外观与系统集成
    theme_dd = dropdown("主题", ["亮色", "暗色"],
                        "暗色" if settings.get("theme_mode") == "dark" else "亮色", width=140)
    autostart = ft.Checkbox(label="开机自启（Windows）",
                            value=bool(settings.get("autostart", False)),
                            label_style=th.checkbox_label_style())
    close_to_tray = ft.Checkbox(
        label="关闭窗口时收到托盘（需安装 pystray，否则最小化到任务栏）",
        value=bool(settings.get("close_to_tray", False)),
        label_style=th.checkbox_label_style(),
    )

    # 模块显示与排序：上移/下移即时生效，保存后写入设置
    from ..main import MODULES  # 延迟导入避免循环依赖

    order_state = {"value": [key for key in (settings.get("module_order") or [])]}
    if not order_state["value"]:
        order_state["value"] = [key for key, _label in MODULES]
    labels = dict(MODULES)
    for key, _label in MODULES:
        if key not in order_state["value"]:
            order_state["value"].append(key)
    hidden = {str(key) for key in (settings.get("hidden_modules") or [])}
    order_box = ft.Column(spacing=2)

    def render_order() -> None:
        def move(index: int, delta: int) -> None:
            target = index + delta
            if 0 <= target < len(order_state["value"]):
                order_state["value"][index], order_state["value"][target] = (
                    order_state["value"][target],
                    order_state["value"][index],
                )
                render_order()
                page.update()

        def toggle_visible(key: str, checked: bool) -> None:
            if checked:
                hidden.discard(key)
            else:
                hidden.add(key)

        order_box.controls = [
            ft.Row(
                [
                    ft.Checkbox(value=key not in hidden,
                                on_change=lambda e, k=key: toggle_visible(k, bool(e.control.value))),
                    ft.Text(labels.get(key, key), size=12, color=th.TEXT, width=90),
                    ft.TextButton("↑", on_click=lambda e, i=index: move(i, -1)),
                    ft.TextButton("↓", on_click=lambda e, i=index: move(i, 1)),
                ],
                spacing=4,
            )
            for index, key in enumerate(order_state["value"])
        ]

    render_order()

    # AI 助手配置
    provider_names = [meta["name"] for meta in AI_PROVIDERS.values()]
    current_provider = settings.get("ai_provider", "custom")
    if current_provider not in AI_PROVIDERS:
        current_provider = "custom"
    provider_key = {"value": current_provider}
    ai_provider_dd = dropdown("厂商", provider_names, AI_PROVIDERS[current_provider]["name"], width=220)
    ai_base_url = form_field("服务地址 base_url", settings.get("ai_base_url", ""))
    ai_model = form_field("模型名", settings.get("ai_model", ""))
    ai_key = form_field("API Key", settings.get("ai_api_key", ""))
    ai_key.password = True
    ai_key.can_reveal_password = True

    def on_provider_change(e):
        name = e.control.value
        for key, meta in AI_PROVIDERS.items():
            if meta["name"] == name:
                provider_key["value"] = key
                ai_base_url.value = meta["base_url"]
                ai_model.value = meta["model"]
                break
        page.update()

    ai_provider_dd.on_select = on_provider_change

    # 未保存配置的连通性自检：后台线程发请求，回到 UI 线程更新提示。
    sync_probe_text = ft.Text("", size=th.FONT_CAPTION, color=th.TEXT_MUTED)
    ai_probe_text = ft.Text("", size=th.FONT_CAPTION, color=th.TEXT_MUTED)

    def _run_probe(target: ft.Text, work: Callable[[], str]) -> None:
        target.value = "正在测试连接…"
        target.color = th.TEXT_MUTED
        target.update()

        def worker() -> None:
            message = work()

            async def apply() -> None:
                target.value = message
                target.color = th.PRIMARY if "成功" in message else th.ACCENT
                page.update()

            try:
                page.run_task(apply)
            except Exception:
                logger.exception("连接测试结果回 UI 线程失败")

        page.run_thread(worker)

    def test_sync(_):
        _run_probe(
            sync_probe_text,
            lambda: probe(server.value or DEFAULT_SERVER_URL, token.value or ""),
        )

    def test_ai(_):
        def work() -> str:
            try:
                chat(
                    [{"role": "user", "content": "ping"}],
                    settings={
                        "ai_base_url": ai_base_url.value or "",
                        "ai_api_key": ai_key.value or "",
                        "ai_model": ai_model.value or "",
                    },
                )
            except AIConfigError as exc:
                logger.debug("open_settings_dialog：忽略 %s", type(exc).__name__)
                return str(exc)
            except AIError as exc:
                logger.debug("open_settings_dialog：忽略 %s", type(exc).__name__)
                return str(exc)
            except Exception:
                logger.exception("AI 连接测试异常")
                return "连接失败，详见日志"
            return "连接成功"

        _run_probe(ai_probe_text, work)

    # 同步队列可见性：被拒记录不会无限重推，但用户需要知道「哪张表、哪条、为什么」。
    with session_scope() as session:
        stats = sync_queue_stats(session)
        issues = list_sync_issues(session, limit=20)
    queue_note = ft.Text(
        f"待同步 {stats.get('pending', 0)} 条"
        + (
            f" · {stats['error']} 条被服务端拒收，重新编辑后会自动重试"
            if stats.get("error")
            else ""
        ),
        size=th.FONT_CAPTION,
        color=th.ACCENT if stats.get("error") else th.TEXT_MUTED,
    )

    def retry_all(_):
        with session_scope() as session:
            restored = retry_rejected(session)
        if restored:
            mark_pending_write()
        snack(
            page,
            f"已重新排队 {restored} 条记录" if restored else "没有被拒记录需要重试",
            th.PRIMARY if restored else th.TEXT_MUTED,
        )
        if refresh_status:
            refresh_status()
        _close_and_refresh(page, dialog, on_done)

    issue_section: list[ft.Control] = []
    if stats.get("error"):
        issue_section = [
            ft.Container(height=1, bgcolor=th.DIVIDER),
            ft.Row(
                [
                    ft.Text(f"被拒明细（最近 {len(issues)} 条）", size=th.FONT_CARD_TITLE,
                            color=th.TEXT, weight="bold"),
                    ft.Container(expand=True),
                    ft.TextButton("全部重试", on_click=retry_all),
                ]
            ),
            *[
                ft.Row(
                    [
                        ft.Text(table_label(issue.table_name), size=th.FONT_CAPTION,
                                color=th.TEXT, width=48),
                        ft.Text(
                            issue.reason or "未提供原因",
                            size=th.FONT_CAPTION, color=th.TEXT_MUTED, expand=True,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(issue.record_id[:8], size=th.FONT_CAPTION,
                                color=th.TEXT_MUTED),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                for issue in issues
            ],
            ft.Text(
                "被拒记录已退出待同步队列，不会反复上传；修改该记录会自动重试，"
                "若载荷本身不合法则重试后仍会被拒。",
                size=th.FONT_CAPTION, color=th.TEXT_MUTED,
            ),
        ]

    # 本地备份：自动每日一次，也可手动触发与打开目录
    last_backup = settings.get("last_backup_date") or "从未备份"
    backup_text = ft.Text(f"最近备份：{last_backup}", size=th.FONT_CAPTION,
                          color=th.TEXT_MUTED)

    def backup_now(_):
        # 备份要拷贝整个 SQLite 文件，放事件循环里会卡住窗口，改到后台线程执行。
        def worker() -> None:
            try:
                path = backup_database(label="manual")
            except Exception:
                logger.exception("手动备份失败")

                async def fail() -> None:
                    backup_text.value = "最近备份：失败，详见日志"
                    snack(page, "备份失败，详见日志", th.ACCENT)

                page.run_task(fail)
                return
            today_text = date.today().isoformat()
            save_settings({"last_backup_date": today_text})

            async def done() -> None:
                backup_text.value = f"最近备份：{today_text}（{path.name}）"
                # 下面 snack 会触发整页 update，无需单独刷新这个 Text。
                snack(page, f"已备份：{path.name}", th.PRIMARY)

            page.run_task(done)

        page.run_thread(worker)

    def open_backups(_):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        opener = getattr(os, "startfile", None)
        if opener is None:
            snack(page, f"备份目录：{BACKUP_DIR}", th.TEXT_MUTED)
            return
        opener(BACKUP_DIR)

    def _open_backup_center() -> None:
        from .backup_center import open_backup_center

        open_backup_center(page, on_done)

    def _open_data_io() -> None:
        from .data_io_dialog import open_data_io_dialog

        open_data_io_dialog(page, on_done)

    def _open_sync_detail() -> None:
        from .sync_detail import open_sync_detail

        open_sync_detail(page, refresh_status)

    def save(_):
        try:
            h = float(height.value or DEFAULT_HEIGHT_CM)
        except ValueError:
            # 与其它字段一致：报错并中止保存，而不是静默把身高重置成默认值。
            logger.debug("设置未保存：身高不是数字")
            snack(page, "身高需为 50-250 之间的数字", th.ACCENT)
            return
        if not 50 <= h <= 250:
            logger.debug("设置未保存：身高越界 %.1f", h)
            snack(page, "身高需为 50-250 之间的数字", th.ACCENT)
            return
        try:
            save_settings(dict(
                server_url=server.value or DEFAULT_SERVER_URL, sync_token=token.value or "",
                height_cm=h, gender=gender.value, sync_enabled=enabled.value,
                ai_provider=provider_key["value"],
                ai_base_url=ai_base_url.value or "",
                ai_api_key=ai_key.value or "",
                ai_model=ai_model.value or "",
                theme_mode="dark" if theme_dd.value == "暗色" else "light",
                autostart=bool(autostart.value),
                close_to_tray=bool(close_to_tray.value),
                module_order=list(order_state["value"]),
                hidden_modules=sorted(hidden),
                allow_weak_secret_storage=bool(allow_weak.value),
            ))
        except InsufficientSecretProtection as exc:
            # 缺 DPAPI 时拒绝把凭据写成 base64：留在弹窗里让用户勾选「允许弱保护存储」重试。
            logger.warning("设置未保存：设置文件存在但无法解析（%s）", type(exc).__name__)
            snack(page, str(exc), th.ACCENT)
            return
        except SettingsUnreadable as exc:
            # 设置文件损坏时拒绝回写（否则会把整份配置覆盖成默认值），保留弹窗让用户先修文件。
            logger.debug("open_settings_dialog：忽略 %s", type(exc).__name__)
            snack(page, str(exc), th.ACCENT)
            return
        # 明文 HTTP 同步：令牌是唯一身份凭证，值得显著提示一次（不阻断，本机/内网调试常见）。
        from ..services.sync import insecure_remote_server

        if insecure_remote_server(server.value or DEFAULT_SERVER_URL):
            snack(
                page,
                "设置已保存。注意：同步服务地址是明文 HTTP，令牌与数据可能在链路被窃听，"
                "建议改用 https 或反代 TLS。",
                th.ACCENT,
            )
        # 开机自启写注册表（失败只提示，不影响其他设置保存）
        from ..services import system_integration

        # 模块显隐/排序改了要立刻重建左侧导航，否则用户会以为设置没生效。
        from . import theme_switch as _theme_switch

        previous_autostart = bool(settings.get("autostart", False))
        if bool(autostart.value) != previous_autostart:
            ok, message = system_integration.set_autostart(bool(autostart.value))
            if not ok:
                snack(page, message, th.ACCENT)

        # 先切主题、再重建界面：反过来的话页面会先用旧配色重建一次，紧接着 apply()
        # 又整页重建一次（设置保存一度触发三次整页重建）。
        if theme_dd.value == "暗色" and not th.is_dark():
            _theme_switch.apply("dark")
        elif theme_dd.value == "亮色" and th.is_dark():
            _theme_switch.apply("light")

        _theme_switch.refresh_nav()
        if refresh_status:
            refresh_status()
        _close_and_refresh(page, dialog, on_done)

    def _group(title: str) -> ft.Control:
        return ft.Column(
            [
                ft.Text(title, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
                ft.Container(height=1, bgcolor=th.DIVIDER),
            ],
            spacing=6,
            tight=True,
        )

    dialog = modal(
        page, "设置",
        ft.Column(
            [
                # 同步
                _group("同步"),
                server, token,
                ft.Text(
                    _protection_note(),
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                allow_weak,
                ft.Row([ft.TextButton("测试同步连接", on_click=test_sync), sync_probe_text],
                       spacing=10, wrap=True),
                enabled,
                queue_note,
                *issue_section,
                # 健康档案
                _group("健康档案"),
                ft.Row([height, gender], spacing=12),
                # AI
                _group("AI 助手（国产大模型）"),
                ai_provider_dd,
                ft.Row([ai_base_url, ai_model], spacing=12),
                ai_key,
                ft.Row([ft.TextButton("测试 AI 连接", on_click=test_ai), ai_probe_text],
                       spacing=10, wrap=True),
                ft.Text(
                    "隐私提示：使用 AI 助手时，被「@引用」的数据（任务、日记、笔记、收支等）"
                    "会通过网络发送至你所配置的大模型服务商；请勿在引用范围内填写密码等敏感信息。"
                    "首次发送前还会再次确认。",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                ),
                # 数据
                _group("数据"),
                ft.Row(
                    [
                        ft.TextButton("立即备份", on_click=backup_now),
                        ft.TextButton("打开备份目录", on_click=open_backups),
                        backup_text,
                    ],
                    spacing=10,
                    wrap=True,
                ),
                ft.Row(
                    [
                        ft.TextButton(
                            "备份恢复中心",
                            on_click=lambda e: (_close_and_refresh(page, dialog, None),
                                                _open_backup_center()),
                        ),
                        ft.TextButton(
                            "数据导入导出",
                            on_click=lambda e: (_close_and_refresh(page, dialog, None),
                                                _open_data_io()),
                        ),
                        ft.TextButton(
                            "同步明细",
                            on_click=lambda e: (_close_and_refresh(page, dialog, None),
                                                _open_sync_detail()),
                        ),
                    ],
                    spacing=10,
                    wrap=True,
                ),
                ft.Text(
                    "每天首次启动会自动备份一次，默认保留最近 7 份（可在备份恢复中心改目录/份数）。",
                    size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                ),
                # 外观
                _group("外观与导航"),
                ft.Row([theme_dd], spacing=12),
                autostart,
                close_to_tray,
                ft.Text("导航模块（取消勾选即隐藏，↑↓ 调整顺序）",
                        size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                order_box,
                # 系统
                _group("系统"),
                ft.Text(f"数据目录：{DATA_DIR}", size=th.FONT_CAPTION, color=th.TEXT_MUTED,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            ],
            spacing=10, tight=True, scroll=ft.ScrollMode.AUTO, height=520,
        ),
        [ft.TextButton("取消", on_click=lambda e: _close_and_refresh(page, dialog, None)), primary_button("保存", save)],
        width=600,
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
