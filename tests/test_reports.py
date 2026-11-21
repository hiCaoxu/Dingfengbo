"""回顾报告：统计口径、时间范围、Markdown 与 AI 提示词。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from app.services.reports import PERIODS, build_report, range_of, render_markdown, report_prompt

from tests.helpers import reset_db, seed


class RangeTests(unittest.TestCase):
    def test_week_starts_on_monday(self) -> None:
        start, end = range_of("week", date(2026, 11, 21))  # 周六
        self.assertEqual(start, date(2026, 11, 16))
        self.assertEqual(end, date(2026, 11, 21))

    def test_month_starts_on_first_day(self) -> None:
        start, end = range_of("month", date(2026, 11, 21))
        self.assertEqual(start, date(2026, 11, 1))

    def test_unknown_period_falls_back_to_week(self) -> None:
        report = build_report("nonsense", date(2026, 11, 21))
        self.assertIn(report["period"], PERIODS)


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()
        self.ids = seed()

    def test_todo_and_finance_sections(self) -> None:
        report = build_report("week", date.today())
        self.assertGreaterEqual(report["todo"]["completed"], 1)
        self.assertEqual(report["finance"]["expense"], 32.5)
        self.assertEqual(report["finance"]["top_categories"][0][0], "餐饮")
        self.assertEqual(report["diary"]["written"], 1)

    def test_budget_section_uses_current_month(self) -> None:
        report = build_report("month", date.today())
        self.assertEqual(report["budget"]["total"], 3000)
        self.assertEqual(report["budget"]["expense"], 32.5)
        self.assertEqual(report["budget"]["remaining"], 2967.5)
        categories = {item["category"]: item for item in report["budget"]["categories"]}
        self.assertIn("餐饮", categories)
        self.assertEqual(categories["餐饮"]["actual"], 32.5)

    def test_habit_and_entertainment_sections(self) -> None:
        report = build_report("week", date.today())
        self.assertEqual(report["habit"]["checkins"], 2)
        self.assertIn("电影测试", report["entertainment"]["watching"])

    def test_markdown_contains_core_sections(self) -> None:
        report = build_report("week", date.today())
        markdown = render_markdown(report, "本周状态不错")
        for heading in ("## AI 总结", "## 任务", "## 日记与笔记", "## 健康", "## 财务"):
            self.assertIn(heading, markdown)
        self.assertIn("本周状态不错", markdown)
        self.assertIn("32.50", markdown)

    def test_report_prompt_mentions_numbers_only(self) -> None:
        report = build_report("week", date.today())
        prompt = report_prompt(report)
        self.assertIn("不要编造数据", prompt)
        self.assertIn("完成", prompt)

    def test_year_range_covers_past(self) -> None:
        report = build_report("year", date.today())
        self.assertEqual(report["start"], date(date.today().year, 1, 1))
        self.assertGreaterEqual(report["days"], 1)


class EmptyDataTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_db()

    def test_empty_report_does_not_crash(self) -> None:
        report = build_report("week", date.today())
        self.assertEqual(report["todo"]["completed"], 0)
        self.assertEqual(report["finance"]["expense"], 0)
        markdown = render_markdown(report)
        self.assertIn("# ", markdown)

    def test_missing_data_sections_are_zero(self) -> None:
        report = build_report("month", date.today() - timedelta(days=200))
        self.assertEqual(report["health"]["exercise_count"], 0)
        self.assertEqual(report["budget"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
