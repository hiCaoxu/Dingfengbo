"""记账消费规则：商家/描述 → 分类映射，以及一句话交易的规则解析。

规则解析不依赖大模型：即使没配置 AI，也能把「今天午饭花了 32 元」变成
一条待确认的交易草稿；AI 不可用时这里是唯一兜底。
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# 关键字 → 分类（按顺序匹配，越具体的规则放前面）
CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("地铁", "公交", "打车", "滴滴", "高铁", "火车", "机票", "停车", "加油", "过路费"), "交通"),
    (("房租", "物业", "水电", "燃气", "宽带", "话费", "网费"), "居住"),
    (("咖啡", "奶茶", "外卖", "午饭", "晚饭", "早餐", "夜宵", "餐厅", "火锅", "食堂", "饭"), "餐饮"),
    (("超市", "便利店", "日用品", "纸巾", "洗发", "购物", "淘宝", "京东", "拼多多"), "购物"),
    (("电影", "演出", "游戏", "会员", "视频", "音乐", "剧本杀"), "娱乐"),
    (("医院", "药店", "体检", "挂号", "药"), "医疗"),
    (("书", "课程", "学费", "培训", "考试"), "教育"),
    (("工资", "薪", "奖金", "报销", "分红", "利息", "红包"), "收入"),
]

INCOME_WORDS = ("工资", "薪", "奖金", "报销", "收到", "收入", "分红", "利息", "退款")


def suggest_category(text: str) -> str:
    """按关键字给出分类建议；无法判断时返回「其他」。"""
    body = text or ""
    for keywords, category in CATEGORY_RULES:
        if any(keyword in body for keyword in keywords):
            return category
    return "其他"


_MULTIPLIERS = {"万": 10_000, "千": 1_000}
# ASCII 连字符 + Unicode 减号（U+2212）。用转义写，源码保持纯 ASCII：字体子集
# 是按源码里的可见字符生成的，没必要为它额外扩一套字形。
_MINUS = "-\u2212"


def _to_amount(number: str, sign: str | None, unit: str | None) -> float | None:
    try:
        value = float(number)
    except ValueError as exc:
        logger.debug("_to_amount：忽略 %s", type(exc).__name__)
        return None
    value *= _MULTIPLIERS.get(unit or "", 1)
    return -value if sign else value


def parse_amount(text: str) -> float | None:
    """提取金额：口语「N块M」→ 单位/货币符号 → 万/千倍数 → 最大数字兜底。

    兜底那一步必须排除年份/日期/次数：「2025年收入10万」旧实现会取到 2025（年份），
    记成 2025 元；负号与「10块5」的小数位也一并按口语习惯处理。
    """
    body = text or ""
    # 1) 「10块5」「10块5毛」：口语里 块 后面跟一位数字表示角
    match = re.search(r"(\d+)\s*块\s*(\d)\s*[毛角]?", body)
    if match:
        return float(match.group(1)) + float(match.group(2)) / 10

    # 2) 带货币符号 / 金额单位（允许 万元、千块 这类倍数）
    for pattern in (
        rf"(?:¥|￥)\s*([{_MINUS}])?\s*(\d+(?:\.\d+)?)\s*(万|千)?",
        rf"([{_MINUS}])?\s*(\d+(?:\.\d+)?)\s*(万|千)?\s*(?:元|块钱|块|rmb|RMB)",
    ):
        match = re.search(pattern, body)
        if match:
            amount = _to_amount(match.group(2), match.group(1), match.group(3))
            if amount is not None:
                return amount

    # 3) 光有倍数：「收入10万」「3千」
    match = re.search(rf"([{_MINUS}])?\s*(\d+(?:\.\d+)?)\s*(万|千)(?![年月日])", body)
    if match:
        amount = _to_amount(match.group(2), match.group(1), match.group(3))
        if amount is not None:
            return amount

    # 4) 兜底：取最大的数字，但排除年份、日期、次数、折扣这类非金额数字。
    #    末尾的 (?![\d.]) 防止 `\d+` 回溯到长数字的一部分（"2025年" 曾回退成 202）。
    numbers = [
        float(found)
        for found in re.findall(
            r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])"
            r"(?!\s*(?:年|月|日|号|天|次|岁|点|分|个|折))",
            body,
        )
    ]
    return max(numbers) if numbers else None


def parse_date(text: str, today: date | None = None) -> date:
    """从一句话里取日期；识别不出时返回今天。

    只写了「M 月 D 日」没写年份时，取**距今天最近**的那一年：旧实现固定落在
    「今年或去年」，于是 10 月说「12月25日」会被记成去年，12 月说「1月5日」会被
    记成今年已过去的那一天。2 月 29 日在平年会夹到 2/28，而不是静默返回今天。
    """
    today = today or date.today()
    body = text or ""
    if "前天" in body:
        return today - timedelta(days=2)
    if "昨天" in body:
        return today - timedelta(days=1)
    if "今天" in body:
        return today
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", body)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return today
        candidates: list[date] = []
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                # 平年 2/29 这类不存在的日期：夹到当月最后一天
                last_day = calendar.monthrange(year, month)[1]
                candidates.append(date(year, month, min(day, last_day)))
        return min(candidates, key=lambda item: abs((item - today).days))
    return today


def parse_transaction(text: str, today: date | None = None) -> dict | None:
    """把一句话解析成交易草稿；金额缺失或为 0 时返回 None。

    负号按「退款/收入」理解：金额取绝对值、类型记为收入——交易金额本身恒为正，
    方向由 kind 表达。
    """
    parsed = parse_amount(text)
    if parsed is None or parsed == 0:
        return None
    body = (text or "").strip()
    category = suggest_category(body)
    kind = (
        "income"
        if parsed < 0 or any(word in body for word in INCOME_WORDS)
        else "expense"
    )
    if category == "收入":
        category, kind = "工资", "income"
    return {
        "kind": kind,
        "amount": round(abs(parsed), 2),
        "category": category,
        "date": parse_date(body, today).isoformat(),
        "note": body[:200],
    }
