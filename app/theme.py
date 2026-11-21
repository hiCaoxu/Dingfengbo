"""主题：亮色/暗色两套色板 + 卡片/文字样式工厂。

色板以模块级常量导出（``TEXT`` 等），同时提供 :func:`set_mode` 在运行时整体
替换。界面模块统一通过 ``theme.X`` 读取（而不是把常量逐个 import 进来），
这样切换主题后重新 ``build()`` 就能拿到新颜色：静态导入会把导入时的值钉死。
"""

from __future__ import annotations

import flet as ft
from flet import Border, TextStyle

LIGHT = {
    "BG": "#FAF7F2",
    "CARD": "#FFFFFF",
    "TEXT": "#2E2A26",
    "TEXT_MUTED": "#8C857C",
    "DIVIDER": "#EDE8DF",
    "PRIMARY": "#4A7C59",
    "ACCENT": "#C97B63",
    "DANGER": "#E53935",
    "PRIMARY_LIGHT": "#EEF3EE",
    "ACCENT_LIGHT": "#F6EDE9",
    "ON_PRIMARY": "#FFFFFF",
    "HEATMAP_LEVELS": ["#EEF3EE", "#D7E5D9", "#BBD3BF", "#8FB69A", "#5E8F6E", "#4A7C59"],
}

DARK = {
    "BG": "#1B1A18",
    "CARD": "#252422",
    "TEXT": "#EDE8E1",
    "TEXT_MUTED": "#9C948A",
    "DIVIDER": "#3A3833",
    "PRIMARY": "#7FB08D",
    "ACCENT": "#D28E76",
    "DANGER": "#EF6B68",
    "PRIMARY_LIGHT": "#2C3A31",
    "ACCENT_LIGHT": "#3B302B",
    "ON_PRIMARY": "#12211A",
    "HEATMAP_LEVELS": ["#2C3A31", "#35553F", "#3F7050", "#4E8A60", "#60A473", "#7FB08D"],
}

PALETTES = {"light": LIGHT, "dark": DARK}

# 布局常量与主题无关
NAV_WIDTH = 200
CARD_RADIUS = 12
CARD_PADDING = 16

_mode = "light"

# 默认值先落成模块属性，兼容仍按常量导入的历史代码。
globals().update(LIGHT)


def current_mode() -> str:
    return _mode


def is_dark() -> bool:
    return _mode == "dark"


def set_mode(mode: str) -> str:
    """切换主题；返回实际生效的模式（未知值回退 light）。"""
    global _mode
    _mode = "dark" if str(mode).lower() == "dark" else "light"
    globals().update(PALETTES[_mode])
    return _mode


def toggle_mode() -> str:
    return set_mode("light" if is_dark() else "dark")


def palette() -> dict:
    return PALETTES[_mode]


def card_border() -> Border:
    return ft.Border.all(1, DIVIDER)


def title_style(size: int = 13, color: str | None = None, bold: bool = True) -> TextStyle:
    return TextStyle(size=size, color=color or TEXT, weight="bold" if bold else "normal")


def body_style(size: int = 12, color: str | None = None) -> TextStyle:
    return TextStyle(size=size, color=color or TEXT_MUTED)


def money_style(size: int = 20) -> TextStyle:
    return TextStyle(size=size, color=TEXT, weight="bold")


def card_padding() -> ft.Padding:
    return ft.Padding.all(CARD_PADDING)


def icon_size() -> int:
    return 20
