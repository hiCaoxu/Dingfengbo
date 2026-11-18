"""米白清爽主题：色板、字号与通用样式。"""

from __future__ import annotations

import flet as ft
from flet import Border, TextStyle

BG = "#FAF7F2"
CARD = "#FFFFFF"
TEXT = "#2E2A26"
TEXT_MUTED = "#8C857C"
DIVIDER = "#EDE8DF"
PRIMARY = "#4A7C59"
ACCENT = "#C97B63"
PRIMARY_LIGHT = "#EEF3EE"
ACCENT_LIGHT = "#F6EDE9"

HEATMAP_LEVELS = ["#EEF3EE", "#D7E5D9", "#BBD3BF", "#8FB69A", "#5E8F6E", "#4A7C59"]

NAV_WIDTH = 200
CARD_RADIUS = 12
CARD_PADDING = 16


def card_border() -> Border:
    return ft.Border.all(1, DIVIDER)


def title_style(size: int = 13, color: str = TEXT, bold: bool = True) -> TextStyle:
    return TextStyle(size=size, color=color, weight="bold" if bold else "normal")


def body_style(size: int = 12, color: str = TEXT_MUTED) -> TextStyle:
    return TextStyle(size=size, color=color)


def money_style(size: int = 20) -> TextStyle:
    return TextStyle(size=size, color=TEXT, weight="bold")


def card_padding() -> ft.Padding:
    return ft.Padding.all(CARD_PADDING)


def icon_size() -> int:
    return 20
