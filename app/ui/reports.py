"""回顾模块：周/月/年统计、AI 叙述与 Markdown 导出。

页面只做展示：统计在 ``services.reports``，AI 只把统计结果当输入，
导出的文件落在数据目录的 exports 下，避免依赖系统文件选择框。
"""

from __future__ import annotations

import logging
from datetime import datetime

import flet as ft

from .. import theme as th
from ..config import DATA_DIR
from ..icons import ico
from ..services.ai.client import AIConfigError, ai_configured, chat
from ..services.ai.provider import AIError
from ..services.reports import PERIODS, build_report, render_markdown, report_prompt
from .async_utils import background_section
from .state import state
from .widgets import card, empty, primary_button, snack

logger = logging.getLogger(__name__)

EXPORT_DIR = DATA_DIR / "exports"


def _metric(title: str, value: str, hint: str = "", color: str | None = None) -> ft.Control:
    return ft.Column(
        [
            ft.Text(title, size=10, color=th.TEXT_MUTED),
            ft.Text(value, size=18, color=color or th.TEXT, weight="bold"),
            ft.Text(hint, size=9, color=th.TEXT_MUTED, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ],
        spacing=2,
        tight=True,
    )


def _section_row(items: list[ft.Control], spacing: int = 18) -> ft.Row:
    return ft.Row(items, spacing=spacing, wrap=True)


def _render(page: ft.Page, refresh, report: dict) -> ft.Control:
    period = report["period"]
    narrative = state.reports.narrative.get(period, "")

    def set_period(value: str):
        state.reports.period = value
        refresh()

    todo, diary, note, health, finance = (
        report["todo"], report["diary"], report["note"], report["health"], report["finance"],
    )
    fun, habit, budget = report["entertainment"], report["habit"], report["budget"]

    period_tabs = ft.Row(
        [
            ft.Button(
                PERIODS[key],
                bgcolor=th.PRIMARY if period == key else th.PRIMARY_LIGHT,
                color=th.ON_PRIMARY if period == key else th.TEXT_MUTED,
                elevation=0,
                on_click=lambda e, k=key: set_period(k),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=8),
                    padding=ft.Padding.symmetric(vertical=8, horizontal=16),
                ),
            )
            for key in PERIODS
        ],
        spacing=8,
    )

    todo_card = card(
        ft.Column(
            [
                _section_row(
                    [
                        _metric("完成", f"{todo['completed']} 项", "本期勾选完成"),
                        _metric("新建", f"{todo['created']} 项", "本期新增任务"),
                        _metric("未完成", f"{todo['pending']} 项", f"逾期 {todo['overdue']} 项",
                                th.ACCENT if todo["overdue"] else None),
                        _metric("完成率", f"{todo['completion_rate']}%", "完成 / 全部"),
                    ]
                ),
                ft.ProgressBar(value=min(1.0, todo["completion_rate"] / 100),
                               color=th.PRIMARY, bgcolor=th.DIVIDER),
                ft.Text(
                    "高频标签：" + "、".join(f"{tag}({count})" for tag, count in todo["top_tags"])
                    if todo["top_tags"] else "本期没有未完成标签",
                    size=10, color=th.TEXT_MUTED,
                ),
            ],
            spacing=10,
        ),
        title="任务",
        expand=2,
    )

    diary_card = card(
        ft.Column(
            [
                _metric("日记", f"{diary['count']} 篇",
                        f"有内容 {diary['written']} 篇 · 平均 {diary['avg_length']} 字"),
                _metric("笔记", f"{note['total']} 篇",
                        f"新建 {note['created']} · 更新 {note['updated']}"),
                ft.Text(
                    "笔记标签：" + "、".join(f"{tag}({count})" for tag, count in note["top_tags"])
                    if note["top_tags"] else "笔记暂无标签",
                    size=10, color=th.TEXT_MUTED,
                ),
            ],
            spacing=10,
        ),
        title="记录",
        expand=1,
    )

    health_card = card(
        ft.Column(
            [
                _section_row(
                    [
                        _metric("锻炼", f"{health['exercise_count']} 次",
                                f"{health['exercise_minutes']} 分钟 · {health['exercise_calories']} 千卡"),
                        _metric("睡眠", f"{health['sleep_hours']} 小时",
                                f"{health['sleep_nights']} 晚 · 质量 {health['sleep_quality']}/5"),
                        _metric(
                            "体重",
                            f"{health['weight_last']:g} kg" if health["weight_last"] else "—",
                            (
                                f"{health['weight_delta']:+.1f} kg"
                                if health["weight_delta"] is not None
                                else "数据不足"
                            ),
                            th.PRIMARY if (health["weight_delta"] or 0) < 0 else None,
                        ),
                    ]
                )
            ],
            spacing=6,
        ),
        title="健康",
        expand=1,
    )

    finance_card = card(
        ft.Column(
            [
                _section_row(
                    [
                        _metric("收入", f"¥{finance['income']:,.2f}", "", th.PRIMARY),
                        _metric("支出", f"¥{finance['expense']:,.2f}", "", th.ACCENT),
                        _metric("结余", f"¥{finance['balance']:,.2f}", ""),
                        _metric("日均支出", f"¥{finance['daily_expense']:,.2f}", ""),
                    ]
                ),
                ft.Text(
                    "主要支出：" + "、".join(
                        f"{name} ¥{value:,.0f}" for name, value in finance["top_categories"]
                    )
                    if finance["top_categories"] else "本期没有支出记录",
                    size=10, color=th.TEXT_MUTED,
                ),
                ft.Text(
                    f"预算：¥{budget['total_budget']:,.0f}，已用 ¥{budget['expense']:,.0f}，"
                    f"剩余 ¥{budget['remaining']:,.0f}" if budget.get("total_budget")
                    else "还没有设置预算（记账页可设置）",
                    size=10,
                    color=th.DANGER if budget.get("remaining", 0) < 0 else th.TEXT_MUTED,
                ),
            ],
            spacing=10,
        ),
        title="财务",
        expand=2,
    )

    fun_lines = list(fun["finished"]) + [f"在看：{title}" for title in fun["watching"]]
    fun_card = card(
        ft.Column(
            [
                ft.Text(
                    f"本期看完 {len(fun['finished'])} 条，在看 {len(fun['watching'])} 条",
                    size=12, color=th.TEXT,
                ),
                *[ft.Text(line, size=10, color=th.TEXT_MUTED) for line in fun_lines[:6]],
                empty("本期没有娱乐记录") if not fun_lines else ft.Container(),
            ],
            spacing=6,
        ),
        title="阅读与娱乐",
        expand=1,
    )

    habit_card = card(
        ft.Column(
            [
                ft.Text(f"打卡 {habit['checkins']} 次", size=12, color=th.TEXT),
                *[
                    ft.Text(f"{name}：{count} 次", size=10, color=th.TEXT_MUTED)
                    for name, count in habit["per_habit"][:6]
                ],
                empty("本期没有习惯打卡") if not habit["per_habit"] else ft.Container(),
            ],
            spacing=6,
        ),
        title="习惯",
        expand=1,
    )

    narrative_text = ft.Text(
        narrative or "还没有 AI 总结。统计只在本地计算，点「生成 AI 总结」才会把数字与标题发给模型。",
        size=11,
        color=th.TEXT if narrative else th.TEXT_MUTED,
        selectable=True,
    )

    def export_markdown(_) -> None:
        try:
            EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = EXPORT_DIR / f"回顾-{PERIODS[period]}-{stamp}.md"
            path.write_text(render_markdown(report, narrative), encoding="utf-8")
        except OSError as exc:
            logger.exception("导出回顾失败")
            snack(page, f"导出失败：{exc}", th.ACCENT)
            return
        snack(page, f"已导出：{path}", th.PRIMARY)

    def generate(_) -> None:
        if not ai_configured():
            snack(page, "请先在设置里配置 AI", th.ACCENT)
            return
        from .ai_tasks_ui import ensure_consent

        def go() -> None:
            narrative_text.value = "正在生成…"
            narrative_text.color = th.TEXT_MUTED
            page.update()

            def worker() -> None:
                try:
                    text = chat(
                        [
                            {"role": "system", "content": "你是个人工作台的数据助手，输出简体中文。"},
                            {"role": "user", "content": report_prompt(report)},
                        ]
                    )
                except (AIConfigError, AIError) as exc:
                    text = f"⚠️ {exc}"
                except Exception:
                    logger.exception("生成回顾叙述失败")
                    text = "⚠️ 生成失败，详见日志"

                async def apply() -> None:
                    if not text.startswith("⚠️"):
                        state.reports.narrative[period] = text
                    narrative_text.value = text
                    narrative_text.color = th.TEXT
                    page.update()

                try:
                    page.run_task(apply)
                except Exception:
                    logger.debug("回顾叙述回 UI 失败", exc_info=True)

            page.run_thread(worker)

        if not ensure_consent(page, ["todos", "diaries", "transactions", "entertainments"], go):
            return
        go()

    ai_card = card(
        ft.Column(
            [
                narrative_text,
                ft.Row(
                    [
                        primary_button("生成 AI 总结", generate, "AUTO_AWESOME_OUTLINED"),
                        ft.TextButton("导出 Markdown", on_click=export_markdown),
                    ],
                    spacing=8,
                ),
            ],
            spacing=10,
        ),
        title=f"{report['period_label']} AI 总结",
    )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Row(
                        [
                            ft.Icon(ico("INSIGHTS_OUTLINED"), size=20, color=th.PRIMARY),
                            ft.Text("回顾报告", size=15, color=th.TEXT, weight="bold"),
                            ft.Text(
                                f"{report['start'].isoformat()} ~ {report['end'].isoformat()}",
                                size=11, color=th.TEXT_MUTED,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Container(expand=True),
                    period_tabs,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ai_card,
            ft.Row([todo_card, diary_card], spacing=12),
            ft.Row([health_card, finance_card], spacing=12),
            ft.Row([fun_card, habit_card], spacing=12),
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )


def build(page: ft.Page, refresh) -> ft.Control:
    period = state.reports.period
    return background_section(
        page,
        lambda: build_report(period),
        lambda report: _render(page, refresh, report),
        placeholder="正在统计…",
    )
