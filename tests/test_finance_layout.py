"""记账首页布局：图表行 + 收支记录/账户列表竖排 + 一行三卡。"""

from __future__ import annotations

import unittest

import flet as ft

from app.ui import finance_ui


class _FakePage:
    """图表渲染走 run_thread，这里直接跳过，避免在测试里跑 matplotlib。"""

    def update(self) -> None:
        return None

    def run_thread(self, handler) -> None:  # noqa: ARG002
        return None

    def run_task(self, handler) -> None:  # noqa: ARG002
        return None


def _overview_data() -> dict:
    return {
        "summary": {"income": 100.0, "expense": 20.0, "balance": 80.0,
                    "categories": {"餐饮": 20.0}},
        "assets": 1000.0,
        "trend": [{"label": "11月", "income": 1.0, "expense": 2.0}],
        "recent_txs": [],
        "tx_max_updated": None,
        "budget": {"total_budget": 0, "expense": 0, "remaining": 0, "percent": 0},
        "budget_alerts": [],
        "fixed_total": 0,
        "upcoming": [],
    }


def _title_of(container: ft.Container) -> str:
    """卡片标题取自 card() 生成的第一行 Row 里的 Text。"""
    first = container.content.controls[0]
    return first.controls[0].value


class OverviewLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = finance_ui._render_overview(_FakePage(), lambda: None, _overview_data())

    def test_sections_order(self) -> None:
        labels = [type(control).__name__ for control in self.root.controls]
        self.assertEqual(labels, ["Row", "Row", "Row", "Row"])  # 标题 / 指标 / 图表 / 三卡

    def test_entry_cards_are_stacked_after_trend_chart(self) -> None:
        charts = self.root.controls[2]
        self.assertEqual(len(charts.controls), 3)
        self.assertEqual(_title_of(charts.controls[0]), "支出占比")
        self.assertEqual(_title_of(charts.controls[1]), "收支趋势")
        # 图表按 3:4 弹性分配宽度（窗口变宽时不留白），入口卡列占 2 份
        self.assertEqual([c.expand for c in charts.controls], [3, 4, 2])
        self.assertIsNone(charts.scroll)
        # 第三列是竖排的两张入口卡，与图表同高、各占一半
        stack = charts.controls[2]
        self.assertIsInstance(stack, ft.Column)
        self.assertEqual(len(stack.controls), 2)
        self.assertEqual(stack.height, 284)
        # 竖排卡片要横向拉伸：Column 默认 START 会让卡片缩成文字宽度，右侧留白
        self.assertEqual(stack.horizontal_alignment, ft.CrossAxisAlignment.STRETCH)
        self.assertTrue(all(control.expand == 1 for control in stack.controls))
        # 卡片正文也要撑满，图标与文字才会在整张卡里居中
        for entry_card in stack.controls:
            self.assertEqual(
                entry_card.content.horizontal_alignment,
                ft.CrossAxisAlignment.STRETCH,
                "入口卡正文需要 STRETCH，否则文字贴在左边",
            )

    def test_three_cards_share_one_row_and_equal_height(self) -> None:
        bottom = self.root.controls[3]
        self.assertEqual(len(bottom.controls), 3)
        self.assertEqual(
            [_title_of(card) for card in bottom.controls],
            ["最新收支", "本月预算", "固定支出与订阅"],
        )
        self.assertEqual(bottom.height, 210)
        heights = {card.height for card in bottom.controls}
        self.assertEqual(heights, {210})
        self.assertTrue(all(card.expand == 1 for card in bottom.controls))


if __name__ == "__main__":
    unittest.main()
