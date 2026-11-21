"""图表输出：像素尺寸固定、纵轴范围可固定且不裁数据。"""

from __future__ import annotations

import struct
import unittest

from app.services.charts import _resolve_ylim, bar_chart, line_chart


def _png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("不是 PNG")
    return struct.unpack(">II", data[16:24])


class ChartSizeTests(unittest.TestCase):
    def test_bar_chart_size_is_stable_regardless_of_label_count(self) -> None:
        """柱子数量变化时图片尺寸固定，卡片对齐与柱宽才不会抖动。"""
        few = bar_chart(["11-19", "11-20", "11-21"], [30, 0, 45], "锻炼分钟")
        many = bar_chart([f"11-{day:02d}" for day in range(8, 22)], [30] * 14, "锻炼分钟")
        self.assertEqual(_png_size(few), _png_size(many))

    def test_line_chart_size_is_stable(self) -> None:
        series = [{"name": "体重", "values": [69.5, 70.0], "color": "#2F6B4F", "fill": True}]
        full_series = [{"name": "体重", "values": [70.0] * 14, "color": "#2F6B4F", "fill": True}]
        self.assertEqual(
            _png_size(line_chart(["11-20", "11-21"], series, "体重", y_range=(50, 70))),
            _png_size(line_chart([f"11-{day:02d}" for day in range(8, 22)], full_series, "体重", y_range=(50, 70))),
        )


class BarYRangeTests(unittest.TestCase):
    def test_bar_chart_supports_fixed_y_range(self) -> None:
        """睡眠柱状图固定 5-12：尺寸稳定，且不同范围不会命中同一张缓存图。"""
        data = bar_chart(["11-20", "11-21"], [7.2, 0.0], "睡眠时长", y_range=(5, 12))
        self.assertEqual(
            _png_size(data),
            _png_size(bar_chart(["11-20"], [7.2], "睡眠时长", y_range=(5, 12))),
        )
        self.assertNotEqual(
            data,
            bar_chart(["11-20", "11-21"], [7.2, 0.0], "睡眠时长", y_range=(0, 12)),
        )


class YRangeTests(unittest.TestCase):
    def test_keeps_requested_range_inside_bounds(self) -> None:
        self.assertEqual(_resolve_ylim([60.2, 68.9], (50, 70)), (50, 70))
        self.assertEqual(_resolve_ylim([6.5, 8.0], (5, 12)), (5, 12))

    def test_expands_when_data_exceeds_range(self) -> None:
        self.assertEqual(_resolve_ylim([48.1, 72.4], (50, 70)), (48, 73))

    def test_none_means_auto(self) -> None:
        self.assertIsNone(_resolve_ylim([1.0], None))


if __name__ == "__main__":
    unittest.main()
