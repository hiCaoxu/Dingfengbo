"""健康模块计算服务。"""

from __future__ import annotations

import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DEFAULT_HEIGHT_CM, DEFAULT_WEIGHT_KG, get_setting
from ..db.models import Food, Weight

logger = logging.getLogger(__name__)

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


def latest_weight_kg(session: Session) -> float:
    """最近一次体重记录，用于运动消耗估算；无记录时退回默认体重。"""
    row = session.scalar(
        select(Weight)
        .where(Weight.is_deleted.is_(False))
        .order_by(Weight.date.desc(), Weight.updated_at.desc())
    )
    return float(row.kg) if row is not None else DEFAULT_WEIGHT_KG


def estimate_exercise_calories(
    kind: str, minutes: int, weight_kg: float | None = None
) -> float:
    weight = weight_kg if weight_kg and weight_kg > 0 else DEFAULT_WEIGHT_KG
    return round(MET.get(kind, 5.0) * weight * (minutes / 60), 1)


def bmi(kg: float, height_cm: float | None = None) -> float | None:
    h = (height_cm or float(get_setting("height_cm", DEFAULT_HEIGHT_CM))) / 100
    if h <= 0 or kg <= 0:
        return None
    return round(kg / (h * h), 1)
