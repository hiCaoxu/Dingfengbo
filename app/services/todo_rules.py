"""待办循环规则：UI 选项常量 + 配置读写 + 下一次到期日推进。

「不定期」（irregular）是旧版选项，等价于「自定义但未配置」——不推进下一次，
保留它只为兼容存量数据；自定义规则的推算与文案在 :mod:`recurrence` 里。
"""

from __future__ import annotations

import json
import logging
from datetime import date

from .dates import next_due, next_lunar_due
from .recurrence import describe as describe_custom
from .recurrence import next_custom_due

logger = logging.getLogger(__name__)

REPEAT_LABELS: dict[str, str] = {
    "none": "不循环",
    "day": "按天",
    "week": "按周",
    "month": "按月",
    "year": "按年",
    "custom": "自定义",
    "irregular": "自定义",
}

# UI 下拉框选项顺序（自定义替代了旧版「不定期」）
RULE_OPTIONS = ["不循环", "按天", "按周", "按月", "按年", "自定义"]
RULE_BY_LABEL = {
    "不循环": "none",
    "按天": "day",
    "按周": "week",
    "按月": "month",
    "按年": "year",
    "自定义": "custom",
}


def parse_config(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError) as exc:
        logger.debug("parse_config：忽略 %s", type(exc).__name__)
        return {}
    return data if isinstance(data, dict) else {}


def serialize_config(config: dict | None) -> str:
    return json.dumps(config, ensure_ascii=False) if config else ""


def next_occurrence(due: date, rule: str, config_raw: str, lunar: bool) -> date | None:
    """按规则推进下一次到期日；无法推进（不循环/自定义未配置）返回 None。"""
    config = parse_config(config_raw)
    if rule in ("custom", "irregular"):
        return next_custom_due(due, config)
    try:
        anchor_day = int(config.get("anchor_day")) if config.get("anchor_day") else None
    except (TypeError, ValueError) as exc:
        logger.debug("next_occurrence：忽略 %s", type(exc).__name__)
        anchor_day = None
    if lunar:
        return next_lunar_due(due, rule, anchor_day)
    return next_due(due, rule, anchor_day)


ANCHOR_RULES = ("month", "year")


def ensure_anchor(config_raw: str, due: date) -> str:
    """给按月/按年的循环补上 ``anchor_day``（系列最初选定的日号）。

    没有它，1/31 完成一次就会漂到 2/28，之后永远停在 28 号。锚点随 ``repeat_config``
    一起同步，因此每台设备推进出来的日期一致。
    """
    config = parse_config(config_raw)
    if config.get("anchor_day"):
        return config_raw
    config["anchor_day"] = due.day
    return serialize_config(config)


def describe(rule: str, config_raw: str) -> str:
    """列表里的简短循环说明，如「每 1 周 · 周六」「每月最后一个工作日」。"""
    if rule not in ("custom", "irregular"):
        return REPEAT_LABELS.get(rule, "")
    return describe_custom(parse_config(config_raw))
