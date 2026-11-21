"""内置法定节假日与调休上班日。

数据来源（国务院办公厅年度通知）：
- 2026 年：国办发明电〔2025〕7 号
- 2027 年：国办发明电〔2026〕10 号

新增年份时在 ``_HOLIDAY_RANGES`` 与 ``_MAKEUP_WORKDAYS`` 里补充即可；
用户也可在数据目录的 ``holidays.txt`` 中追加（见 :mod:`holidays`）。
"""

from __future__ import annotations

from datetime import date, timedelta

# (节日名称, 放假首日, 放假末日) —— 含首尾
_HOLIDAY_RANGES: list[tuple[str, str, str]] = [
    ("2026 元旦", "2026-01-01", "2026-01-03"),
    ("2026 春节", "2026-02-15", "2026-02-23"),
    ("2026 清明", "2026-04-04", "2026-04-06"),
    ("2026 劳动节", "2026-05-01", "2026-05-05"),
    ("2026 端午", "2026-06-19", "2026-06-21"),
    ("2026 中秋", "2026-09-25", "2026-09-27"),
    ("2026 国庆", "2026-10-01", "2026-10-07"),
    ("2027 元旦", "2027-01-01", "2027-01-03"),
    ("2027 春节", "2027-02-05", "2027-02-13"),
    ("2027 清明", "2027-04-03", "2027-04-05"),
    ("2027 劳动节", "2027-05-01", "2027-05-05"),
    ("2027 端午", "2027-06-09", "2027-06-09"),
    ("2027 中秋", "2027-09-15", "2027-09-15"),
    ("2027 国庆", "2027-10-01", "2027-10-07"),
]

# 调休上班日（周末补班，按工作日处理）
_MAKEUP_WORKDAYS: list[str] = [
    # 2026：元旦 1/4；春节 2/14、2/28；劳动节 5/9；国庆 9/20、10/10
    "2026-01-04",
    "2026-02-14",
    "2026-02-28",
    "2026-05-09",
    "2026-09-20",
    "2026-10-10",
    # 2027：春节 1/31、2/14；劳动节 5/8；国庆 9/26、10/9
    "2027-01-31",
    "2027-02-14",
    "2027-05-08",
    "2027-09-26",
    "2027-10-09",
]


def _expand(start: str, end: str) -> list[date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    days: list[date] = []
    while first <= last:
        days.append(first)
        first += timedelta(days=1)
    return days


HOLIDAY_NAMES: dict[date, str] = {
    day: name for name, start, end in _HOLIDAY_RANGES for day in _expand(start, end)
}

HOLIDAY_DATES: frozenset[date] = frozenset(HOLIDAY_NAMES)

MAKEUP_WORKDAYS: frozenset[date] = frozenset(
    date.fromisoformat(day) for day in _MAKEUP_WORKDAYS
)
