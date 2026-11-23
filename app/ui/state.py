"""页面状态：收敛各模块散落的模块级字典，统一为类型化命名空间。

模块级可变全局状态会在多窗口/测试场景下互相污染；这里用 dataclass 明确每个
模块的状态字段，并保留字典式访问以平滑迁移，后续可逐步改为属性访问。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

class _StateBase:
    """支持字典式访问的类型化状态基类，便于从散落的字典平滑迁移。

    语义要跟 dict 对齐：缺键抛 ``KeyError``（不是 ``AttributeError``），``get()``
    在值为 None 时也返回 default。否则 ``state["current"]`` 的错误处理分支和
    ``dict`` 用法会写出两套行为。
    """

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def __setitem__(self, key: str, value) -> None:
        setattr(self, key, value)

    def get(self, key: str, default=None):
        value = getattr(self, key, None)
        return default if value is None else value

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


@dataclass
class HomeState(_StateBase):
    year: int = field(default_factory=lambda: date.today().year)
    # focus=今日聚焦（行动页），overview=数据概览（原仪表盘）
    view: str = "focus"


@dataclass
class TodoState(_StateBase):
    tag: str = "全部"
    status: str = "未完成"
    year: int = field(default_factory=lambda: date.today().year)
    month: int = field(default_factory=lambda: date.today().month)
    # 选中的日期（ISO 字符串，None=不按日期筛选）
    date: str | None = None
    # 四象限筛选：空=全部，q1..q4
    quadrant: str = ""


@dataclass
class NotesState(_StateBase):
    tag_path: str = "全部"
    expanded: set[str] = field(default_factory=set)


@dataclass
class DiaryState(_StateBase):
    view: str = "月历"
    year: int = field(default_factory=lambda: date.today().year)
    month: int = field(default_factory=lambda: date.today().month)
    selected: date = field(default_factory=date.today)
    query: str = ""


@dataclass
class HealthState(_StateBase):
    # summary=汇总首页，records=记录二级页
    view: str = "summary"
    tab: str = "锻炼"
    range: str = "本周"


@dataclass
class FinanceState(_StateBase):
    view: str = "overview"
    year: int | None = None
    month: int = 0
    page: int = 0


@dataclass
class EntertainmentState(_StateBase):
    kind: str = "全部"


@dataclass
class AIState(_StateBase):
    # 对话历史：每项 {"role": "user"|"assistant", "text": str}
    messages: list[dict[str, Any]] = field(default_factory=list)
    busy: bool = False


@dataclass
class ReportsState(_StateBase):
    period: str = "week"
    # AI 叙述按周期缓存，切换周期不丢失
    narrative: dict[str, str] = field(default_factory=dict)


@dataclass
class TimelineState(_StateBase):
    days: int = 30
    module: str = "全部"
    tag: str | None = None


class PageState:
    """全局页面状态容器；各模块通过 state.<模块> 访问。"""

    def __init__(self) -> None:
        self.home = HomeState()
        self.todo = TodoState()
        self.notes = NotesState()
        self.diary = DiaryState()
        self.health = HealthState()
        self.finance = FinanceState()
        self.entertainment = EntertainmentState()
        self.ai = AIState()
        self.reports = ReportsState()
        self.timeline = TimelineState()
        # 进程启动时（或上次跨天同步时）的「今天」：各模块的日期默认值都派生自它。
        self.today = date.today()


state = PageState()


def sync_today(today: date | None = None) -> date:
    """把「按启动日固化的今天」推进到真正的今天。

    各模块的日期状态（首页年份、任务月历、日记的选中日期与月历）在进程启动时
    取了一次 ``date.today()``；应用整夜开着（或常驻托盘）时它们会停在昨天——
    首页日历卡仍显示昨天的日期就是这么来的。

    只推进**还停在旧今天**的字段：用户已经翻到别的日期/月份时保持不动，免得
    每次刷新把正在看的内容拽回今天。
    """
    today = today or date.today()
    previous = state.today
    if today == previous:
        return today
    state.today = today

    home = state.home
    # 年份还停在旧今天所在的年（没手动切过年份）才跟着跨年。
    if home.year == previous.year:
        home.year = today.year

    todo = state.todo
    # 任务页的月历：没手动翻过月份才跟着走。
    if (todo.year, todo.month) == (previous.year, previous.month):
        todo.year, todo.month = today.year, today.month

    diary = state.diary
    # 整块日期状态都没被用户动过（选中日期=旧今天、月历=旧今天的月）才整体推到
    # 今天；用户翻过别的日子/月份就整块不动，避免"日期跳了但月历没跳"。
    if (
        diary.selected == previous
        and (diary.year, diary.month) == (previous.year, previous.month)
    ):
        diary.selected = today
        diary.year, diary.month = today.year, today.month
    return today
