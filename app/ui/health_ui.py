"""健康：锻炼/饮食/体重/睡眠记录与周月图表。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import flet as ft
from sqlalchemy import select

from ..db.models import Diet, Exercise, Sleep, Weight
from ..db.repo import soft_delete
from ..db.session import session_scope
from ..icons import ACTIONS, ico
from ..services.charts import bar_chart, line_chart
from ..services.health import bmi
from ..services.sync import mark_pending_write
from ..theme import ACCENT, DIVIDER, PRIMARY, TEXT, TEXT_MUTED
from .forms import open_diet_dialog, open_exercise_dialog, open_sleep_dialog, open_weight_dialog
from .widgets import card, confirm, divider, empty, icon_button, primary_button, section_header

_state = {"tab": "锻炼", "range": "本周"}
_TABS = {
    "锻炼": (Exercise, open_exercise_dialog),
    "饮食": (Diet, open_diet_dialog),
    "体重": (Weight, open_weight_dialog),
    "睡眠": (Sleep, open_sleep_dialog),
}


def _chart_placeholder() -> ft.Control:
    return ft.Container(
        content=ft.Text("加载中…", size=11, color=TEXT_MUTED),
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
    def action():
        with session_scope() as session:
            obj = session.get(model, item_id)
            if obj:
                soft_delete(session, obj)
        mark_pending_write()
        refresh()

    confirm(page, "确定删除该记录？", action)


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
                ft.Column([ft.Text(primary, size=12, color=TEXT, weight="bold"),
                           ft.Text(secondary, size=10, color=TEXT_MUTED)], spacing=3, expand=True),
                icon_button(ACTIONS["edit"], lambda e, i=item: _TABS[tab][1](page, i, refresh), "编辑"),
                icon_button(ACTIONS["delete"], lambda e, i=item.id: _delete(_TABS[tab][0], i, page, refresh), "删除"),
            ],
            spacing=8,
        ),
        border=ft.Border.all(1, DIVIDER),
        border_radius=10,
        padding=ft.Padding.symmetric(vertical=9, horizontal=12),
    )


def build(page: ft.Page, refresh) -> ft.Control:
    start, end = _date_range()
    tab = _state["tab"]
    model, opener = _TABS[tab]
    with session_scope() as session:
        items = list(
            session.scalars(
                select(model).where(
                    model.is_deleted.is_(False), model.date >= start, model.date < end
                ).order_by(model.date.desc())
            )
        )
        latest_weight = session.scalar(select(Weight).where(Weight.is_deleted.is_(False)).order_by(Weight.date.desc()))
        today_kcal = sum(d.calories for d in session.scalars(
            select(Diet).where(Diet.is_deleted.is_(False), Diet.date == date.today())
        ))
        chart_models = (Weight, Sleep, Exercise, Diet)
        weights, sleeps, exercises, diets = [
            list(session.scalars(
                select(chart_model).where(
                    chart_model.is_deleted.is_(False),
                    chart_model.date >= start,
                    chart_model.date < end,
                ).order_by(chart_model.date)
            ))
            for chart_model in chart_models
        ]

    def labels_of(rows):
        return [r.date.strftime("%m-%d") for r in rows[-14:]]

    def daily_totals(rows, attribute: str):
        totals = defaultdict(float)
        for row in rows:
            totals[row.date] += getattr(row, attribute)
        days = sorted(totals)[-14:]
        return [day.strftime("%m-%d") for day in days], [totals[day] for day in days]

    exercise_labels, exercise_minutes = daily_totals(exercises, "minutes")
    diet_labels, diet_calories = daily_totals(diets, "calories")

    weight_slot = _chart_slot(420, 170)
    sleep_slot = _chart_slot(420, 170)
    exercise_slot = _chart_slot(420, 170)
    diet_slot = _chart_slot(420, 170)

    charts = ft.Column(
        [
            card(weight_slot, title="体重趋势"),
            card(sleep_slot, title="睡眠时长"),
            card(exercise_slot, title="锻炼时长"),
            card(diet_slot, title="饮食热量"),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )

    def render_charts() -> None:
        weight_slot.content = ft.Image(
            src=line_chart(
                labels_of(weights),
                [{"name": "体重 kg", "values": [w.kg for w in weights[-14:]], "color": PRIMARY, "fill": True}],
                "体重趋势",
            ),
            width=420, height=170, fit=ft.BoxFit.CONTAIN,
        )
        sleep_slot.content = ft.Image(
            src=line_chart(
                labels_of(sleeps),
                [{"name": "睡眠小时", "values": [s.hours for s in sleeps[-14:]], "color": ACCENT, "fill": True}],
                "睡眠时长",
            ),
            width=420, height=170, fit=ft.BoxFit.CONTAIN,
        )
        exercise_slot.content = ft.Image(
            src=bar_chart(exercise_labels, exercise_minutes, "锻炼分钟", PRIMARY),
            width=420, height=170, fit=ft.BoxFit.CONTAIN,
        )
        diet_slot.content = ft.Image(
            src=bar_chart(diet_labels, diet_calories, "饮食热量", ACCENT),
            width=420, height=170, fit=ft.BoxFit.CONTAIN,
        )
        try:
            page.update()
        except Exception:
            pass

    if page is not None:
        page.run_thread(render_charts)

    tab_row = ft.Row(
        [ft.TextButton(("● " if _state["tab"] == t else "") + t,
                       on_click=lambda e, name=t: (_state.__setitem__("tab", name), refresh()))
         for t in _TABS]
        + [ft.Container(expand=True),
           ft.TextButton(("● " if _state["range"] == "本周" else "") + "本周",
                         on_click=lambda e: (_state.__setitem__("range", "本周"), refresh())),
           ft.TextButton(("● " if _state["range"] == "本月" else "") + "本月",
                         on_click=lambda e: (_state.__setitem__("range", "本月"), refresh()))],
        spacing=4,
    )

    latest_text = f"{latest_weight.kg:.1f} kg · BMI {bmi(latest_weight.kg)}" if latest_weight else "暂无体重记录"
    return ft.Column(
        [
            section_header("健康", ft.Row(
                [primary_button("新增记录", lambda e: opener(page, None, refresh), ACTIONS["add"])], spacing=6)),
            ft.Row(
                [
                    card(ft.Text(latest_text, size=20, color=TEXT, weight="bold"), title="最新体重"),
                    card(ft.Text(f"{today_kcal:.0f} kcal", size=20, color=ACCENT, weight="bold"), title="今日摄入"),
                ],
                spacing=12,
            ),
            tab_row,
            ft.Row(
                [
                    card(
                        ft.Column([_row(i, tab, page, refresh) for i in items], spacing=8, scroll=ft.ScrollMode.AUTO)
                        if items else empty("暂无记录"),
                        title=f"{tab}记录",
                        expand=1,
                    ),
                    ft.Container(content=charts, expand=1),
                ],
                spacing=14,
                expand=True,
            ),
        ],
        spacing=12,
        expand=True,
    )
