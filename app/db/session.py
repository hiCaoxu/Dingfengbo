"""本地 SQLite 会话与建库/种子数据。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import DB_PATH
from .migrations import initialize_schema
from .models import Food

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, future=True
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# 精简食物库：数值为每 100g 可食部热量(kcal)，参考《中国食物成分表（第 6 版）》近似值。
FOOD_SEED: list[tuple[str, str, float]] = [
    ("米饭", "主食", 116), ("白粥", "主食", 46), ("馒头", "主食", 223),
    ("面条(煮)", "主食", 109), ("全麦面包", "主食", 246), ("小米粥", "主食", 46),
    ("燕麦片", "主食", 367), ("玉米", "主食", 112), ("红薯", "主食", 86),
    ("土豆", "主食", 77), ("饺子", "主食", 240), ("包子", "主食", 227),
    ("西兰花", "蔬菜", 34), ("菠菜", "蔬菜", 24), ("白菜", "蔬菜", 20),
    ("生菜", "蔬菜", 16), ("黄瓜", "蔬菜", 16), ("番茄", "蔬菜", 20),
    ("胡萝卜", "蔬菜", 39), ("茄子", "蔬菜", 23), ("青椒", "蔬菜", 22),
    ("蘑菇", "蔬菜", 20), ("冬瓜", "蔬菜", 11), ("豆角", "蔬菜", 30),
    ("苹果", "水果", 52), ("香蕉", "水果", 89), ("橙子", "水果", 47),
    ("梨", "水果", 51), ("葡萄", "水果", 45), ("西瓜", "水果", 30),
    ("草莓", "水果", 32), ("蓝莓", "水果", 57), ("猕猴桃", "水果", 61),
    ("鸡胸肉", "肉类", 133), ("瘦猪肉", "肉类", 143), ("牛肉(瘦)", "肉类", 106),
    ("羊肉(瘦)", "肉类", 118), ("三文鱼", "肉类", 139), ("鲈鱼", "肉类", 105),
    ("虾仁", "肉类", 48), ("鸡蛋", "蛋奶", 144), ("鸭蛋", "蛋奶", 180),
    ("牛奶", "蛋奶", 54), ("酸奶", "蛋奶", 72), ("奶酪", "蛋奶", 328),
    ("豆腐", "豆制品", 81), ("豆浆", "豆制品", 16), ("腐竹", "豆制品", 461),
    ("黄豆", "豆制品", 390), ("花生", "坚果", 574), ("核桃", "坚果", 646),
    ("杏仁", "坚果", 578), ("腰果", "坚果", 559), ("芝麻", "坚果", 536),
    ("白开水", "饮品", 0), ("绿茶", "饮品", 1), ("黑咖啡", "饮品", 2),
    ("可乐", "饮品", 43), ("橙汁", "饮品", 45), ("啤酒", "饮品", 32),
]


def init_db() -> None:
    initialize_schema(engine)
    with SessionLocal() as session:
        count = session.scalar(select(Food.id).limit(1))
        if count is None:
            session.add_all(
                Food(name=n, category=c, kcal_per_100g=k) for n, c, k in FOOD_SEED
            )
            session.commit()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
