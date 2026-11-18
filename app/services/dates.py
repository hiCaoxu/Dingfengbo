"""日期工具：ISO 周、年内天数、中文星期与农历。

所有周数一律取 date.isocalendar()[1]，不做任何自行推算。
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def iso_week(value: date) -> int:
    return value.isocalendar()[1]


def iso_year(value: date) -> int:
    return value.isocalendar()[0]


def day_of_year(value: date) -> int:
    return value.timetuple().tm_yday


def weekday_cn(value: date) -> str:
    return WEEKDAY_CN[value.weekday()]


def lunar_text(value: date) -> str:
    """返回如「十月初十」的农历文本；不可用时返回空串。"""
    try:
        from zhdate import ZhDate

        raw = ZhDate.from_datetime(datetime.combine(value, datetime.min.time())).chinese()
        raw = raw.replace("农历", "").strip()
        # 去掉开头的中文年份，例如「二零二六年十月初十」->「十月初十」
        cleaned = re.sub(r"^[〇零一二三四五六七八九十百千]+年", "", raw).strip()
        return cleaned or raw
    except Exception:
        return ""


def diary_title(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日 {iso_week(value)}周{weekday_cn(value)}"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def shift_date_month(value: date, delta: int) -> date:
    """按月份平移日期，保留日号；目标月无该日时取月末。"""
    year, month = shift_month(value.year, value.month, delta)
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_grid(year: int, month: int) -> list[list[date]]:
    """周一为行首的整月网格，补齐前后日期。"""
    first = date(year, month, 1)
    offset = first.weekday()  # 0=周一
    start = first - timedelta(days=offset)
    days = calendar.monthrange(year, month)[1]
    total = offset + days
    rows = total // 7 + (1 if total % 7 else 0)
    return [[start + timedelta(days=i * 7 + j) for j in range(7)] for i in range(rows)]


def next_due(value: date, rule: str) -> date | None:
    if rule == "day":
        return value + timedelta(days=1)
    if rule == "week":
        return value + timedelta(weeks=1)
    if rule == "month":
        year, month = shift_month(value.year, value.month, 1)
        day = min(value.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    if rule == "year":
        year = value.year + 1
        month = value.month
        day = min(value.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    return None


def parse_hours(bedtime: str, wake_time: str) -> float:
    """跨天睡眠时长。"""
    try:
        bh, bm = (int(x) for x in bedtime.split(":")[:2])
        wh, wm = (int(x) for x in wake_time.split(":")[:2])
    except (ValueError, AttributeError):
        return 0.0
    minutes = (wh * 60 + wm) - (bh * 60 + bm)
    if minutes <= 0:
        minutes += 24 * 60
    return round(minutes / 60, 2)
