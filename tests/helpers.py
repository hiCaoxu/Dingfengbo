"""测试共用：临时库重置与一批覆盖各模块的样本数据。"""

from __future__ import annotations

from datetime import date, timedelta

from app.db.models import (
    Account,
    Budget,
    Diary,
    Entertainment,
    Habit,
    HabitLog,
    Note,
    Todo,
    Transaction,
    utcnow,
)
from app.db.repo import create
from app.db.session import engine, init_db, session_scope
from app.db.models import Base


def reset_db() -> None:
    """清空临时库并重建结构，保证用例之间互不影响。"""
    Base.metadata.drop_all(engine)
    init_db()


def seed() -> dict[str, str]:
    """写入一组样本数据，返回关键记录 ID。"""
    today = date.today()
    with session_scope() as session:
        account = create(
            session, Account, name="招商银行", kind="银行卡", initial_balance=10000
        )
        todo = create(
            session, Todo, title="写周报", due_date=today, priority="high",
            tags='["工作"]', note="整理本周进展",
        )
        create(
            session, Todo, title="体检预约", due_date=today - timedelta(days=2),
            priority="medium", tags='["健康"]',
        )
        create(
            session, Todo, title="已完成的整理", due_date=today, completed=True,
            completed_at=utcnow(), tags='["工作"]',
        )
        note = create(
            session, Note, title="AI 笔记", content="关于大模型的思考", tags='["AI","工作"]'
        )
        create(session, Diary, date=today, title=f"{today} 日记", content="今天状态不错")
        tx = create(
            session, Transaction, kind="expense", amount=32.5, category="餐饮",
            account_id=account.id, date=today, note="午饭",
        )
        create(session, Budget, month=today.isoformat()[:7], category="", amount=3000)
        create(session, Budget, month=today.isoformat()[:7], category="餐饮", amount=1000)
        create(
            session, Entertainment, title="电影测试", kind="电影", status="在看", note="科幻"
        )
        habit = create(session, Habit, name="阅读", target_per_week=7, target_minutes=30)
        create(session, HabitLog, habit_id=habit.id, date=today, done=True)
        create(session, HabitLog, habit_id=habit.id, date=today - timedelta(days=1), done=True)
        return {
            "account_id": account.id,
            "todo_id": todo.id,
            "note_id": note.id,
            "transaction_id": tx.id,
            "habit_id": habit.id,
        }
