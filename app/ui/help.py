"""帮助中心：按模块说明能做什么、怎么操作，并摊开快捷键与数据位置。

内容集中在这里维护：界面调整后只改这一处。测试会校验模块与 ``main.MODULES``
一一对应，将来新增模块却忘了写帮助会直接失败。
"""

from __future__ import annotations

import logging
import flet as ft

from .. import theme as th
from ..icons import NAV, ico
from .widgets import card, divider, ghost_button, page_header

logger = logging.getLogger(__name__)

# 苏轼《定风波》。应用以此命名，帮助页标题上方放全词（含小序）。
POEM_TITLE = "定风波"
POEM_AUTHOR = "苏轼"
POEM_PREFACE = "三月七日，沙湖道中遇雨。雨具先去，同行皆狼狈，余独不觉。已而遂晴，故作此词。"
POEM_LINES: tuple[str, ...] = (
    "莫听穿林打叶声，何妨吟啸且徐行。",
    "竹杖芒鞋轻胜马，谁怕？一蓑烟雨任平生。",
    "料峭春风吹酒醒，微冷，山头斜照却相迎。",
    "回首向来萧瑟处，归去，也无风雨也无晴。",
)

QUICK_START: tuple[str, ...] = (
    "左侧导航切换模块；顶栏右侧依次是「立即同步」、搜索、主题和设置，最右是最小化/最大化/关闭。",
    "Ctrl+K 随时唤出搜索与命令面板：输入关键字搜任务、笔记、日记、娱乐等，回车执行第一条；也能直接触发「新增任务」「立即同步」这类命令。",
    "数据默认只存在本机，每天首次启动自动备份一份；数据目录在导航底部，设置里能看到同一个路径。",
    "设置里可以配同步服务地址与令牌、AI 模型、模块显隐与顺序、开机自启与关闭到托盘。",
)

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+K", "搜索与命令面板：输入即搜，回车执行第一条"),
    ("Ctrl+T", "亮色 / 暗色主题切换"),
    ("Ctrl+1 … Ctrl+9 / Ctrl+0", "跳到第 1~10 个模块，顺序跟设置里的导航排序一致"),
    ("F1", "打开本帮助页"),
    ("双击顶栏", "窗口最大化 / 还原；按住顶栏可拖动窗口"),
    ("回车", "弹窗里的输入框回车即保存，等同点「保存」"),
)

DATA_TIPS: tuple[str, ...] = (
    "数据都在本机 SQLite 文件里，导航底部显示的数据目录就是全部；换电脑直接拷整个目录。",
    "备份：每天首次启动自动备份一次，默认保留最近 7 份；设置 →「备份恢复中心」可立即备份、恢复或导出。",
    "同步：本地优先、离线可写；启动时、每 5 分钟、写入后 10 秒各自动同步一次，顶栏「立即同步」可手动触发。",
    "待同步与失败数量显示在顶栏，点开是同步明细，能单条重试或忽略，不至于一条坏记录卡住整个队列。",
    "导入导出在设置 →「数据导入导出」，支持 Markdown / CSV / JSON，导出文件落在数据目录的 exports 下。",
    "封面与正文插图先上传到同步服务端再回填链接，所以插图功能依赖服务端可用。",
)

FAQ: tuple[tuple[str, str], ...] = (
    (
        "插图或封面提示上传失败",
        "多半是同步服务端不可用或版本过旧。确认设置里的服务地址能在浏览器打开，服务端需提供 /api/upload。",
    ),
    (
        "点关闭直接退出，没收到托盘",
        "设置里勾「关闭窗口时收到托盘」需要 pystray；没装时退化为最小化到任务栏，不会直接退出。",
    ),
    (
        "导航里少了某个模块",
        "设置 →「模块显示与排序」勾选显示、↑↓ 调整顺序，Ctrl+数字的跳转顺序跟着这个列表走。",
    ),
    (
        "AI 助手提示未配置模型",
        "设置里选厂商，填服务地址、模型名和 API Key，点「测试连接」确认能通再回 AI 页。",
    ),
    (
        "到期提醒没弹出来",
        "任务要设「到期时间」和「提前提醒」；提醒每分钟检查一次，系统通知走 Windows Toast，失败会退化为提示音。",
    ),
    (
        "写完日记还要点保存吗",
        "不用，正文失焦即自动保存；任务、笔记、记账这些弹窗仍需点「保存」或回车提交。",
    ),
)

