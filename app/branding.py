"""应用标志。侧栏、窗口图标和托盘共用 app/assets/logo.png。"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

_LOGO = Path(__file__).resolve().parent / "assets" / "logo.png"
_ICO = Path(__file__).resolve().parents[1] / "packaging" / "icon.ico"


def logo_path() -> Path:
    if _LOGO.is_file():
        return _LOGO
    root = getattr(sys, "_MEIPASS", None)
    if root:
        bundled = Path(root) / "app" / "assets" / "logo.png"
        if bundled.is_file():
            return bundled
    return _LOGO


@lru_cache(maxsize=1)
def logo_bytes() -> bytes:
    return logo_path().read_bytes()


def window_icon_path() -> Path | None:
    """Windows 窗口图标。源码运行用 packaging/icon.ico，打包后用随包的那份。"""
    root = getattr(sys, "_MEIPASS", None)
    candidate = Path(root) / "icon.ico" if root else _ICO
    return candidate if candidate.is_file() else None


def logo_mark(size: int):
    """界面上的标志。用字节交给 Flet，不依赖 assets 目录是否配过。"""
    import flet as ft

    return ft.Image(
        src=logo_bytes(),
        width=size,
        height=size,
        fit=ft.BoxFit.CONTAIN,
        border_radius=6,
    )
