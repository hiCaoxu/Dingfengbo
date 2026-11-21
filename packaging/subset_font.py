# -*- mode: python ; coding: utf-8 -*-
"""把霞鹜文楷裁成子集，压缩发布体积。

原字体 18.42MB / 36753 个字形，其中大部分是 GB2312 之外的汉字。这个应用只会
用到「界面文案 + 日常输入」的那批字，整包分发时多出来的十几个 MB 只是用户的
下载与磁盘成本。

保留范围（取并集）：

1. ASCII、Latin-1、通用标点、CJK 标点、全角字符（排版与数字符号）；
2. GB2312 全集（6763 汉字，覆盖《通用规范汉字表》一级与二级大部分）；
3. 源码和 README 里实际出现过的所有字符——新增界面文案不会突然缺字；
4. ``EXTRA_CHARS`` 手工补充的字（人名、网络常用字等）。

用法::

    python packaging/subset_font.py            # 重新生成 assets/fonts 下的子集
    python packaging/subset_font.py --check    # 只校验现有子集是否覆盖代码用字

需要扩充字表时，把缺的字加进 ``EXTRA_CHARS`` 再重跑；确认要回到全量字体时，
直接把 ``packaging/fonts`` 里的原字体拷回 ``assets/fonts`` 即可。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging" / "fonts" / "LXGWWenKai-Regular.ttf"
TARGET = ROOT / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"

# 扫描这些文件，确保界面/文档里出现过的字符都在子集里（避免上线才发现缺字）。
SCAN_GLOBS = ("app/**/*.py", "server/**/*.py", "main.py", "README.md")

# 手工补充区：GB2312 之外但仍常见的字（人名、网络用字）。按需追加即可。
EXTRA_CHARS = "囧喆堃玥珺瑄頔赟淼焱垚犇骉"

# 体积守护：超过这个值说明字表被放得太宽，先怀疑是不是整包了。
SIZE_LIMIT_MB = 8.0


def punctuation_chars() -> set[str]:
    """ASCII、Latin-1 与各类标点/全角字符。"""
    chars = {chr(code) for code in range(0x20, 0x7F)}  # 基本拉丁
    chars |= {chr(code) for code in range(0xA0, 0x100)}  # ° ± × ÷
    chars |= {chr(code) for code in range(0x2000, 0x2070)}  # — … “ ” • ‰
    chars |= {chr(code) for code in range(0x3000, 0x3040)}  # 「」、。『』
    chars |= {chr(code) for code in range(0xFF00, 0xFF66)}  # 全角字母数字与标点
    chars |= {chr(code) for code in range(0xFFE0, 0xFFE6)}  # ￥ ￡ ￢
    return chars


def gb2312_chars() -> set[str]:
    """GB2312 全集：6763 汉字 + 682 符号。"""
    chars: set[str] = set()
    for high in range(0xA1, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                chars.add(bytes((high, low)).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def source_chars() -> set[str]:
    """源码与 README 里出现过的所有字符。"""
    chars: set[str] = set()
    for pattern in SCAN_GLOBS:
        for path in ROOT.glob(pattern):
            try:
                chars.update(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return chars


def wanted_chars() -> set[str]:
    return punctuation_chars() | gb2312_chars() | source_chars() | set(EXTRA_CHARS)


def build(source: Path = SOURCE, target: Path = TARGET) -> tuple[int, float]:
    """生成子集字体，返回 (保留字形数, 目标体积 MB)。"""
    if not source.exists():
        raise SystemExit(f"找不到原始字体：{source}（子集化需要 packaging/fonts 里的原字体）")

    font = TTFont(source)
    available = set(font.getBestCmap())
    # 字体里没有的字符（emoji、生僻扩展区）直接跳过，subsetter 遇到会报 KeyError。
    unicodes = sorted({ord(ch) for ch in wanted_chars() if ord(ch) in available})

    options = subset.Options()
    # name 表全留：Flutter 靠内部的 "LXGW WenKai" 族名绑定字体，缺了会渲染不出中文。
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    options.layout_features = ["*"]
    # 保留 .notdef 与 hinting：小字号下不出豆腐块、笔画不糊。
    options.notdef_outline = True
    options.recommended_glyphs = True
    options.hinting = True
    options.drop_tables += ["DSIG"]  # 子集后签名必然失效

    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)

    target.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = None  # 保持 ttf：Flutter 不解 woff/woff2
    font.save(target)
    size_mb = target.stat().st_size / (1024 * 1024)
    return len(font.getBestCmap()), size_mb


def missing_chars(target: Path = TARGET, source: Path = SOURCE) -> set[str]:
    """现有子集缺哪些「代码用字 + GB2312」字符。

    只报「原字体有、子集没有」的字：源码里可能混入 emoji 或控制类码位
    （U+200C 零宽非连接符等），原字体本来就没有，不算子集的锅。
    """

    def _cmap_of(path: Path) -> set[int]:
        font = TTFont(path, lazy=True)
        try:
            return set(font.getBestCmap())
        finally:
            font.close()

    subset_cmap = _cmap_of(target)
    try:
        source_cmap = _cmap_of(source)
    except Exception:  # 源字体缺失时退化为「凡是缺的都报」，宁可多报不漏报
        source_cmap = None
    missing: set[str] = set()
    for char in wanted_chars():
        code = ord(char)
        if code in subset_cmap:
            continue
        if source_cmap is not None and code not in source_cmap:
            continue
        missing.add(char)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="霞鹜文楷子集化")
    parser.add_argument(
        "--check", action="store_true", help="只校验现有子集覆盖，不重新生成"
    )
    args = parser.parse_args(argv)

    if args.check:
        if not TARGET.exists():
            print(f"缺少子集字体：{TARGET}", file=sys.stderr)
            return 1
        missing = missing_chars()
        size_mb = TARGET.stat().st_size / (1024 * 1024)
        if missing:
            preview = "".join(sorted(missing)[:40])
            print(f"子集缺 {len(missing)} 个字符：{preview}", file=sys.stderr)
            return 1
        if size_mb > SIZE_LIMIT_MB:
            print(f"子集体积 {size_mb:.2f}MB 超过上限 {SIZE_LIMIT_MB}MB", file=sys.stderr)
            return 1
        print(f"子集 OK：{size_mb:.2f}MB")
        return 0

    before = SOURCE.stat().st_size / (1024 * 1024)
    glyphs, size_mb = build()
    saved = before - size_mb
    print(
        f"{SOURCE.name} -> {TARGET.name}: {before:.2f}MB -> {size_mb:.2f}MB "
        f"(省 {saved:.2f}MB, 保留 {glyphs} 个字形)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