MODULE_HELP: dict[str, tuple[str, ...]] = {
    "home": (
        "顶部是问候、日期与座右铭；「今日聚焦」汇总今天到期和已逾期的任务。",
        "「活跃度热力图」按天的记录量着色，越深越活跃；旁边是数据概览。",
        "快捷记录按钮可直接新增任务、笔记、收支、锻炼、饮食、体重、睡眠，不必先切模块。",
    ),
    "todo": (
        "左栏月历选日期（上一月 / 下一月 / 今天），中栏在「全部 / 未完成 / 已完成」和四象限之间筛选。",
        "任务可设优先级、标签、到期日期与时间、提前提醒，支持循环规则（间隔、每周几、每月几号，可跳过双休日与法定节假日）和农历日期。",
        "右栏是习惯打卡；任务卡片上的「番茄 N」是已完成的番茄数。",
        "点任务卡片进入编辑，垃圾桶删除并二次确认。",
    ),
    "diary": (
        "左栏月历选日期，有日记的日子会标出来；正文失焦即自动保存，不需要手动点保存。",
        "正文支持 Markdown，也可插入图片（图片上传到同步服务端后回填链接）。",
        "右栏「人生大事」是长期目标清单：新增、编辑、删除，日期手输 YYYY-MM-DD，留空显示「未定」。",
        "列表按事件日期倒序，没填日期的沉底；下方「历史上的今天」列出往年同一天的日记。",
    ),
    "notes": (
        "Markdown 编辑器可在「源码 / 预览 / 并列」之间切换，退出点「返回」。代码块显示语言，可一键复制；引用块不用斜体。",
        "标签用逗号分隔，支持 a/b 这样的层级；回顾与时间轴会按标签聚合。",
        "封面图和正文插图都走图片上传，列表卡片上直接显示封面。",
        "点卡片进预览，编辑弹窗里也能删除笔记。",
    ),
    "health": (
        "锻炼、饮食、体重、睡眠四类记录，顶部按本周筛选，图表看体重趋势、睡眠时长、锻炼分钟、饮食热量。",
        "体重记录会按设置里的身高自动算 BMI，也能补体脂。",
        "「快捷记录」不必进二级页就能记一笔。",
    ),
    "fun": (
        "想看 / 在看 / 看完 三列看板，条目可以左右移动列。",
        "类型有电影、电视剧、纪录片、播客、书籍，可评分、加封面和备注。",
        "看完的条目仍能编辑或删除，按类型和状态都能筛。",
    ),
    "finance": (
        "账户管理余额，收支记录可筛选，也支持账户之间的转账。",
        "月度预算、固定支出与订阅：到期的订阅会自动生成一笔交易，重复启动不会重复记账。",
        "「一句话记账」输入「午饭花了 32 元」就能自动解析成一条收支。",
        "图表看本月支出占比和近六个月收支趋势。",
    ),
    "reports": (
        "按周 / 月 / 年切换，统计任务完成率与逾期、日记和笔记数量、健康记录、收支与结余。",
        "「生成 AI 总结」会把统计结果交给模型写一段叙述，需要先配置模型。",
        "「导出 Markdown」把本期报告存到数据目录的 exports 下。",
    ),
    "timeline": (
        "把任务、日记、笔记、健康、娱乐、记账的记录按天串成一条生活时间线。",
        "时间范围可选近 7 / 30 / 90 天或一年，也能按模块和跨模块标签筛选。",
        "「查看/新增关联」给两条记录建立关联，「取消关联」拆开，关联关系在各模块都能看到。",
    ),
    "ai": (
        "输入框里用 @中文表名（比如 @任务）引用数据，相关记录会随问题一起带进上下文。",
        "首次发送前会提示数据将发往第三方大模型，同意后才继续；之后可在设置里查看说明。",
        "回复里的结构化草稿可以「保存为记录」，也能「撤销」刚刚写入的内容。",
        "模型在设置里配置（厂商、服务地址、模型名、API Key），未配置时页面会提示「去设置」。",
    ),
}


