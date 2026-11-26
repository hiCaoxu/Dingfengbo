"""金额运算与格式化。库存、界面、同步载荷和导出文件都用「元」，精确到分。

比较和累加走 Decimal，四舍五入到分，避免 ``0.1 + 0.2 != 0.3``。
``func.sum(金额列)`` 的结果会再走一遍类型装饰器，已经量化到分，业务代码用
``to_decimal`` / ``to_float`` 即可。
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")
ZERO = Decimal("0")


def to_decimal(value: object) -> Decimal:
    """任意输入 → Decimal。

    float 走 ``repr``（``repr(0.1)`` 是 ``'0.1'``，而不是 ``0.1000000000000000055``），
    这样「用户输入的 0.1」不会被二进制误差污染。
    """
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    if isinstance(value, bool):
        return Decimal(int(value))
    text = repr(value) if isinstance(value, float) else str(value)
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        logger.debug("to_decimal：忽略 %s", type(exc).__name__)
        return ZERO
    return parsed if parsed.is_finite() else ZERO


def quantize(value: object) -> Decimal:
    """量化到分，四舍五入（ROUND_HALF_UP）。"""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_float(value: object) -> float:
    """量化到分后转回 float（用于落库与界面计算）。"""
    return float(quantize(value))


def total(values: Iterable[object]) -> Decimal:
    """按分累加，返回精确的 Decimal 合计。"""
    result = ZERO
    for value in values:
        result += quantize(value)
    return result


def is_over(used: object, limit: object) -> bool:
    """是否超支：按分比较，避免 `3.3000000000000003 > 3.3` 这类误判。"""
    return quantize(used) > quantize(limit)


def percent(part: object, whole: object) -> int:
    """百分比（四舍五入到整数）；whole 为 0 时返回 0。"""
    denominator = quantize(whole)
    if denominator == 0:
        return 0
    return int((quantize(part) / denominator * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(value: object) -> str:
    """格式化成「¥1,234.50」。"""
    return f"¥{quantize(value):,.2f}"
