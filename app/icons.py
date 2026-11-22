"""全局统一使用 Flet 的线性（Outlined）图标，描边风格一致。"""

from __future__ import annotations

import logging
import flet as ft

logger = logging.getLogger(__name__)

_FALLBACK = "CIRCLE_OUTLINED"
_VARIANT_SUFFIXES = ("_OUTLINED", "_SHARP", "_ROUND", "_FILLED")


def ico(name: str) -> str:
    """取线性图标名；低版本 Flet 缺少对应常量时回退到线性圆点。

    调用方偶尔会传小写（如 ``"fitness_center"``）或不带变体后缀的名字。
    ``getattr`` 大小写敏感，不统一转大写就会静默回退成空心圆，图标语义全丢；
    而裸名（``"LINK"``）通常存在实心版本，因此优先补 ``_OUTLINED`` 再退回裸名，
    保持全项目"线性图标"的一致风格。
    """
    key = (name or "").upper()
    candidates: list[str] = []
    if key and not key.endswith(_VARIANT_SUFFIXES):
        candidates.append(f"{key}_OUTLINED")
    candidates.append(key)
    for candidate in candidates:
        value = getattr(ft.Icons, candidate, None)
        if value is not None:
            return value
    alt = key.replace("_OUTLINED", "_SHARP")
    return getattr(ft.Icons, alt, None) or getattr(ft.Icons, _FALLBACK, ft.Icons.CIRCLE)


NAV = {
    "home": "HOME_OUTLINED",
    "todo": "TASK_ALT_OUTLINED",
    "diary": "AUTO_STORIES_OUTLINED",
    "notes": "DESCRIPTION_OUTLINED",
    "health": "FAVORITE_OUTLINED",
    "fun": "MOVIE_OUTLINED",
    "finance": "ACCOUNT_BALANCE_WALLET_OUTLINED",
    "habits": "TRACK_CHANGES_OUTLINED",
    "reports": "INSIGHTS_OUTLINED",
    "timeline": "TIMELINE_OUTLINED",
    "ai": "AUTO_AWESOME_OUTLINED",
}

QUICK = {
    "todo": "ADD_TASK_OUTLINED",
    "note": "NOTE_ADD_OUTLINED",
    "finance": "PAYMENTS_OUTLINED",
    "exercise": "FITNESS_CENTER_OUTLINED",
    "diet": "RESTAURANT_OUTLINED",
    "weight": "MONITOR_HEART_OUTLINED",
    "sleep": "BEDTIME_OUTLINED",
}

ACTIONS = {
    "sync": "SYNC_OUTLINED",
    "settings": "SETTINGS_OUTLINED",
    "add": "ADD_OUTLINED",
    "search": "SEARCH_OUTLINED",
    "edit": "EDIT_OUTLINED",
    "delete": "DELETE_OUTLINED",
    "close": "CLOSE_OUTLINED",
    "check": "CHECK_OUTLINED",
    "chevron_left": "CHEVRON_LEFT_OUTLINED",
    "chevron_right": "CHEVRON_RIGHT_OUTLINED",
    "calendar": "CALENDAR_MONTH_OUTLINED",
    "list": "LIST_OUTLINED",
    "grid": "GRID_VIEW_OUTLINED",
    "today": "TODAY_OUTLINED",
    "timer": "TIMER_OUTLINED",
    "play": "PLAY_ARROW_OUTLINED",
    "stop": "STOP_CIRCLE_OUTLINED",
    "save": "SAVE_OUTLINED",
    "download": "DOWNLOAD_OUTLINED",
    "upload": "UPLOAD_OUTLINED",
    "link": "LINK_OUTLINED",
    "backup": "BACKUP_OUTLINED",
}
