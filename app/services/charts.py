"""matplotlib 图表 -> PNG bytes，供 Flet Image 直接显示。"""

from __future__ import annotations

import io
import math
import threading
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from .. import theme as th

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
        return _chart_cache.get((th.current_mode(), key))


def _cache_put(key: tuple, value: bytes) -> None:
    with _cache_guard:
        if len(_chart_cache) >= _CACHE_MAX:
            _chart_cache.pop(next(iter(_chart_cache)))
        _chart_cache[(th.current_mode(), key)] = value


def _encode(fig: plt.Figure, tight: bool = True) -> bytes:
    """输出 PNG。

    ``tight=False`` 时按 figsize × dpi 输出**固定像素尺寸**：柱状图/折线图的标签
    数量会变，用 ``bbox_inches="tight"`` 会裁掉不同宽度的空白，导致图片尺寸随数据
    抖动（视觉上柱子忽粗忽细、卡片对齐漂移）。固定画布 + ``tight_layout`` 兼顾对齐。
    """
    buffer = io.BytesIO()
    kwargs = {"bbox_inches": "tight"} if tight else {}
    fig.savefig(buffer, format="png", dpi=110, facecolor=th.CHART_BG, **kwargs)
    plt.close(fig)
    return buffer.getvalue()


def _resolve_ylim(
    values: list[float], y_range: tuple[float, float] | None
) -> tuple[float, float] | None:
    """固定纵轴范围；数据越界时自动扩展，避免把点裁到画布外。"""
    if y_range is None:
        return None
    low, high = float(y_range[0]), float(y_range[1])
    if values:
        low = min(low, math.floor(min(values)))
        high = max(high, math.ceil(max(values)))
    if high <= low:
        high = low + 1
    return low, high


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(th.CHART_BG)
    ax.grid(axis="y", color=th.DIVIDER, linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(th.DIVIDER)
    ax.tick_params(colors=th.TEXT_MUTED, labelsize=9)


def line_chart(
    labels: list[str],
    series: list[dict[str, Any]],
    title: str = "",
    height: float = 2.6,
    cache_key: Any = None,
    y_range: tuple[float, float] | None = None,
) -> bytes:
    """折线图 PNG bytes。

    ``cache_key`` 约定：一旦提供就**完全替代**内容比对（命中即直接返回旧图），
    所以它必须包含「数据一变就变」的单调量，例如 ``(行数, max(updated_at))``。
    传不严谨的 key（如只传固定字符串）会拿到陈旧图表且没有任何告警。

    ``y_range`` 用于固定纵轴（如体重 50-70），数据越界时自动扩展以免裁掉数据点。
    """
    key_series = tuple(
        (
            str(item.get("name", "")),
            tuple(float(value) for value in item["values"]),
            str(item.get("color", th.PRIMARY)),
            bool(item.get("fill", False)),
        )
        for item in series
    )
    key = (
        (cache_key, title, height, y_range)
        if cache_key is not None
        else (tuple(labels), key_series, title, height, y_range)
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached

    all_values = [float(value) for _n, values, _c, _f in key_series for value in values]
    ylim = _resolve_ylim(all_values, y_range)

    with _RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(6.4, height))
        for name, values, color, fill in key_series:
            ax.plot(labels, values, marker="o", linewidth=2, color=color, label=name)
            if fill:
                ax.fill_between(range(len(labels)), values, alpha=0.12, color=color)
        _style(ax)
        if len(labels) > 7:
            # 14 天的日期标签横排会互相挤压，斜置并缩一号字。
            ax.tick_params(axis="x", labelrotation=45, labelsize=8)
        if ylim is not None:
            ax.set_ylim(*ylim)
        if title:
            ax.set_title(title, color=th.TEXT, fontsize=11, loc="left", pad=8)
        if len(series) > 1:
            ax.legend(frameon=False, fontsize=9, labelcolor=th.TEXT_MUTED)
        fig.tight_layout()
        data = _encode(fig, tight=False)
    _cache_put(key, data)
    return data


def bar_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
    color: str = th.PRIMARY,
    cache_key: Any = None,
    y_range: tuple[float, float] | None = None,
) -> bytes:
    """柱状图 PNG bytes；``cache_key`` 契约同 :func:`line_chart`。

    画布尺寸与横轴范围都固定：柱子数量变化时单根柱子的宽度不变，
    四张图在 2×2 网格里也保持同一像素尺寸。

    ``y_range`` 为**严格**纵轴范围（不做越界扩展）：柱状图里 0 代表「当天无记录」，
    落在轴外正好不显示；若数据超出范围也按范围裁切，保持刻度稳定可比。
    """
    key_values = tuple(float(value) for value in values)
    key = (cache_key, title, color, y_range) if cache_key is not None else (
        tuple(labels), key_values, title, color, y_range
    )
    cached = _cache_get(key)
    if cached is not None:
        return cached

    with _RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(6.4, 2.6))
        ax.bar(range(len(key_values)), key_values, color=color, width=0.62, alpha=0.9)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
        # 固定横轴边界：否则只有几天数据时 matplotlib 会把少数柱子拉宽填满画布。
        ax.set_xlim(-0.5, max(len(key_values), 1) - 0.5)
        _style(ax)
        if y_range is not None:
            ax.set_ylim(float(y_range[0]), float(y_range[1]))
        if len(labels) > 7:
            ax.tick_params(axis="x", labelrotation=45, labelsize=8)
        if title:
            ax.set_title(title, color=th.TEXT, fontsize=11, loc="left", pad=8)
        fig.tight_layout()
        data = _encode(fig, tight=False)
    _cache_put(key, data)
    return data


def pie_chart(items: dict[str, float], title: str = "", cache_key: Any = None) -> bytes:
    """饼图 PNG bytes；``cache_key`` 契约同 :func:`line_chart`。"""
    key_items = tuple((key, float(value)) for key, value in items.items())
    key = (cache_key, title) if cache_key is not None else (key_items, title)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    with _RENDER_LOCK:
        fig, ax = plt.subplots(figsize=(4.2, 3.0))
        if key_items:
            # 配色全部取自当前主题：写死 #8FB69A 这类浅色在暗色主题下几乎糊成一团。
            colors = [
                th.PRIMARY,
                th.ACCENT,
                th.HEATMAP_LEVELS[4],
                th.HEATMAP_LEVELS[3],
                th.HEATMAP_LEVELS[2],
                th.TEXT_MUTED,
            ]
            labels = [key for key, _ in key_items]
            values = [value for _, value in key_items]
            ax.pie(
                values,
                labels=labels,
                autopct="%1.1f%%",
                startangle=90,
                colors=[colors[i % len(colors)] for i in range(len(key_items))],
                textprops={"color": th.TEXT, "fontsize": 9},
                wedgeprops={"width": 0.55, "edgecolor": th.CHART_BG},
            )
        else:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", color=th.TEXT_MUTED, transform=ax.transAxes)
        ax.axis("equal")
        if title:
            ax.set_title(title, color=th.TEXT, fontsize=11, loc="left")
        fig.tight_layout()
        data = _encode(fig)
    _cache_put(key, data)
    return data
