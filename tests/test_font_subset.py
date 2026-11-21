"""子集字体守门测试。

界面文案一旦出现子集里没有的字，用户看到的就是豆腐块或字形跳变。这里用
package/subset_font.py 的同一份字表反查 cmap，保证「代码里出现过的字」都被覆盖。
"""

import importlib.util
import unittest
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = ROOT / "assets" / "fonts" / "LXGWWenKai-Regular.ttf"
SCRIPT = ROOT / "packaging" / "subset_font.py"


def _load_subset_module():
    spec = importlib.util.spec_from_file_location("subset_font", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cmap(path: Path = FONT_PATH) -> set[int]:
    font = TTFont(path, lazy=True)
    try:
        return set(font.getBestCmap())
    finally:
        font.close()


class FontSubsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.subset_font = _load_subset_module()

    def test_font_is_subsetted(self) -> None:
        self.assertTrue(FONT_PATH.exists(), "缺少 assets/fonts 下的子集字体")
        size_mb = FONT_PATH.stat().st_size / (1024 * 1024)
        self.assertLess(size_mb, self.subset_font.SIZE_LIMIT_MB)

    def test_family_name_preserved(self) -> None:
        # theme.FONT_CJK 用的是 "LXGW WenKai"，靠字体内部族名绑定；子集不能裁掉 name 表。
        font = TTFont(FONT_PATH, lazy=True)
        try:
            family = font["name"].getDebugName(1)
        finally:
            font.close()
        self.assertEqual(family, "LXGW WenKai")

    def test_covers_code_and_gb2312(self) -> None:
        missing = self.subset_font.missing_chars()
        preview = "".join(sorted(missing)[:30])
        self.assertEqual(missing, set(), f"子集缺 {len(missing)} 个字：{preview}")

    def test_key_glyphs_present(self) -> None:
        cmap = _cmap()
        for char in "定风波记账日记番茄￥—…「」×÷°±":
            with self.subTest(char=char):
                self.assertIn(ord(char), cmap)
