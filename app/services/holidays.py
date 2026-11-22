"""法定节假日与调休上班日。

数据 = 内置年度数据（:mod:`holiday_data`，随版本更新） ∪ 用户补充文件
``D:\\Data\\Dingfengbo\\holidays.txt``：

    2028-01-01          # 放假
    !2028-01-02         # 调休上班日（按工作日处理）

不做「启动时写库」：节假日是逐年更新的静态数据，放代码里能随版本升级、
避免双数据源漂移；文件只用于补充以后年份或本地差异。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from ..config import DATA_DIR
from .holiday_data import HOLIDAY_DATES, HOLIDAY_NAMES, MAKEUP_WORKDAYS

logger = logging.getLogger(__name__)

HOLIDAYS_PATH: Path = DATA_DIR / "holidays.txt"

_SAMPLE = """\
# 节假日补充文件（可选）
# 每行一个日期：
#   2028-01-01    表示放假
#   !2028-01-02   表示调休上班日（按工作日处理）
# 内置数据已覆盖 2026-2027 年，本文件用于补充以后年份或本地差异。
"""

_cache: dict[str, tuple[tuple[float, int], tuple[set[date], set[date]]]] = {}

# 内置数据的覆盖年份（holiday_data 里最新的一年）。超出这个年份且用户没补充数据时，
# 结果会退化成「只按双休判断」，必须让用户知道。
COVERED_LAST_YEAR = max(item.year for item in HOLIDAY_DATES) if HOLIDAY_DATES else 0
_warned_years: set[int] = set()


def _warn_if_uncovered(value: date, path: Path | None = None) -> None:
    """超出内置覆盖年份且补充文件也没写到这一年时，记一次警告。"""
    if not COVERED_LAST_YEAR or value.year <= COVERED_LAST_YEAR:
        return
    if value.year in _warned_years:
        return
    extra, workdays = _extra(path)
    if any(item.year == value.year for item in extra) or any(
        item.year == value.year for item in workdays
    ):
        return
    _warned_years.add(value.year)
    logger.warning(
        "节假日数据只覆盖到 %d 年，%d 年按「周末休息」估算；"
        "可在节假日补充文件里追加该年安排",
        COVERED_LAST_YEAR, value.year,
    )


def _read_file(path: Path) -> tuple[set[date], set[date]]:
    """解析补充文件，返回（放假日, 调休上班日）。"""
    try:
        stat = path.stat()
    except OSError:
        return set(), set()
    key = str(path)
    signature = (stat.st_mtime, stat.st_size)
    cached = _cache.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]

    holidays: set[date] = set()
    workdays: set[date] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.debug("读取节假日文件失败：%s", path, exc_info=True)
        return set(), set()
    for line in lines:
        raw = line.split("#", 1)[0].strip()
        if not raw:
            continue
        target = workdays if raw.startswith("!") else holidays
        try:
            target.add(date.fromisoformat(raw.lstrip("!").strip()[:10]))
        except ValueError:
            continue
    _cache[key] = (signature, (holidays, workdays))
    logger.info(
        "节假日补充文件已加载：%s（放假 %d 天，补班 %d 天）",
        path, len(holidays), len(workdays),
    )
    return holidays, workdays


def _extra(path: Path | None = None) -> tuple[set[date], set[date]]:
    return _read_file(Path(path or HOLIDAYS_PATH))


def holiday_dates(path: Path | None = None) -> set[date]:
    """全部放假日（内置 ∪ 补充文件）。"""
    extra, _ = _extra(path)
    return set(HOLIDAY_DATES) | extra


def makeup_workdays(path: Path | None = None) -> set[date]:
    """调休上班日（内置 ∪ 补充文件）。"""
    _, extra = _extra(path)
    return set(MAKEUP_WORKDAYS) | extra


def holiday_name(value: date, path: Path | None = None) -> str:
    """节日名称；未知返回空串。"""
    return HOLIDAY_NAMES.get(value, "")


def is_holiday(value: date, path: Path | None = None) -> bool:
    _warn_if_uncovered(value, path)
    if value in HOLIDAY_DATES:
        return True
    extra, _ = _extra(path)
    return value in extra


def is_makeup_workday(value: date, path: Path | None = None) -> bool:
    if value in MAKEUP_WORKDAYS:
        return True
    _, extra = _extra(path)
    return value in extra


def is_weekend_off(value: date, path: Path | None = None) -> bool:
    """周末休息（调休上班的周末不算休息）。"""
    if value.weekday() < 5:
        return False
    return not is_makeup_workday(value, path)


def ensure_sample_file(path: Path | None = None) -> None:
    """首次启动时生成带注释的补充文件模板，方便用户按年追加。"""
    target = Path(path or HOLIDAYS_PATH)
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_SAMPLE, encoding="utf-8")
    except OSError:
        logger.debug("生成节假日模板失败：%s", target, exc_info=True)
