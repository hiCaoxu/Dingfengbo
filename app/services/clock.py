"""以服务端时间为公共时间轴的时钟偏移估计（秒）。

同步时观察服务端返回的 ``server_time``，计算 ``offset = server_time - local``，
写入路径据此把 ``updated_at`` 对齐到服务端时间轴，使 LWW 的「最后」重新等价于
真实世界的「最后」，规避设备时钟回拨/快走导致的静默丢数据。
"""

from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

_clock_offset = 0.0


def get_clock_offset() -> float:
    return _clock_offset


def set_clock_offset(offset: float) -> None:
    global _clock_offset
    _clock_offset = offset
