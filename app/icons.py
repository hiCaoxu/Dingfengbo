"""全局统一使用 Flet 的线性（Outlined）图标，描边风格一致。"""

from __future__ import annotations

import flet as ft

_FALLBACK = "CIRCLE_OUTLINED"


def ico(name: str) -> str:
    """取线性图标名；低版本 Flet 缺少对应常量时回退到线性圆点。"""
    value = getattr(ft.Icons, name, None)
    if value is None:
        alt = name.replace("_OUTLINED", "_SHARP")
        value = getattr(ft.Icons, alt, None)
    return value or getattr(ft.Icons, _FALLBACK, ft.Icons.CIRCLE)


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
