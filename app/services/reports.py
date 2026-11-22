"""周/月/年回顾：本地统计 + Markdown 渲染 + AI 叙述提示词。

统计一律用 SQL 聚合（SUM/COUNT/GROUP BY），只在需要处理 UTC→本地日期的地方
才把行取回 Python；报告本身不依赖 AI，断网也能看。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import (
    Budget,
    Diary,
    Diet,
    Entertainment,
    Exercise,
    Habit,
    HabitLog,
    Note,
    Sleep,
    Todo,
    Transaction,
    Weight,
)
from ..db.session import session_scope
from .dates import local_date_from_utc
from .finance import money
from .money import ZERO, from_cents, quantize, to_float, total

PERIODS = {"week": "本周", "month": "本月", "year": "本年"}


def range_of(period: str, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    if period == "month":
        return today.replace(day=1), today
    if period == "year":
        return date(today.year, 1, 1), today
    start = today - timedelta(days=today.weekday())
    return start, today


def _days(start: date, end: date) -> int:
    return max(1, (end - start).days + 1)


def _tags_of(raw: str | None) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return [str(v).strip() for v in values if str(v).strip()] if isinstance(values, list) else []


def _todo_section(session: Session, start: date, end: date) -> dict[str, Any]:
    # 只要统计用得上的列：任务正文（content）在报告里一个字都不显示，
    # select(Todo) 却会把它整行物化成 ORM 对象。
    rows = session.execute(
        select(
            Todo.title,
            Todo.completed,
            Todo.completed_at,
            Todo.created_at,
            Todo.due_date,
            Todo.tags,
        ).where(Todo.is_deleted.is_(False), Todo.is_template.is_(False))
    ).all()
    completed = 0
    created = 0
    tags: Counter[str] = Counter()
    completed_titles: list[str] = []
    for item in rows:
        if item.completed_at:
            day = local_date_from_utc(item.completed_at)
            if start <= day <= end:
                completed += 1
                completed_titles.append(item.title)
        if item.created_at and start <= local_date_from_utc(item.created_at) <= end:
            created += 1
        if not item.completed:
            tags.update(_tags_of(item.tags))
    today = date.today()
    overdue = sum(
        1 for item in rows if not item.completed and item.due_date and item.due_date < today
    )
    pending = sum(1 for item in rows if not item.completed)
    # 本期「应完成」= 本期完成的 + 到期日落在本期内但尚未完成的。
    # 用全库未完成数当分母，会把"积压 100 项、本周完成 5 项"算成 5%，完全失真。
    due_in_period_pending = sum(
        1
        for item in rows
        if not item.completed and item.due_date and start <= item.due_date <= end
    )
    denominator = completed + due_in_period_pending
    return {
        "completed": completed,
        "created": created,
        "pending": pending,
        "overdue": overdue,
        "due_in_period": denominator,
        "completion_rate": round(completed / denominator * 100) if denominator else 0,
        "top_tags": tags.most_common(5),
        "completed_titles": completed_titles[:10],
    }


def _diary_section(session: Session, start: date, end: date) -> dict[str, Any]:
    # 正文仍然要（算字数、判断是否算「写了」），但不拉 tags / 图片等其他列。
    rows = session.execute(
        select(Diary.content, Diary.title).where(
            Diary.is_deleted.is_(False), Diary.date.between(start, end)
        )
    ).all()
    written = [item for item in rows if (item.content or "").strip()]
    lengths = [len((item.content or "").strip()) for item in written]
    return {
        "count": len(rows),
        "written": len(written),
        "days": _days(start, end),
        "avg_length": round(sum(lengths) / len(lengths)) if lengths else 0,
        "titles": [item.title for item in written][:10],
    }


def _note_section(session: Session, start: date, end: date) -> dict[str, Any]:
    rows = session.execute(
        select(Note.created_at, Note.updated_at, Note.tags).where(Note.is_deleted.is_(False))
    ).all()
    created = 0
    updated = 0
    tags: Counter[str] = Counter()
    for item in rows:
        if item.created_at and start <= local_date_from_utc(item.created_at) <= end:
            created += 1
        if item.updated_at and start <= local_date_from_utc(item.updated_at) <= end:
            updated += 1
        tags.update(_tags_of(item.tags))
    return {
        "total": len(rows),
        "created": created,
        "updated": updated,
        "top_tags": tags.most_common(5),
    }


def _health_section(session: Session, start: date, end: date) -> dict[str, Any]:
    exercises = session.execute(
        select(Exercise.kind, Exercise.minutes, Exercise.calories).where(
            Exercise.is_deleted.is_(False), Exercise.date.between(start, end)
        )
    ).all()
    diets = session.execute(
        select(Diet.calories, Diet.date).where(
            Diet.is_deleted.is_(False), Diet.date.between(start, end)
        )
    ).all()
    weights = session.execute(
        select(Weight.kg, Weight.date)
        .where(Weight.is_deleted.is_(False), Weight.date.between(start, end))
        .order_by(Weight.date)
    ).all()
    sleeps = session.execute(
        select(Sleep.hours, Sleep.quality).where(
            Sleep.is_deleted.is_(False), Sleep.date.between(start, end)
        )
    ).all()
    by_kind: Counter[str] = Counter(item.kind for item in exercises)
    weight_delta = (
        round(float(weights[-1].kg) - float(weights[0].kg), 2) if len(weights) >= 2 else None
    )
    return {
        "exercise_count": len(exercises),
        "exercise_minutes": round(sum(float(item.minutes or 0) for item in exercises)),
        "exercise_calories": round(sum(float(item.calories or 0) for item in exercises)),
        "exercise_kinds": by_kind.most_common(5),
        "diet_calories": round(sum(float(item.calories or 0) for item in diets)),
        "diet_days": len({item.date for item in diets}),
        "weight_first": round(float(weights[0].kg), 1) if weights else None,
        "weight_last": round(float(weights[-1].kg), 1) if weights else None,
        "weight_delta": weight_delta,
        "sleep_nights": len(sleeps),
        "sleep_hours": round(sum(float(item.hours or 0) for item in sleeps) / len(sleeps), 1)
        if sleeps
        else 0,
        "sleep_quality": round(
            sum(int(item.quality or 0) for item in sleeps) / len(sleeps), 1
        )
        if sleeps
        else 0,
    }


def _finance_section(session: Session, start: date, end: date) -> dict[str, Any]:
    rows = session.execute(
        select(Transaction.kind, Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .group_by(Transaction.kind, Transaction.category)
    )
    income = ZERO
    expense = ZERO
    categories: dict[str, Decimal] = defaultdict(Decimal)
    for kind, category, row_total in rows:
        amount = quantize(row_total)
        if kind == "income":
            income += amount
        elif kind == "expense":
            expense += amount
            categories[category or "其他"] += amount
    top = sorted(categories.items(), key=lambda item: -item[1])[:5]
    days = _days(start, end)
    return {
        "income": to_float(income),
        "expense": to_float(expense),
        "balance": to_float(income - expense),
        "daily_expense": to_float(expense / days),
        "top_categories": [(name, to_float(value)) for name, value in top],
    }


def _fun_section(session: Session, start: date, end: date) -> dict[str, Any]:
    rows = session.execute(
        select(
            Entertainment.title, Entertainment.kind,
            Entertainment.status, Entertainment.updated_at,
        ).where(Entertainment.is_deleted.is_(False))
    ).all()
    finished: list[str] = []
    watching: list[str] = []
    for item in rows:
        day = local_date_from_utc(item.updated_at) if item.updated_at else None
        if item.status == "看完" and day and start <= day <= end:
            finished.append(f"{item.title}（{item.kind}）")
        if item.status == "在看":
            watching.append(item.title)
    return {"finished": finished, "watching": watching[:8], "total": len(rows)}


def _habit_section(session: Session, start: date, end: date) -> dict[str, Any]:
    habits = {
        row.id: row.name
        for row in session.execute(
            select(Habit.id, Habit.name).where(Habit.is_deleted.is_(False))
        )
    }
    logs = session.execute(
        select(HabitLog.habit_id).where(
            HabitLog.is_deleted.is_(False),
            HabitLog.done.is_(True),
            HabitLog.date.between(start, end),
        )
    ).all()
    per_habit: Counter[str] = Counter()
    for row in logs:
        name = habits.get(row.habit_id)
        if name:
            per_habit[name] += 1
    return {"checkins": len(logs), "per_habit": per_habit.most_common(8)}


def _budget_section(session: Session, start: date, end: date) -> dict[str, Any]:
    month = f"{end.year:04d}-{end.month:02d}"
    # 与「记账 → 预算」同一口径：长期预算（month=""）也参与，月度条目覆盖长期条目。
    # 只查 month == 本月 会让"只设了长期预算"的用户在报告里永远看到 0。
    budgets = list(
        session.scalars(
            select(Budget).where(
                Budget.is_deleted.is_(False),
                (Budget.month == month) | (Budget.month == ""),
            )
        )
    )
    if not budgets:
        return {"month": month, "total": 0.0, "expense": 0.0, "remaining": 0.0, "categories": []}
    spent = session.execute(
        select(Transaction.category, func.sum(Transaction.amount))
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.kind == "expense",
            Transaction.date >= date(end.year, end.month, 1),
            Transaction.date <= end,
        )
        .group_by(Transaction.category)
    )
    actual = {str(category or "其他"): quantize(value) for category, value in spent}
    monthly: dict[str, Decimal] = {}
    standing: dict[str, Decimal] = {}
    total_monthly = ZERO
    for item in budgets:
        amount = quantize(item.amount)
        if item.category:
            bucket = monthly if item.month else standing
            bucket[item.category] = bucket.get(item.category, ZERO) + amount
        elif item.month:
            total_monthly += amount
        # 无 month 的「长期总预算」不计入本月合计：拿长期额度去减本月支出，
        # 「剩余」会严重失真（报告里显示成一个大负数或大正数）。
    category_budgets = {**standing, **monthly}
    total_budget = total_monthly
    categories = [
        {
            "category": category,
            "budget": to_float(amount),
            "actual": to_float(actual.get(category, Decimal(0))),
        }
        for category, amount in sorted(category_budgets.items(), key=lambda kv: -kv[1])
    ]
    expense = total(actual.values())
    return {
        "month": month,
        "total": to_float(total_budget),
        "expense": to_float(expense),
        "remaining": to_float(total_budget - expense) if total_budget else 0.0,
        "categories": categories,
    }


def build_report(period: str = "week", today: date | None = None) -> dict[str, Any]:
    """汇总报告数据；period 取 week/month/year。"""
    period = period if period in PERIODS else "week"
    today = today or date.today()
    start, end = range_of(period, today)
    with session_scope() as session:
        report = {
            "period": period,
            "period_label": PERIODS[period],
            "start": start,
            "end": end,
            "days": _days(start, end),
            "todo": _todo_section(session, start, end),
            "diary": _diary_section(session, start, end),
            "note": _note_section(session, start, end),
            "health": _health_section(session, start, end),
            "finance": _finance_section(session, start, end),
            "entertainment": _fun_section(session, start, end),
            "habit": _habit_section(session, start, end),
            "budget": _budget_section(session, start, end),
        }
    return report


def render_markdown(report: dict[str, Any], narrative: str = "") -> str:
    """把结构化的报告渲染成可导出的 Markdown。"""
    start = report["start"]
    end = report["end"]
    lines = [
        f"# {report['period_label']}回顾（{start.isoformat()} ~ {end.isoformat()}）",
        "",
    ]
    if narrative.strip():
        lines += ["## AI 总结", "", narrative.strip(), ""]
    todo = report["todo"]
    lines += [
        "## 任务",
        f"- 完成 {todo['completed']} 项，新建 {todo['created']} 项，未完成 {todo['pending']} 项"
        f"（完成率 {todo['completion_rate']}%）",
        f"- 逾期 {todo['overdue']} 项",
    ]
    if todo["top_tags"]:
        lines.append("- 高频标签：" + "、".join(f"{tag}({count})" for tag, count in todo["top_tags"]))
    if todo["completed_titles"]:
        lines.append("- 完成内容：" + "、".join(todo["completed_titles"]))
    diary = report["diary"]
    lines += [
        "",
        "## 日记与笔记",
        f"- 日记 {diary['count']} 篇（有内容 {diary['written']} 篇，平均 {diary['avg_length']} 字）",
        f"- 笔记共 {report['note']['total']} 篇，本期新建 {report['note']['created']} 篇、更新 "
        f"{report['note']['updated']} 篇",
    ]
    if report["note"]["top_tags"]:
        lines.append(
            "- 笔记标签：" + "、".join(f"{tag}({count})" for tag, count in report["note"]["top_tags"])
        )
    health = report["health"]
    lines += [
        "",
        "## 健康",
        f"- 锻炼 {health['exercise_count']} 次 / {health['exercise_minutes']} 分钟 / "
        f"{health['exercise_calories']} 千卡",
        f"- 睡眠 {health['sleep_nights']} 晚，平均 {health['sleep_hours']} 小时，质量 "
        f"{health['sleep_quality']}/5",
    ]
    if health["weight_last"] is not None:
        delta = health["weight_delta"]
        lines.append(
            f"- 体重 {health['weight_first']} → {health['weight_last']} kg"
            + (f"（{delta:+.1f}）" if delta is not None else "")
        )
    finance = report["finance"]
    lines += [
        "",
        "## 财务",
        f"- 收入 {money(finance['income'])}，支出 {money(finance['expense'])}，"
        f"结余 {money(finance['balance'])}",
        f"- 日均支出 {money(finance['daily_expense'])}",
    ]
    if finance["top_categories"]:
        lines.append(
            "- 主要支出："
            + "、".join(f"{name} {money(value)}" for name, value in finance["top_categories"])
        )
    budget = report["budget"]
    if budget.get("total"):
        lines.append(
            f"- 预算 {money(budget['total'])}，已用 {money(budget.get('expense', 0))}，"
            f"剩余 {money(budget.get('remaining', 0))}"
        )
    fun = report["entertainment"]
    if fun["finished"] or fun["watching"]:
        lines += ["", "## 娱乐"]
        if fun["finished"]:
            lines.append("- 本期看完：" + "、".join(fun["finished"]))
        if fun["watching"]:
            lines.append("- 在看：" + "、".join(fun["watching"]))
    habit = report["habit"]
    if habit["checkins"]:
        lines += [
            "",
            "## 习惯",
            f"- 打卡 {habit['checkins']} 次",
            "- 明细：" + "、".join(f"{name} {count} 次" for name, count in habit["per_habit"]),
        ]
    return "\n".join(lines).rstrip() + "\n"


def report_prompt(report: dict[str, Any]) -> str:
    """把统计结果转成给大模型的叙述提示词（只发数字与标题，不发全文）。"""
    return (
        "以下是用户个人工作台的本地统计结果（已脱敏，只含计数与标题），"
        "请写一段 200 字以内的口语化总结：先讲最有价值的进展，再指出一个需要注意的"
        "问题，最后给一条具体可执行的建议。不要编造数据。\n\n"
        + render_markdown(report)
    )
