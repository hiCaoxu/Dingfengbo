"""matplotlib 图表 -> PNG bytes，供 Flet Image 直接显示。"""

from __future__ import annotations

import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from ..theme import ACCENT, DIVIDER, PRIMARY, TEXT, TEXT_MUTED

_FONT_CANDIDATES = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC", "Arial Unicode MS"]
_available = {f.name for f in font_manager.fontManager.ttflist}
for _font in _FONT_CANDIDATES:
    if _font in _available:
        plt.rcParams["font.sans-serif"] = [_font]
        break
plt.rcParams["axes.unicode_minus"] = False


def _encode(fig: plt.Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor("white")
    ax.grid(axis="y", color=DIVIDER, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(DIVIDER)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9)


def line_chart(
    labels: list[str],
    series: list[dict[str, Any]],
    title: str = "",
    height: float = 2.6,
) -> bytes:
    fig, ax = plt.subplots(figsize=(6.4, height))
    for item in series:
        ax.plot(labels, item["values"], marker="o", linewidth=2, color=item.get("color", PRIMARY), label=item.get("name", ""))
        if item.get("fill"):
            ax.fill_between(range(len(labels)), item["values"], alpha=0.12, color=item.get("color", PRIMARY))
    _style(ax)
    if title:
        ax.set_title(title, color=TEXT, fontsize=11, loc="left", pad=8)
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_MUTED)
    fig.tight_layout()
    return _encode(fig)


def bar_chart(labels: list[str], values: list[float], title: str = "", color: str = PRIMARY) -> bytes:
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.bar(labels, values, color=color, width=0.55, alpha=0.9)
    _style(ax)
    if title:
        ax.set_title(title, color=TEXT, fontsize=11, loc="left", pad=8)
    fig.tight_layout()
    return _encode(fig)


def pie_chart(items: dict[str, float], title: str = "") -> bytes:
    fig, ax = plt.subplots(figsize=(4.2, 3.0))
    if items:
        colors = [PRIMARY, ACCENT, "#8FB69A", "#D9B98A", "#7E9BB5", "#C9C2B6"]
        ax.pie(
            list(items.values()),
            labels=list(items.keys()),
            autopct="%1.1f%%",
            startangle=90,
            colors=[colors[i % len(colors)] for i in range(len(items))],
            textprops={"color": TEXT, "fontsize": 9},
            wedgeprops={"width": 0.55, "edgecolor": "white"},
        )
    else:
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", color=TEXT_MUTED, transform=ax.transAxes)
    ax.axis("equal")
    if title:
        ax.set_title(title, color=TEXT, fontsize=11, loc="left")
    fig.tight_layout()
    return _encode(fig)