def _dot() -> ft.Container:
    return ft.Container(
        width=5, height=5, bgcolor=th.PRIMARY, border_radius=3, margin=ft.Margin.only(top=6)
    )


def _bullets(points: tuple[str, ...]) -> ft.Column:
    return ft.Column(
        [
            ft.Row(
                [_dot(), ft.Text(point, size=th.FONT_BODY, color=th.TEXT_MUTED, expand=True)],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            for point in points
        ],
        spacing=6,
        tight=True,
    )


def _module_card(key: str, label: str, navigate) -> ft.Container:
    head = ft.Row(
        [
            ft.Icon(ico(NAV.get(key, "CIRCLE_OUTLINED")), size=16, color=th.PRIMARY),
            ft.Text(label, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
            ft.Container(expand=True),
            ghost_button("去看看", lambda e, k=key: navigate(k)),
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    # 统一高度：同一排的模块卡片不再因说明条数不同而高矮不一，超长内容卡片内滚动。
    return card(
        ft.Column([head, divider(), _bullets(MODULE_HELP[key])], spacing=8,
                  scroll=ft.ScrollMode.AUTO),
        width=316,
        height=190,
    )


def _shortcut_row(key: str, desc: str) -> ft.Row:
    return ft.Row(
        [
            ft.Container(content=ft.Text(key, size=th.FONT_BODY, color=th.TEXT), width=150),
            ft.Text(desc, size=th.FONT_BODY, color=th.TEXT_MUTED, expand=True),
        ],
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def _faq_row(question: str, answer: str) -> ft.Column:
    return ft.Column(
        [
            ft.Text(question, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
            ft.Text(answer, size=th.FONT_BODY, color=th.TEXT_MUTED),
        ],
        spacing=2,
        tight=True,
    )


def _poem() -> ft.Control:
    lines: list[ft.Control] = []
    for index, line in enumerate(POEM_LINES):
        if index == 2:
            lines.append(ft.Container(height=8))
        lines.append(ft.Text(line, size=th.FONT_BODY, color=th.TEXT))
    return card(
        ft.Column(
            [
                ft.Text(POEM_TITLE, size=th.FONT_CARD_TITLE, color=th.TEXT, weight="bold"),
                ft.Text(POEM_AUTHOR, size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                ft.Container(height=6),
                ft.Text(POEM_PREFACE, size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                ft.Column(lines, spacing=4, tight=True),
            ],
            spacing=2,
            tight=True,
        ),
    )


def build(page: ft.Page, navigate) -> ft.Control:
    """帮助页：词作在标题之上，其下是上手说明、模块、快捷键、数据与常见问题。"""
    from ..main import MODULE_LABELS  # 延迟导入：main 反过来依赖本模块

    heading = page_header(
        "帮助与上手",
        "说明按当前版本的界面整理；如果和界面有出入，以界面为准。",
    )
    module_cards = [
        _module_card(key, MODULE_LABELS.get(key, key), navigate) for key in MODULE_HELP
    ]
    return ft.Column(
        [
            _poem(),
            heading,
            card(_bullets(QUICK_START), title="快速上手"),
            ft.Text("各模块能做什么", size=th.FONT_SECTION_TITLE, color=th.TEXT,
                    weight="bold"),
            ft.Row(module_cards, wrap=True, spacing=12, run_spacing=12),
            card(
                ft.Column([_shortcut_row(key, desc) for key, desc in SHORTCUTS], spacing=8, tight=True),
                title="快捷键与鼠标",
            ),
            card(_bullets(DATA_TIPS), title="数据、备份与同步"),
            card(
                ft.Column([_faq_row(q, a) for q, a in FAQ], spacing=10, tight=True),
                title="常见问题",
            ),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
