"""金额运算与格式化：库里存**整数分**，ORM 与同步协议仍以「元」暴露。

两个口径必须分开：

- **存储**：`transactions.amount` 等四列存整数分（12.34 元 → 1234），SUM 与比较
  完全精确，不会再出现 `0.1 + 0.2 != 0.3`。
- **协议**：同步载荷与导出文件仍是「元」的浮点数。旧版本客户端按元推送，若把线上
  格式也改成整数分，老客户端的 `12.34` 会被新端当成分读成 1/100——跨版本数据直接
  写坏。换算收敛在 `models.MoneyCents` 类型装饰器里，业务代码继续用元。

`from_cents()` 则是给 SQL 聚合用的：`SUM(金额列)` 拿到的是分，必须换算回元。
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


def cents(value: object) -> int:
    """「元」→ 整数分（四舍五入到分），用于落库。"""
    return int(quantize(value) * 100)


def from_cents(value: object) -> Decimal:
    """整数分 → Decimal「元」。

    SQL 的 ``SUM(金额列)`` 拿到的是**分**（列里就是这么存的），聚合后必须经过这里
    换算回元，否则整条链路会大 100 倍。
    """
    return Decimal(int(to_decimal(value))) / 100
