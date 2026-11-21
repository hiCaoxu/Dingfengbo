"""任务视图的纯逻辑：月历网格、四象限分类与计数。

界面只负责渲染，日期/象限的判定放在这里，便于单测与保持一致口径：

- 未手选象限时，按「重要 = 优先级为高」「紧急 = 到期日在今天或明天（含逾期）」推导；
- 手选象限（``Todo.quadrant`` 为 q1..q4）优先于推导，用于用户显式调整。
"""

from __future__ import annotations

import calendar as pycal
from collections import Counter
from datetime import date, timedelta
from typing import Any, Iterable

URGENT_WINDOW_DAYS = 1

QUADRANTS: tuple[tuple[str, str, str], ...] = (
    ("q1", "重要且紧急", "马上做"),
    ("q2", "重要不紧急", "排进日程"),
    ("q3", "紧急不重要", "尽快了结"),
    ("q4", "不重要不紧急", "少做或删掉"),
)
QUADRANT_KEYS = tuple(key for key, _label, _hint in QUADRANTS)
QUADRANT_LABELS = {key: label for key, label, _hint in QUADRANTS}
QUADRANT_HINTS = {key: hint for key, _label, hint in QUADRANTS}
LABEL_TO_QUADRANT = {label: key for key, label, _hint in QUADRANTS}
AUTO_LABEL = "自动（按优先级与到期）"
WEEK_LABELS = ("一", "二", "三", "四", "五", "六", "日")


def quadrant_of(item: Any, today: date | None = None) -> str:
    """返回任务所在象限；``item`` 可以是 Todo 对象或同名字段字典。"""

    def field(name: str, default=None):
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    manual = str(field("quadrant", "") or "")
    if manual in QUADRANT_LABELS:
        return manual
    today = today or date.today()
    important = str(field("priority", "") or "") in ("high", "medium")
    due = field("due_date")
    urgent = bool(due and due <= today + timedelta(days=URGENT_WINDOW_DAYS))
    if important and urgent:
        return "q1"
    if important:
        return "q2"
    if urgent:
        return "q3"
    return "q4"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """按月份前后移动（``delta`` 可为负），返回新的 (year, month)。"""
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_grid(year: int, month: int) -> list[list[date]]:
    """整月网格：周一为一周起点，行数 5 或 6，首尾包含邻月日期。"""
    first = date(year, month, 1)
    start = first - timedelta(days=first.weekday())
    last = date(year, month, pycal.monthrange(year, month)[1])
    end = last + timedelta(days=6 - last.weekday())
    total = (end - start).days + 1
    return [
        [start + timedelta(days=week * 7 + weekday) for weekday in range(7)]
        for week in range(total // 7)
    ]


def counts_by_date(items: Iterable[Any]) -> dict[date, int]:
    """按到期日统计未完成任务数（不含循环模板）。"""
    counter: Counter[date] = Counter()
    for item in items:
        if _is_open(item) and _field(item, "due_date"):
            counter[_field(item, "due_date")] += 1
    return dict(counter)


def quadrant_counts(items: Iterable[Any], today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    result = {key: 0 for key in QUADRANT_KEYS}
    for item in items:
        if _is_open(item):
            result[quadrant_of(item, today)] += 1
    return result


def group_by_quadrant(
    items: Iterable[Any], today: date | None = None
) -> dict[str, list[Any]]:
    today = today or date.today()
    grouped: dict[str, list[Any]] = {key: [] for key in QUADRANT_KEYS}
    for item in items:
        if _is_open(item):
            grouped[quadrant_of(item, today)].append(item)
    return grouped


def _is_open(item: Any) -> bool:
    return not bool(_field(item, "completed")) and not bool(_field(item, "is_template"))


def _field(item: Any, name: str):
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)
