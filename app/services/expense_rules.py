"""记账消费规则：商家/描述 → 分类映射，以及一句话交易的规则解析。

规则解析不依赖大模型：即使没配置 AI，也能把「今天午饭花了 32 元」变成
一条待确认的交易草稿；AI 不可用时这里是唯一兜底。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

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


def parse_amount(text: str) -> float | None:
    """提取金额：优先「数字+元/块」，其次 ¥ 前缀，最后取最大的数字。"""
    body = text or ""
    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:元|块钱|块|rmb|RMB)",
        r"[¥￥]\s*(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", body)]
    return max(numbers) if numbers else None


def parse_date(text: str, today: date | None = None) -> date:
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
        if 1 <= month <= 12 and 1 <= day <= 31:
            year = today.year if month <= today.month else today.year - 1
            try:
                return date(year, month, day)
            except ValueError:
                return today
    return today


def parse_transaction(text: str, today: date | None = None) -> dict | None:
    """把一句话解析成交易草稿；金额缺失时返回 None。"""
    amount = parse_amount(text)
    if amount is None or amount <= 0:
        return None
    body = (text or "").strip()
    category = suggest_category(body)
    kind = "income" if any(word in body for word in INCOME_WORDS) else "expense"
    if category == "收入":
        category, kind = "工资", "income"
    return {
        "kind": kind,
        "amount": round(amount, 2),
        "category": category,
        "date": parse_date(body, today).isoformat(),
        "note": body[:200],
    }
