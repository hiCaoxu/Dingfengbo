"""自定义重复规则引擎：天 / 周 / 月 / 年 + 间隔 + 跳过双休日 / 节假日。

配置结构（``Todo.repeat_config`` 的 JSON）::

    {"unit": "day",   "interval": 1, "skip_weekends": true}
    {"unit": "week",  "interval": 1, "weekdays": [0, 5]}
    {"unit": "month", "interval": 1, "month_mode": "day",
     "month_days": [1, 15], "month_last_day": true}
    {"unit": "month", "interval": 1, "month_mode": "weekday",
     "month_nth": "first", "month_weekday": 0}
    {"unit": "month", "interval": 1, "month_mode": "workday", "month_nth": "last"}
    {"unit": "year",  "interval": 1, "year_mode": "date", "year_month": 11, "year_day": 30}
    {"unit": "year",  "interval": 1, "year_mode": "weekday",
     "year_month": 11, "year_nth": "first", "year_weekday": 0}

旧版 ``{"kind": "weekly_times" | "monthly_first_workday" | "monthly_last_workday"
| "monthly_days"}`` 由 :func:`normalize_config` 自动转换。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from .dates import nth_workday, shift_month
from .holidays import is_holiday

UNIT_LABELS = {"day": "天", "week": "周", "month": "月", "year": "年"}
UNIT_BY_LABEL = {label: unit for unit, label in UNIT_LABELS.items()}

# 0=周一 … 6=周日（与 date.weekday() 对齐）
WEEKDAY_SHORT = ["一", "二", "三", "四", "五", "六", "日"]
WEEKDAY_LABELS = [f"周{name}" for name in WEEKDAY_SHORT]
WEEKDAY_INDEX = {label: index for index, label in enumerate(WEEKDAY_LABELS)}

NTH_LABELS = {
    "first": "第一个",
    "second": "第二个",
    "third": "第三个",
    "fourth": "第四个",
    "last": "最后一个",
}
NTH_BY_LABEL = {label: key for key, label in NTH_LABELS.items()}

MONTH_MODE_LABELS = {"day": "按日期", "weekday": "按星期", "workday": "按工作日"}
MONTH_MODE_BY_LABEL = {label: mode for mode, label in MONTH_MODE_LABELS.items()}

YEAR_MODE_LABELS = {"date": "按日期", "weekday": "按星期"}
YEAR_MODE_BY_LABEL = {label: mode for mode, label in YEAR_MODE_LABELS.items()}

MAX_STEPS = 400


def normalize_config(raw: dict | None) -> dict:
    """统一新旧配置结构；无法识别时返回 {}。"""
    if not raw:
        return {}
    if raw.get("unit"):
        return dict(raw)
    kind = str(raw.get("kind") or "")
    if kind == "weekly_times":
        try:
            times = int(raw.get("times") or 1)
        except (TypeError, ValueError):
            times = 1
        times = max(1, min(7, times))
        return {
            "unit": "day",
            "interval": max(1, round(7 / times)),
            "legacy_weekly_times": times,
        }
    if kind == "monthly_first_workday":
        return {"unit": "month", "interval": 1, "month_mode": "workday", "month_nth": "first"}
    if kind == "monthly_last_workday":
        return {"unit": "month", "interval": 1, "month_mode": "workday", "month_nth": "last"}
    if kind == "monthly_days":
        days = [int(day) for day in raw.get("days") or [] if str(day).isdigit()]
        return {"unit": "month", "interval": 1, "month_mode": "day", "month_days": sorted(days)}
    return {}


def _nth_weekday(year: int, month: int, weekday: int, nth: str) -> date | None:
    days = calendar.monthrange(year, month)[1]
    matches = [
        date(year, month, day)
        for day in range(1, days + 1)
        if date(year, month, day).weekday() == weekday
    ]
    if not matches:
        return None
    if nth == "last":
        return matches[-1]
    index = {"first": 0, "second": 1, "third": 2, "fourth": 3}.get(nth, 0)
    return matches[index] if index < len(matches) else matches[-1]


def _month_targets(year: int, month: int, config: dict, mode: str) -> list[date]:
    days = calendar.monthrange(year, month)[1]
    if mode == "day":
        wanted = sorted(
            {
                int(day)
                for day in config.get("month_days") or []
                if str(day).isdigit() and 1 <= int(day) <= 31
            }
        )
        targets = [date(year, month, day) for day in wanted if day <= days]
        if config.get("month_last_day"):
            targets.append(date(year, month, days))
        return sorted(set(targets))
    if mode == "weekday":
        found = _nth_weekday(
            year,
            month,
            int(config.get("month_weekday") or 0),
            str(config.get("month_nth") or "first"),
        )
        return [found] if found else []
    if mode == "workday":
        nth = "last" if str(config.get("month_nth")) == "last" else "first"
        found = nth_workday(year, month, nth)
        return [found] if found else []
    return []


def _skip_blocked(value: date, config: dict) -> date:
    """按「跳过双休日 / 法定节假日」顺延到第一个可用日期。"""
    skip_weekends = bool(config.get("skip_weekends"))
    skip_holidays = bool(config.get("skip_holidays"))
    if not (skip_weekends or skip_holidays):
        return value
    candidate = value
    for _ in range(MAX_STEPS):
        blocked = (skip_weekends and candidate.weekday() >= 5) or (
            skip_holidays and is_holiday(candidate)
        )
        if not blocked:
            return candidate
        candidate += timedelta(days=1)
    return candidate


def _next_week(value: date, config: dict, interval: int) -> date | None:
    weekdays = sorted(
        {
            int(day)
            for day in config.get("weekdays") or []
            if str(day).lstrip("-").isdigit() and 0 <= int(day) <= 6
        }
    ) or [value.weekday()]
    week_start = value - timedelta(days=value.weekday())
    for block in range(MAX_STEPS):
        base = week_start + timedelta(weeks=block * interval)
        for weekday in weekdays:
            candidate = base + timedelta(days=weekday)
            if candidate > value:
                return _skip_blocked(candidate, config)
    return None


def _next_month(value: date, config: dict, interval: int) -> date | None:
    mode = str(config.get("month_mode") or "day")
    for block in range(MAX_STEPS):
        year, month = shift_month(value.year, value.month, block * interval)
        for candidate in _month_targets(year, month, config, mode):
            if candidate > value:
                return _skip_blocked(candidate, config)
    return None


def _next_year(value: date, config: dict, interval: int) -> date | None:
    mode = str(config.get("year_mode") or "date")
    try:
        month = int(config.get("year_month") or value.month)
    except (TypeError, ValueError):
        month = value.month
    month = min(12, max(1, month))
    for block in range(MAX_STEPS):
        year = value.year + block * interval
        if mode == "weekday":
            targets = _month_targets(
                year,
                month,
                {
                    "month_nth": config.get("year_nth"),
                    "month_weekday": config.get("year_weekday"),
                },
                "weekday",
            )
        else:
            try:
                day = int(config.get("year_day") or value.day)
            except (TypeError, ValueError):
                day = value.day
            day = min(day, calendar.monthrange(year, month)[1])
            targets = [date(year, month, day)]
        for candidate in targets:
            if candidate > value:
                return _skip_blocked(candidate, config)
    return None


def next_custom_due(value: date, raw_config: dict | None) -> date | None:
    """按自定义规则推算下一次到期日；无法推算返回 None。"""
    config = normalize_config(raw_config)
    unit = str(config.get("unit") or "")
    if not unit:
        return None
    try:
        interval = max(1, int(config.get("interval") or 1))
    except (TypeError, ValueError):
        interval = 1
    if unit == "day":
        return _skip_blocked(value + timedelta(days=interval), config)
    if unit == "week":
        return _next_week(value, config, interval)
    if unit == "month":
        return _next_month(value, config, interval)
    if unit == "year":
        return _next_year(value, config, interval)
    return None


def describe(raw_config: dict | None) -> str:
    """一行摘要，如「每 1 月 · 第一个工作日 · 跳过节假日」。"""
    config = normalize_config(raw_config)
    if not config:
        return "自定义"
    legacy_times = config.get("legacy_weekly_times")
    if legacy_times:
        return f"每周 {legacy_times} 次"

    unit = str(config.get("unit"))
    try:
        interval = max(1, int(config.get("interval") or 1))
    except (TypeError, ValueError):
        interval = 1
    text = f"每 {interval} {UNIT_LABELS.get(unit, unit)}"

    if unit == "week":
        days = "、".join(
            WEEKDAY_LABELS[int(day)]
            for day in sorted(config.get("weekdays") or [])
            if str(day).isdigit() and 0 <= int(day) <= 6
        )
        if days:
            text += f" · {days}"
    elif unit == "month":
        mode = str(config.get("month_mode") or "day")
        if mode == "day":
            parts = [f"{int(day)}日" for day in config.get("month_days") or [] if str(day).isdigit()]
            if config.get("month_last_day"):
                parts.append("最后一天")
            if parts:
                text += " · " + "、".join(parts)
        elif mode == "weekday":
            text += (
                f" · {NTH_LABELS.get(str(config.get('month_nth')), '第一个')}"
                f"{WEEKDAY_LABELS[int(config.get('month_weekday') or 0) % 7]}"
            )
        else:
            text += " · " + ("最后一个工作日" if str(config.get("month_nth")) == "last" else "第一个工作日")
    elif unit == "year":
        month = int(config.get("year_month") or 1)
        if str(config.get("year_mode")) == "weekday":
            text += (
                f" · {month}月{NTH_LABELS.get(str(config.get('year_nth')), '第一个')}"
                f"{WEEKDAY_LABELS[int(config.get('year_weekday') or 0) % 7]}"
            )
        else:
            text += f" · {month}月{int(config.get('year_day') or 1)}日"

    if config.get("skip_weekends"):
        text += " · 跳过双休日"
    if config.get("skip_holidays"):
        text += " · 跳过节假日"
    return text
