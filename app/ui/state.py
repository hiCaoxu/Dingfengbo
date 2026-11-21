"""页面状态：收敛各模块散落的模块级字典，统一为类型化命名空间。

模块级可变全局状态会在多窗口/测试场景下互相污染；这里用 dataclass 明确每个
模块的状态字段，并保留字典式访问以平滑迁移，后续可逐步改为属性访问。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class _StateBase:
    """支持字典式访问的类型化状态基类，便于从散落的字典平滑迁移。"""

    def __getitem__(self, key: str):
        return getattr(self, key)

    def __setitem__(self, key: str, value) -> None:
        setattr(self, key, value)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


@dataclass
class HomeState(_StateBase):
    selected_date: date = field(default_factory=date.today)
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


state = PageState()
