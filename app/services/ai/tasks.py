"""AI 快捷任务：把常见诉求固化成「提示词 + 数据范围」。

AI 助手原来只有自由对话；这里把周报、任务拆解、日记润色、消费分析、笔记摘要
等场景固化成任务卡，用户点一下就能带着正确的数据范围提问。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from ..focus import brief_text, today_focus
from ..reports import build_report, report_prompt
from ...db.models import table_label

logger = logging.getLogger(__name__)

BASE_SYSTEM = (
    "你是「定风波」个人工作台的 AI 助手。用户可能通过「@表名」引用待办、日记、笔记、"
    "锻炼、饮食、体重、睡眠、娱乐、账户、收支等数据，这些数据会以「【表名】」块的形式"
    "附加在用户消息中。请结合数据给出务实、简洁、可执行的建议。"
    "除非用户明确要求生成草稿，否则只输出文本建议，不要声称已写入数据。使用简体中文。"
)

DRAFT_SYSTEM = (
    BASE_SYSTEM
    + " 当需要产出可保存的记录时，只输出一个 ```json 代码块，格式为 "
    '{"drafts": [{"type": "todo", "title": "...", "due_date": "YYYY-MM-DD", "priority": "high", "note": ""}]}；'
    "不要输出除 JSON 代码块以外的解释。"
)


@dataclass(frozen=True)
class AITask:
    key: str
    label: str
    instruction: str
    refs: tuple[str, ...] = ()
    source: str = ""  # report / focus / ""
    draft: bool = False
    hint: str = ""


TASKS: tuple[AITask, ...] = (
    AITask(
        "daily_brief",
        "今日建议",
        "请基于今天的聚焦数据，给出今天最值得做的三件事（按优先级排序），并各用一句话说明理由。",
        source="focus",
        hint="结合今日任务、健康、消费与习惯",
    ),
    AITask(
        "weekly_report",
        "写周报",
        "请根据下面的本地统计，写一份本周回顾：先讲最有价值的进展，再指出一个需要注意的问题，"
        "最后给三条下周的具体建议。使用小标题与列表。",
        source="report",
    ),
    AITask(
        "breakdown",
        "任务拆解",
        "请把我当前的任务拆解成今天就能开始执行的具体子任务。每个子任务都要可保存为一条任务，"
        "标题以动词开头，写明预计耗时；due_date 用今天（{today}）。最多 8 条。",
        refs=("todos",),
        draft=True,
        hint="产出可保存的任务草稿",
    ),
    AITask(
        "polish_diary",
        "日记润色",
        "请帮我润色最近一篇日记：保留事实与情绪，不要编造细节，让表达更通顺、有条理。"
        "先给出润色后的正文，再列出 3 个可以继续写的追问。",
        refs=("diaries",),
    ),
    AITask(
        "spending",
        "消费分析",
        "请分析我本月的支出结构：指出占比最高的两类支出、可能的异常项（金额明显高于同类），"
        "并给出 3 条可执行的省钱建议。",
        refs=("transactions",),
        source="report",
    ),
    AITask(
        "note_summary",
        "笔记摘要",
        "请总结我最近的笔记，提炼 5 条要点，并为每篇笔记建议一个更清晰的标题与标签。",
        refs=("notes",),
    ),
    AITask(
        "health_trend",
        "健康趋势",
        "请总结我最近的运动、睡眠与体重趋势，指出一个好转信号和一个风险信号，并给出一条具体建议。",
        refs=("exercises", "sleeps", "weights", "diets"),
    ),
    AITask(
        "watchlist",
        "待看清单",
        "请把我的娱乐记录整理成一份待看清单：按「先看什么」排序，说明理由，并顺手补 3 部同类型的推荐。",
        refs=("entertainments",),
    ),
    AITask(
        "draft_todo",
        "一句话变任务",
        "请把下面这句话整理成 1-3 条任务草稿（标题简洁、动词开头，能推断出截止日就填 due_date）。\n用户输入：",
        draft=True,
        hint="输入「明天下午三点前交周报」",
    ),
    AITask(
        "draft_transaction",
        "一句话记账",
        "请把下面这句话整理成一条交易草稿（kind 为 income 或 expense，category 用中文分类，"
        "amount 只要数字，date 为 YYYY-MM-DD）。\n用户输入：",
        draft=True,
        hint="输入「今天午饭花了 32 元」",
    ),
)

TASK_BY_KEY = {task.key: task for task in TASKS}


@dataclass
class TaskRequest:
    system: str
    content: str
    refs: list[str] = field(default_factory=list)
    draft: bool = False
    task: AITask | None = None
    # 本次请求是否真的会把本地数据发往第三方（refs 之外，source=focus/report
    # 也会把统计拼进 prompt）——知情确认必须按它判断，而不是只看 refs。
    sends_data: bool = False
    data_scope: str = ""
    # 上下文时间下界：本月类任务只把本月的数据发给模型。
    since: date | None = None


def build_request(key: str, extra: str = "", today: date | None = None) -> TaskRequest:
    """构造一次任务请求：系统提示、附加数据、是否允许产出草稿。"""
    task = TASK_BY_KEY.get(key)
    if task is None:
        raise ValueError(f"未知任务：{key}")
    today = today or date.today()
    pieces: list[str] = []
    scope = ""
    if task.source == "report":
        period = "month" if key == "spending" else "week"
        report = build_report(period, today)
        pieces.append(report_prompt(report))
        scope = "本期统计（任务、日记、笔记、健康、收支、娱乐、习惯）"
    elif task.source == "focus":
        pieces.append("今天的聚焦数据：\n" + brief_text(today_focus(today)))
        scope = "今日聚焦数据（任务、健康、消费、习惯）"
    refs_scope = "、".join(f"@{table_label(name)}" for name in task.refs)
    send_scope = "，".join(part for part in (scope, refs_scope) if part)
    instruction = task.instruction.replace("{today}", today.isoformat())
    content = instruction if not extra.strip() else f"{instruction}\n\n{extra.strip()}"
    if pieces:
        content = "\n\n".join([*pieces, content])
    return TaskRequest(
        system=DRAFT_SYSTEM if task.draft else BASE_SYSTEM,
        content=content,
        refs=list(task.refs),
        draft=task.draft,
        task=task,
        sends_data=bool(send_scope),
        data_scope=send_scope,
        since=today.replace(day=1) if key == "spending" else None,
    )
