"""健康模块计算服务。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_setting
from ..db.models import Food

# 常见运动 MET 值，用于估算消耗：kcal ≈ MET × 体重(kg) × 小时
MET = {
    "跑步": 8.3,
    "快走": 4.3,
    "骑行": 6.8,
    "游泳": 7.0,
    "力量训练": 5.0,
    "瑜伽": 3.0,
    "跳绳": 11.8,
    "爬楼": 8.8,
}


def food_options(session: Session) -> list[Food]:
    return list(session.scalars(select(Food).order_by(Food.category, Food.name)))


def calorie_of(session: Session, food: str, grams: float) -> float:
    """kcal = 每100g可食部热量 × 实际重量(g) ÷ 100。"""
    row = session.scalar(select(Food).where(Food.name == food))
    if row is None:
        return 0.0
    return round(row.kcal_per_100g * grams / 100, 1)


def estimate_exercise_calories(kind: str, minutes: int, weight_kg: float = 65.0) -> float:
    return round(MET.get(kind, 5.0) * weight_kg * (minutes / 60), 1)


def bmi(kg: float, height_cm: float | None = None) -> float | None:
    h = (height_cm or float(get_setting("height_cm", 165))) / 100
    if h <= 0 or kg <= 0:
        return None
    return round(kg / (h * h), 1)
