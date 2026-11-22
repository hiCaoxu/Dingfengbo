"""任务视图的纯逻辑：月历网格、四象限分类与计数。

界面只负责渲染，日期/象限的判定放在这里，便于单测与保持一致口径：

- 四象限**只按自动规则推导**：重要 = 优先级为高/中，紧急 = 到期日在今天/明天或已逾期；
  早期「手选象限」覆盖已废弃（同一任务在不同设备上会因手选值不同而归类不一致）；
- 优先级历史上有中文（高/中/低）与英文码（high/medium/low）两种写法，
  统一用 :func:`normalize_priority` 收敛，写入只存英文码。
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any, Iterable

# 日历网格只保留一份实现（dates.month_grid）：这里曾与 dates.py 各写一份，
# 改动时容易只改一处，待办与日记的日历就对不上了。re-export 兼容既有调用方。
from .dates import month_grid  # noqa: F401

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
WEEK_LABELS = ("一", "二", "三", "四", "五", "六", "日")

# 优先级：库内规范值是英文码（与同步/搜索/AI 草稿一致），界面展示中文。
PRIORITY_LABELS = {"": "", "low": "低", "medium": "中", "high": "高"}
PRIORITY_BY_LABEL = {"高": "high", "中": "medium", "低": "low"}
_IMPORTANT_PRIORITIES = ("high", "medium")


def normalize_priority(value: Any) -> str:
    """把任意写法的优先级收敛成规范码（high / medium / low / 空）。

    历史遗留：任务弹窗曾直接保存「高/中/低」，而象限推导与「高优先级」搜索只认
    英文码，导致弹窗里选「高」的任务永远被判成"不重要"。写入与读取都先过这里。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in PRIORITY_LABELS and lowered:
        return lowered
    if text in PRIORITY_BY_LABEL:
        return PRIORITY_BY_LABEL[text]
    return lowered if lowered in _IMPORTANT_PRIORITIES or lowered == "low" else ""


def priority_label(value: Any) -> str:
    """规范码 → 中文标签（未知/空返回空串）。"""
    return PRIORITY_LABELS.get(normalize_priority(value), "")


def quadrant_of(item: Any, today: date | None = None) -> str:
    """返回任务所在象限；``item`` 可以是 Todo 对象或同名字段字典。

    只做自动推导：``Todo.quadrant`` 字段仅为兼容历史数据保留，不再参与判断。
    """

    def field(name: str, default=None):
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    today = today or date.today()
    important = normalize_priority(field("priority", "")) in _IMPORTANT_PRIORITIES
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
