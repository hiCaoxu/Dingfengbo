"""以服务端时间为公共时间轴的时钟偏移估计（秒）。

同步时观察服务端返回的 ``server_time``，计算 ``offset = server_time - local``，
写入路径据此把 ``updated_at`` 对齐到服务端时间轴，使 LWW 的「最后」重新等价于
真实世界的「最后」，规避设备时钟回拨/快走导致的静默丢数据。
"""

from __future__ import annotations

import logging
import statistics
from collections import deque

logger = logging.getLogger(__name__)

# 保留最近几次观测：单次同步的 RTT 抖动（网络抖动、服务端慢查询）会让偏移瞬间
# 偏掉几百毫秒，而写入路径会把这个偏移直接叠加到每条记录的 updated_at 上——
# 于是「谁的更新更晚」被一次网络抖动改写。取中位数可以让单个离群值不影响结果。
_HISTORY: deque[float] = deque(maxlen=5)
_clock_offset = 0.0


def get_clock_offset() -> float:
    return _clock_offset


def set_clock_offset(offset: float) -> None:
    """直接设定期望值（测试/手动校准时用），同时清空观测历史。"""
    global _clock_offset
    _clock_offset = offset
    _HISTORY.clear()


def observe_offset(sample: float) -> float:
    """记录一次观测，返回用于写路径的稳定偏移（最近 5 次的中位数）。"""
    global _clock_offset
    _HISTORY.append(float(sample))
    _clock_offset = round(statistics.median(_HISTORY), 3)
    return _clock_offset


def reset_observations() -> None:
    """清空观测历史（测试用；不清当前偏移）。"""
    _HISTORY.clear()
