"""matplotlib 图表 -> PNG bytes，供 Flet Image 直接显示。"""

from __future__ import annotations

import io
import threading
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

# matplotlib 的 pyplot 状态非线程安全，图表在后台线程渲染时需串行化。
_RENDER_LOCK = threading.Lock()

# 图表缓存：键为语义摘要（cache_key），值为 PNG bytes。
# 调用方可传入 cache_key（如 日期范围+记录数+max(updated_at)），数据未变时直接复用缓存，
# 避免把完整数据元组作为缓存键带来的 hash 开销。
_chart_cache: dict[tuple, bytes] = {}
_CACHE_MAX = 64
_cache_guard = threading.Lock()


def _cache_get(key: tuple) -> bytes | None:
    with _cache_guard:
        return _chart_cache.get(key)


def _cache_put(key: tuple, value: bytes) -> None:
    with _cache_guard:
        if len(_chart_cache) >= _CACHE_MAX:
            _chart_cache.pop(next(iter(_chart_cache)))
        _chart_cache[key] = value


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
    cache_key: Any = None,
) -> bytes:
    key_series = tuple(
        (
            str(item.get("name", "")),
            tuple(float(value) for value in item["values"]),
            str(item.get("color", PRIMARY)),
            bool(item.get("fill", False)),
        )
        for item in series
    )
    key = (cache_key, title, height) if cache_key is not None else (tuple(labels), key_series, title, height)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    with _RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(6.4, height))
        for name, values, color, fill in key_series:
            ax.plot(labels, values, marker="o", linewidth=2, color=color, label=name)
            if fill:
                ax.fill_between(range(len(labels)), values, alpha=0.12, color=color)
        _style(ax)
        if title:
            ax.set_title(title, color=TEXT, fontsize=11, loc="left", pad=8)
        if len(series) > 1:
            ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_MUTED)
        fig.tight_layout()
        data = _encode(fig)
    _cache_put(key, data)
    return data


def bar_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
    color: str = PRIMARY,
    cache_key: Any = None,
) -> bytes:
    key_values = tuple(float(value) for value in values)
    key = (cache_key, title, color) if cache_key is not None else (tuple(labels), key_values, title, color)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    with _RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(6.4, 2.6))
        ax.bar(labels, key_values, color=color, width=0.55, alpha=0.9)
        _style(ax)
        if title:
            ax.set_title(title, color=TEXT, fontsize=11, loc="left", pad=8)
        fig.tight_layout()
        data = _encode(fig)
    _cache_put(key, data)
    return data


def pie_chart(items: dict[str, float], title: str = "", cache_key: Any = None) -> bytes:
    key_items = tuple((key, float(value)) for key, value in items.items())
    key = (cache_key, title) if cache_key is not None else (key_items, title)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    with _RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(4.2, 3.0))
        if key_items:
            colors = [PRIMARY, ACCENT, "#8FB69A", "#D9B98A", "#7E9BB5", "#C9C2B6"]
            labels = [key for key, _ in key_items]
            values = [value for _, value in key_items]
            ax.pie(
                values,
                labels=labels,
                autopct="%1.1f%%",
                startangle=90,
                colors=[colors[i % len(colors)] for i in range(len(key_items))],
                textprops={"color": TEXT, "fontsize": 9},
                wedgeprops={"width": 0.55, "edgecolor": "white"},
            )
        else:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", color=TEXT_MUTED, transform=ax.transAxes)
        ax.axis("equal")
        if title:
            ax.set_title(title, color=TEXT, fontsize=11, loc="left")
        fig.tight_layout()
        data = _encode(fig)
    _cache_put(key, data)
    return data
