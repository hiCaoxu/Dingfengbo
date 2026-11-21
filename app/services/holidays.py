"""法定节假日：读取本地 holidays.txt，供「跳过法定节假日」使用。

不内置节假日数据：法定调休每年调整、各地差异大，写死反而会误跳过工作日。
用户可在数据目录维护 ``holidays.txt``，每行一个 ``YYYY-MM-DD``（``#`` 之后为注释）：
    D:\\Data\\Dingfengbo\\holidays.txt
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

HOLIDAYS_PATH: Path = DATA_DIR / "holidays.txt"

_cache: tuple[tuple[float, int], set[date]] | None = None


def holiday_dates(path: Path | None = None) -> set[date]:
    """读取并缓存节假日集合；文件变化（mtime/size）后自动重读。"""
    global _cache
    target = Path(path or HOLIDAYS_PATH)
    try:
        stat = target.stat()
    except OSError:
        _cache = None
        return set()
    signature = (stat.st_mtime, stat.st_size)
    if _cache is not None and _cache[0] == signature:
        return _cache[1]

    dates: set[date] = set()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.debug("读取节假日文件失败：%s", target, exc_info=True)
        return set()
    for line in lines:
        raw = line.split("#", 1)[0].strip()
        if not raw:
            continue
        try:
            dates.add(date.fromisoformat(raw[:10]))
        except ValueError:
            continue
    _cache = (signature, dates)
    logger.info("已加载 %d 个节假日：%s", len(dates), target)
    return dates


def is_holiday(value: date, path: Path | None = None) -> bool:
    return value in holiday_dates(path)
