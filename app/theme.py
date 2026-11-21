"""主题：亮色/暗色两套色板 + 卡片/文字样式工厂。

色板以模块级常量导出（``TEXT`` 等），同时提供 :func:`set_mode` 在运行时整体
替换。界面模块统一通过 ``theme.X`` 读取（而不是把常量逐个 import 进来），
这样切换主题后重新 ``build()`` 就能拿到新颜色：静态导入会把导入时的值钉死。
"""

from __future__ import annotations

import flet as ft
from flet import Border, TextStyle

LIGHT = {
    "BG": "#FAF7F2",
    "CARD": "#FFFFFF",
    # SUBTLE：比卡片再浅/再深一档的块底色（日历空白格、对话气泡、代码块）
    "SUBTLE": "#F7F4EF",
    # TABLE_HEAD：列表表头、行强调底色
    "TABLE_HEAD": "#F4F6F3",
    # CHART_BG：matplotlib 画布底色，必须和卡片同色，否则暗色下图表是白板
    "CHART_BG": "#FFFFFF",
    "TEXT": "#2E2A26",
    "TEXT_MUTED": "#8C857C",
    "DIVIDER": "#EDE8DF",
    "PRIMARY": "#4A7C59",
    "ACCENT": "#C97B63",
    "DANGER": "#E53935",
    "PRIMARY_LIGHT": "#EEF3EE",
    "ACCENT_LIGHT": "#F6EDE9",
    "ON_PRIMARY": "#FFFFFF",
    "HEATMAP_LEVELS": ["#EEF3EE", "#D7E5D9", "#BBD3BF", "#8FB69A", "#5E8F6E", "#4A7C59"],
}

DARK = {
    "BG": "#1B1A18",
    "CARD": "#252422",
    "SUBTLE": "#201F1D",
    "TABLE_HEAD": "#2A2825",
    "CHART_BG": "#252422",
    "TEXT": "#EDE8E1",
    "TEXT_MUTED": "#9C948A",
    "DIVIDER": "#3A3833",
    "PRIMARY": "#7FB08D",
    "ACCENT": "#D28E76",
    "DANGER": "#EF6B68",
    "PRIMARY_LIGHT": "#2C3A31",
    "ACCENT_LIGHT": "#3B302B",
    "ON_PRIMARY": "#12211A",
    "HEATMAP_LEVELS": ["#2C3A31", "#35553F", "#3F7050", "#4E8A60", "#60A473", "#7FB08D"],
}

PALETTES = {"light": LIGHT, "dark": DARK}

# 布局常量与主题无关
NAV_WIDTH = 200
CARD_RADIUS = 12
CARD_PADDING = 16

# ---------------------------------------------------------------- 字号 token
# 硬性规则：用户可见文字不得小于 FONT_MIN。辅助文字只靠颜色/字重弱化。
# 层级：PAGE_TITLE > SECTION_TITLE > CARD_TITLE > BODY(=CAPTION=MIN)。
FONT_MIN = 12
FONT_BODY = 12
FONT_CAPTION = 12
FONT_CARD_TITLE = 13
FONT_SECTION_TITLE = 15
FONT_PAGE_TITLE = 20
# 导航/顶栏标签与正文同级，靠颜色区分激活态
FONT_NAV = 13
# 指标卡上的大数字
FONT_METRIC = 22

# 同行卡片常用高度：内容过多时卡片内部滚动，不让内容把卡片撑高。
CARD_HEIGHT_SM = 132
CARD_HEIGHT = 180
CARD_HEIGHT_LG = 240
# 三栏/看板列的固定高度（600 高窗口下仍可完整显示）
COLUMN_HEIGHT = 460

_mode = "light"

# 默认值先落成模块属性，兼容仍按常量导入的历史代码。
globals().update(LIGHT)


def current_mode() -> str:
    return _mode


def is_dark() -> bool:
    return _mode == "dark"


def set_mode(mode: str) -> str:
    """切换主题；返回实际生效的模式（未知值回退 light）。"""
    global _mode
    _mode = "dark" if str(mode).lower() == "dark" else "light"
    globals().update(PALETTES[_mode])
    return _mode


def toggle_mode() -> str:
    return set_mode("light" if is_dark() else "dark")


def palette() -> dict:
    return PALETTES[_mode]


def card_border() -> Border:
    return ft.Border.all(1, DIVIDER)


def title_style(size: int = FONT_CARD_TITLE, color: str | None = None, bold: bool = True) -> TextStyle:
    return TextStyle(size=max(size, FONT_MIN), color=color or TEXT, weight="bold" if bold else "normal")


