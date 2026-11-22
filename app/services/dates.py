"""日期工具：ISO 周、年内天数、中文星期与农历。

所有周数一律取 date.isocalendar()[1]，不做任何自行推算。
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

logger = logging.getLogger(__name__)

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
        # zhdate 对超出支持范围（1900-2100 之外）的日期会抛异常：农历只是装饰，
        # 显示不出来不影响功能，但不能静默无痕，至少留一条 debug 供排查。
        logger.debug("农历换算失败：%s", value, exc_info=True)
        return ""


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_LUNAR_MONTHS = {"正": 1, "冬": 11, "腊": 12}


def _cn_number(text: str) -> int | None:
    """中文数字转整数（一到九十九），兼容「初九」「廿三」「三十」等。"""
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text.startswith("初"):
        text = text[1:]
    if text.startswith("廿"):
        return 20 + (_CN_DIGITS.get(text[1:]) or 0)
    if text.startswith("十"):
        return 10 + (_CN_DIGITS.get(text[1:]) or 0)
    if text.endswith("十"):
        return (_CN_DIGITS.get(text[0]) or 0) * 10
    if "十" in text:
        tens, ones = text.split("十", 1)
        return (_CN_DIGITS.get(tens) or 0) * 10 + (_CN_DIGITS.get(ones) or 0)
    return _CN_DIGITS.get(text)


def _lunar_month(text: str) -> int | None:
    text = text.strip()
    if text in ("十一", "十一月"):
        return 11
    if text in ("十二", "十二月"):
        return 12
    if text in _LUNAR_MONTHS:
        return _LUNAR_MONTHS[text]
    if text and text[0] in _LUNAR_MONTHS:
        return _LUNAR_MONTHS[text[0]]
    return _cn_number(text)


def parse_lunar(value: str, default_year: int | None = None) -> tuple[int, int, int] | None:
    """解析农历日期，支持「九月初九」「2026年九月初九」「9-9」「2026-9-9」。

    返回 (year, month, day)；年份缺省时用 default_year 或当前年。
    """
    value = (value or "").strip()
    if not value:
        return None
    year = default_year or date.today().year
    # 「2027年九月初九」这种写法：年份后面跟的是中文月份，不是数字串，
    # 先单独把年份摘出来；否则下面的「剥掉年份」分支会把年份一起丢掉，
    # 解析结果退化成今年，到期日整体错一年。
    m = re.match(r"^(\d{4})\s*年\s*", value)
    if m:
        year = int(m.group(1))
        value = value[m.end():]
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", value)
    if m:
        year, month, day = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/.月](\d{1,2})日?", value)
        if m:
            month, day = (int(x) for x in m.groups())
        else:
            value = re.sub(r"^[〇零一二三四五六七八九十百千两0-9]+年", "", value)
            value = value.replace("月", " ").replace("日", " ").strip()
            parts = value.split()
            if len(parts) != 2:
                return None
            month = _lunar_month(parts[0])
            day = _cn_number(parts[1])
    if month is None or day is None or not (1 <= month <= 12 and 1 <= day <= 30):
        return None
    return year, month, day


def lunar_to_gregorian(year: int, month: int, day: int) -> date | None:
    """农历转公历；非法日期（含该月无此日）返回 None。"""
    try:
        from zhdate import ZhDate

        return ZhDate(year, month, day).to_datetime().date()
    except Exception:
        logger.debug("农历转公历失败：%s-%s-%s", year, month, day, exc_info=True)
        return None


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


def is_workday(value: date) -> bool:
    """工作日定义：周一至周五（不排除法定节假日，避免引入节假日数据源）。"""
    return value.weekday() < 5


def nth_workday(year: int, month: int, nth: str) -> date | None:
    """某月第一个/最后一个工作日。"""
    days = calendar.monthrange(year, month)[1]
    order = range(days, 0, -1) if nth == "last" else range(1, days + 1)
    for day in order:
        candidate = date(year, month, day)
        if is_workday(candidate):
            return candidate
    return None


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


def next_lunar_due(value: date, rule: str) -> date | None:
    """农历日期的循环推进：按天/按周走公历，按月=农历月+1，按年=农历年+1。"""
    if rule == "day":
        return value + timedelta(days=1)
    if rule == "week":
        return value + timedelta(weeks=1)
    if rule not in ("month", "year"):
        return None
    try:
        from zhdate import ZhDate

        zh = ZhDate.from_datetime(datetime.combine(value, datetime.min.time()))
        year, month, day = zh.lunar_year, zh.lunar_month, zh.lunar_day
        if rule == "month":
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        else:
            year += 1
        days = ZhDate.month_days(year)[month - 1]
        if day > days:
            day = days
        return ZhDate(year, month, day).to_datetime().date()
    except Exception:
        logger.debug("农历循环推进失败：%s (%s)", value, rule, exc_info=True)
        return None


def lunar_md(value: date) -> str:
    """返回简洁农历月日，如「九月初九」；不可用时返回空串。"""
    return lunar_text(value).split(" ")[0]


def local_date_from_utc(value: datetime) -> date:
    """将历史 naive UTC 或带时区时间转换为本地日期。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().date()


def local_datetime_from_utc(value: datetime) -> datetime:
    """naive UTC（库里的存储口径）→ 本地时间，供界面与 AI 上下文展示。

    库里统一存 UTC 是为了让多端 LWW 比较有公共时间轴；但直接把它 ``strftime``
    出来显示，用户看到的时间会比本地墙上时间早一个时区（UTC+8 下就是早 8 小时）。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().replace(tzinfo=None)


_HM_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def valid_hm(value: str) -> bool:
    """是否为合法的 HH:MM（00:00 至 23:59）。"""
    return bool(_HM_PATTERN.fullmatch((value or "").strip()))


def parse_hm(value: str) -> time | None:
    raw = (value or "").strip()
    if not valid_hm(raw):
        return None
    hour, minute = (int(part) for part in raw.split(":"))
    return time(hour, minute)


def parse_hours(bedtime: str, wake_time: str) -> float:
    """跨天睡眠时长；非法 HH:MM 输入按 0 小时处理。"""
    pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
    if not re.fullmatch(pattern, bedtime) or not re.fullmatch(pattern, wake_time):
        return 0.0
    bh, bm = (int(x) for x in bedtime.split(":"))
    wh, wm = (int(x) for x in wake_time.split(":"))
    minutes = (wh * 60 + wm) - (bh * 60 + bm)
    if minutes <= 0:
        minutes += 24 * 60
    return round(minutes / 60, 2)
