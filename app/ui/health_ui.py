"""健康：锻炼/饮食/体重/睡眠记录与周月图表。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

import flet as ft
from sqlalchemy import func, select

from ..db.models import Diet, Exercise, Sleep, Weight
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.charts import bar_chart, line_chart
from ..services.health import bmi
from .. import theme as th
from .async_utils import background_section
from .forms import open_diet_dialog, open_exercise_dialog, open_sleep_dialog, open_weight_dialog
from .state import state
from .widgets import card, delete_with_confirm, empty, icon_button, primary_button, section_header

logger = logging.getLogger(__name__)

_state = state.health
# 汇总页图表尺寸：2×2 排布，单张宽度留出卡片内边距。
_CHART_W = 430
_CHART_H = 168
_TABS = {
    "锻炼": (Exercise, open_exercise_dialog),
    "饮食": (Diet, open_diet_dialog),
    "体重": (Weight, open_weight_dialog),
    "睡眠": (Sleep, open_sleep_dialog),
}


def _chart_placeholder() -> ft.Control:
    return ft.Container(
        content=ft.Text("加载中…", size=11, color=th.TEXT_MUTED),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def _chart_slot(width: int, height: int) -> ft.Container:
    """图表占位槽：初始显示加载提示，后台渲染完成后替换为 Image。"""
    return ft.Container(
        content=_chart_placeholder(),
        width=width,
        height=height,
        alignment=ft.Alignment.CENTER,
    )


def _date_range(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if _state["range"] == "本周":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7)
    start = today.replace(day=1)
    if start.month == 12:
        return start, date(start.year + 1, 1, 1)
    return start, date(start.year, start.month + 1, 1)


def _delete(model, item_id: str, page, refresh) -> None:
    delete_with_confirm(page, model, item_id, refresh, "确定删除该记录？")


def _row(item, tab: str, page, refresh) -> ft.Control:
    if tab == "锻炼":
        primary = f"{item.kind} · {item.minutes} 分钟"
        secondary = f"{item.date} · {item.calories:.0f} kcal" + (f" · {item.distance_km} km" if item.distance_km else "")
    elif tab == "饮食":
        primary = f"{item.meal} · {item.food} {item.grams:g}g"
        secondary = f"{item.date} · {item.calories:.0f} kcal"
    elif tab == "体重":
        value = bmi(item.kg)
        primary = f"{item.kg:.1f} kg"
        secondary = f"{item.date} · BMI {value}" + (f" · 体脂 {item.body_fat}%" if item.body_fat else "")
    else:
        primary = f"{item.bedtime} → {item.wake_time} · {item.hours:.1f} 小时"
        secondary = f"{item.date} · 质量 {item.quality}/5"
    return ft.Container(
        content=ft.Row(
            [
                ft.Column([ft.Text(primary, size=12, color=th.TEXT, weight="bold"),
                           ft.Text(secondary, size=10, color=th.TEXT_MUTED)], spacing=3, expand=True),
                icon_button(ACTIONS["edit"], lambda e, i=item: _TABS[tab][1](page, i, refresh), "编辑"),
                icon_button(ACTIONS["delete"], lambda e, i=item.id: _delete(_TABS[tab][0], i, page, refresh), "删除"),
            ],
            spacing=8,
        ),
        border=ft.Border.all(1, th.DIVIDER),
        border_radius=10,
        padding=ft.Padding.symmetric(vertical=9, horizontal=12),
    )


def _summary_query() -> dict:
    """汇总首页数据（后台线程）：体重/BMI、今日摄入、本周锻炼、近 7 天睡眠与四张图表。"""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    chart_start = today - timedelta(days=13)
    with session_scope() as session:
        latest_weight = session.scalar(
            select(Weight)
            .where(Weight.is_deleted.is_(False))
            .order_by(Weight.date.desc(), Weight.updated_at.desc())
        )
        # 各项汇总一律交给 SQL 聚合，不把明细行搬进 Python。
        today_kcal = session.scalar(
            select(func.coalesce(func.sum(Diet.calories), 0.0)).where(
                Diet.is_deleted.is_(False), Diet.date == today
            )
        )
        exercise_count, exercise_minutes, exercise_kcal = session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Exercise.minutes), 0),
                func.coalesce(func.sum(Exercise.calories), 0.0),
            ).where(
                Exercise.is_deleted.is_(False),
                Exercise.date >= week_start,
                Exercise.date <= today,
            )
        ).one()
        sleep_avg = session.scalar(
            select(func.avg(Sleep.hours)).where(
                Sleep.is_deleted.is_(False),
                Sleep.date >= today - timedelta(days=6),
                Sleep.date <= today,
            )
        )
        charts = {}
        for model in (Weight, Sleep, Exercise, Diet):
            charts[model] = list(
                session.scalars(
                    select(model)
                    .where(
                        model.is_deleted.is_(False),
                        model.date >= chart_start,
                        model.date <= today,
                    )
                    .order_by(model.date)
                )
            )
    return {
        "latest_weight": latest_weight,
        "today_kcal": float(today_kcal or 0.0),
        "exercise_count": int(exercise_count or 0),
        "exercise_minutes": int(exercise_minutes or 0),
        "exercise_kcal": float(exercise_kcal or 0.0),
        "sleep_avg": float(sleep_avg) if sleep_avg is not None else None,
        "weights": charts[Weight],
        "sleeps": charts[Sleep],
        "exercises": charts[Exercise],
        "diets": charts[Diet],
    }


def _records_query() -> dict:
    """记录二级页数据（后台线程）：当前分类在所选范围内的明细。"""
    start, end = _date_range()
    tab = _state["tab"]
    model = _TABS[tab][0]
    with session_scope() as session:
        items = list(
            session.scalars(
                select(model)
                .where(
                    model.is_deleted.is_(False),
                    model.date >= start,
                    model.date < end,
                )
                .order_by(model.date.desc())
            )
        )
    return {"tab": tab, "range": _state["range"], "items": items}


def build(page: ft.Page, refresh) -> ft.Control:
    """健康首页只放汇总；明细记录在二级页面（view=records）。"""
    if _state["view"] == "records":
        return background_section(
            page, _records_query, lambda data: _render_records(page, refresh, data)
        )
    return background_section(
        page, _summary_query, lambda data: _render_summary(page, refresh, data)
    )


def _goto(view: str, refresh) -> None:
    _state["view"] = view
    refresh()


def _summary_card(title: str, value: str, subtitle: str, accent: bool = False) -> ft.Container:
    return card(
        ft.Column(
            [
                ft.Text(value, size=21, color=th.ACCENT if accent else th.TEXT, weight="bold"),
                ft.Text(subtitle, size=10, color=th.TEXT_MUTED),
            ],
            spacing=4,
        ),
        title=title,
        expand=1,
        height=96,
    )


def _quick_record_row(page: ft.Page, refresh) -> ft.Control:
    """汇总页的分类快捷记录：不必先进二级页再选分类。"""
    return ft.Row(
        [
            ft.Text("快捷记录", size=11, color=th.TEXT_MUTED),
            *[
                ft.TextButton(
                    f"+ {name}",
                    on_click=lambda e, tab=name: _TABS[tab][1](page, None, refresh),
                )
                for name in _TABS
            ],
        ],
        spacing=6,
    )


def _chart_grid(page: ft.Page, data: dict) -> ft.Control:
    """四张图表 2×2 排布：汇总页的信息密度比竖排更高，也不至于要滚很久。"""
    weights, sleeps = data["weights"], data["sleeps"]
    exercises, diets = data["exercises"], data["diets"]

    def labels_of(rows):
        return [r.date.strftime("%m-%d") for r in rows[-14:]]

    def daily_buckets(rows, attribute: str, days: int = 14):
        """按自然日补齐空档：柱状图始终 14 根柱子，宽度才不会随天数变化。"""
        totals = defaultdict(float)
        for row in rows:
            totals[row.date] += getattr(row, attribute)
        start = date.today() - timedelta(days=days - 1)
        labels: list[str] = []
        values: list[float] = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            labels.append(day.strftime("%m-%d"))
            values.append(totals.get(day, 0.0))
        return labels, values

    exercise_labels, exercise_minutes = daily_buckets(exercises, "minutes")
    diet_labels, diet_calories = daily_buckets(diets, "calories")
    sleep_labels, sleep_hours = daily_buckets(sleeps, "hours")

    width, height = _CHART_W, _CHART_H
    weight_slot = _chart_slot(width, height)
    sleep_slot = _chart_slot(width, height)
    exercise_slot = _chart_slot(width, height)
    diet_slot = _chart_slot(width, height)

    def _key(rows) -> tuple:
        return (len(rows), max((r.updated_at for r in rows), default=None))

    def render_charts() -> None:
        weight_slot.content = ft.Image(
            src=line_chart(
                labels_of(weights),
                [{"name": "体重 kg", "values": [w.kg for w in weights[-14:]], "color": th.PRIMARY, "fill": True}],
                "体重趋势",
                cache_key=_key(weights),
                y_range=(50, 70),
            ),
            width=width, height=height, fit=ft.BoxFit.CONTAIN,
        )
        sleep_slot.content = ft.Image(
            # 睡眠改成柱状图：每天一根，无记录的天为 0（固定 5-12 轴下不可见）。
            src=bar_chart(
                sleep_labels,
                sleep_hours,
                "睡眠时长",
                th.ACCENT,
                cache_key=_key(sleeps),
                y_range=(5, 12),
            ),
            width=width, height=height, fit=ft.BoxFit.CONTAIN,
        )
        exercise_slot.content = ft.Image(
            src=bar_chart(exercise_labels, exercise_minutes, "锻炼分钟", th.PRIMARY, cache_key=_key(exercises)),
            width=width, height=height, fit=ft.BoxFit.CONTAIN,
        )
        diet_slot.content = ft.Image(
            src=bar_chart(diet_labels, diet_calories, "饮食热量", th.ACCENT, cache_key=_key(diets)),
            width=width, height=height, fit=ft.BoxFit.CONTAIN,
        )
        try:
            page.update()
        except Exception:
            logger.exception("健康图表渲染后刷新页面失败")

    if page is not None:
        page.run_thread(render_charts)

    return ft.Column(
        [
            ft.Row(
                [
                    card(weight_slot, title="体重趋势", expand=1),
                    card(sleep_slot, title="睡眠时长", expand=1),
                ],
                spacing=12,
            ),
            ft.Row(
                [
                    card(exercise_slot, title="锻炼时长", expand=1),
                    card(diet_slot, title="饮食热量", expand=1),
                ],
                spacing=12,
            ),
        ],
        spacing=12,
    )


def _render_summary(page: ft.Page, refresh, data: dict) -> ft.Control:
    weight = data["latest_weight"]
    weight_value = f"{weight.kg:.1f} kg" if weight else "—"
    weight_sub = f"BMI {bmi(weight.kg)} · {weight.date}" if weight else "暂无体重记录"
    sleep_avg = data["sleep_avg"]

    return ft.Column(
        [
            section_header(
                "健康",
                ft.Row(
                    [
                        primary_button(
                            "新增记录",
                            lambda e: _TABS[_state["tab"]][1](page, None, refresh),
                            ACTIONS["add"],
                        ),
                        primary_button(
                            "查看记录",
                            lambda e: _goto("records", refresh),
                            ACTIONS["list"],
                        ),
                    ],
                    spacing=8,
                ),
            ),
            ft.Row(
                [
                    _summary_card("最新体重", weight_value, weight_sub),
                    _summary_card(
                        "今日摄入",
                        f"{data['today_kcal']:.0f} kcal",
                        "当天饮食合计",
                        accent=True,
                    ),
                    _summary_card(
                        "本周锻炼",
                        f"{data['exercise_minutes']} 分钟",
                        f"{data['exercise_count']} 次 · {data['exercise_kcal']:.0f} kcal",
                    ),
                    _summary_card(
                        "近 7 天睡眠",
                        f"{sleep_avg:.1f} 小时" if sleep_avg is not None else "—",
                        "日均睡眠时长",
                    ),
                ],
                spacing=12,
            ),
            _quick_record_row(page, refresh),
            _chart_grid(page, data),
        ],
        spacing=12,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )


def _records_summary(tab: str, items: list) -> str:
    if not items:
        return "暂无记录"
    if tab == "锻炼":
        return (
            f"{len(items)} 条 · 合计 {sum(item.minutes for item in items)} 分钟 · "
            f"{sum(item.calories for item in items):.0f} kcal"
        )
    if tab == "饮食":
        return f"{len(items)} 条 · 合计 {sum(item.calories for item in items):.0f} kcal"
    if tab == "体重":
        return f"{len(items)} 条 · 最新 {items[0].kg:.1f} kg"
    hours = [item.hours for item in items]
    return f"{len(items)} 条 · 平均 {sum(hours) / len(hours):.1f} 小时"


def _render_records(page: ft.Page, refresh, data: dict) -> ft.Control:
    tab = data["tab"]
    items = data["items"]
    opener = _TABS[tab][1]

    tab_row = ft.Row(
        [ft.TextButton(("● " if tab == name else "") + name,
                       on_click=lambda e, value=name: (_state.__setitem__("tab", value), refresh()))
         for name in _TABS]
        + [ft.Container(expand=True),
           ft.TextButton(("● " if data["range"] == "本周" else "") + "本周",
                         on_click=lambda e: (_state.__setitem__("range", "本周"), refresh())),
           ft.TextButton(("● " if data["range"] == "本月" else "") + "本月",
                         on_click=lambda e: (_state.__setitem__("range", "本月"), refresh()))],
        spacing=4,
    )

    return ft.Column(
        [
            section_header(
                "健康 · 记录",
                ft.Row(
                    [
                        ft.TextButton(
                            "返回汇总",
                            on_click=lambda e: _goto("summary", refresh),
                        ),
                        primary_button(
                            "新增记录",
                            lambda e: opener(page, None, refresh),
                            ACTIONS["add"],
                        ),
                    ],
                    spacing=8,
                ),
            ),
            tab_row,
            ft.Text(_records_summary(tab, items), size=11, color=th.TEXT_MUTED),
            card(
                ft.Column(
                    [_row(item, tab, page, refresh) for item in items],
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                )
                if items
                else empty("暂无记录"),
                title=f"{tab}记录（{data['range']}）",
                expand=True,
            ),
        ],
        spacing=12,
        expand=True,
    )