def body_style(size: int = FONT_BODY, color: str | None = None) -> TextStyle:
    return TextStyle(size=max(size, FONT_MIN), color=color or TEXT_MUTED)


def money_style(size: int = FONT_METRIC) -> TextStyle:
    return TextStyle(size=max(size, FONT_MIN), color=TEXT, weight="bold")


def page_title_style(color: str | None = None) -> TextStyle:
    return TextStyle(size=FONT_PAGE_TITLE, color=color or TEXT, weight="bold")


def section_title_style(color: str | None = None) -> TextStyle:
    return TextStyle(size=FONT_SECTION_TITLE, color=color or TEXT, weight="bold")


def card_title_style(color: str | None = None) -> TextStyle:
    return TextStyle(size=FONT_CARD_TITLE, color=color or TEXT, weight="bold")


def caption_style(color: str | None = None) -> TextStyle:
    """辅助文字：只靠颜色弱化，字号仍为 FONT_MIN。"""
    return TextStyle(size=FONT_CAPTION, color=color or TEXT_MUTED)


def card_padding() -> ft.Padding:
    return ft.Padding.all(CARD_PADDING)


def icon_size() -> int:
    return 20


# ---------------------------------------------------------------- 字体
# 中文用「霞鹜文楷」，外文用「Consolas」。
# Flutter 只在主字体缺字形时才走 font_family_fallback，所以主字体必须是没有
# 中文字形的 Consolas，中文回退到随包分发的霞鹜文楷（assets/fonts）。
FONT_LATIN = "Consolas"
FONT_CJK = "LXGW WenKai"
# 相对 ft.run(assets_dir=...) 的路径
FONT_CJK_ASSET = "fonts/LXGWWenKai-Regular.ttf"
FONT_FALLBACK = [FONT_CJK]

_FONT_PATCHED = False


def apply_fonts(page: ft.Page) -> None:
    """注册霞鹜文楷，并把「外文 Consolas + 中文霞鹜」注入所有文本控件。"""
    fonts = dict(page.fonts or {})
    fonts[FONT_CJK] = FONT_CJK_ASSET
    page.fonts = fonts
    _patch_control_fonts()


def _with_font(kwargs: dict) -> None:
    kwargs.setdefault("font_family", FONT_LATIN)
    if not kwargs.get("font_family_fallback"):
        kwargs["font_family_fallback"] = list(FONT_FALLBACK)


def _patch_control_fonts() -> None:
    """给 ``ft.Text`` / ``ft.TextStyle`` 补默认字体。

    全项目有近 300 处 ``ft.Text(...)``，逐个加字体参数不现实；这里在构造入口
    统一补上 font_family / font_family_fallback，控件显式指定时不覆盖。
    """
    global _FONT_PATCHED
    if _FONT_PATCHED:
        return
    _FONT_PATCHED = True

    text_init = ft.Text.__init__

    def text_init_patched(self, *args, **kwargs):
        _with_font(kwargs)
        text_init(self, *args, **kwargs)

    ft.Text.__init__ = text_init_patched

    style_init = ft.TextStyle.__init__

    def style_init_patched(self, *args, **kwargs):
        _with_font(kwargs)
        style_init(self, *args, **kwargs)

    ft.TextStyle.__init__ = style_init_patched


def _text_theme() -> ft.TextTheme:
    """内部控件（输入框、下拉、按钮）走 TextTheme，同样带上中文回退。

    字号显式给出并全部 >= FONT_MIN：Material 默认的 labelSmall 只有 11 号，
    会让 Checkbox/Dropdown 之类的内部文字跌破最小字号。
    """

    def style(size: int = FONT_BODY) -> ft.TextStyle:
        return ft.TextStyle(
            size=max(size, FONT_MIN),
            font_family=FONT_LATIN,
            font_family_fallback=list(FONT_FALLBACK),
        )

    return ft.TextTheme(
        body_large=style(14),
        body_medium=style(13),
        body_small=style(FONT_BODY),
        label_large=style(14),
        label_medium=style(13),
        label_small=style(FONT_BODY),
        title_large=style(FONT_PAGE_TITLE),
        title_medium=style(FONT_SECTION_TITLE),
        title_small=style(FONT_CARD_TITLE),
        headline_small=style(FONT_SECTION_TITLE),
    )


def build_theme(seed: str | None = None) -> ft.Theme:
    """统一构造页面主题（字体已内含，切换主题时重建即可）。"""
    return ft.Theme(
        color_scheme_seed=seed or PRIMARY,
        font_family=FONT_LATIN,
        text_theme=_text_theme(),
        visual_density=ft.VisualDensity.COMPACT,
    )
