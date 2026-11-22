"""金额运算与格式化：内部一律用 Decimal，落库/同步仍保持「元」的 float。

为什么不把存储改成整数分：金额字段参与同步协议，旧版本客户端仍然按「元」推送
浮点数，改存储格式会让跨版本同步互相写坏数据。这里退一步，在四个边界上统一走
Decimal：

- **累加**：多笔金额相加（SUM 结果、账户余额、月度收支）不再受二进制浮点漂移影响；
- **比较**：`1.10 + 2.20 > 3.30` 这类误判在比较前先量化到分；
- **舍入**：统一 ROUND_HALF_UP（`f"{x:.2f}"` 默认是 half-even，1.005 会变 1.00）；
- **展示**：`money()` 用同一个量化口径输出。

所有对外函数仍返回 float（量化到分后的 float），调用方无需感知 Decimal。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable

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
    except (InvalidOperation, ValueError):
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
