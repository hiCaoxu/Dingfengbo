"""把 app/assets/logo.png 收成 Windows 图标 packaging/icon.ico。构建时自动调用。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "assets" / "logo.png"
OUT = Path(__file__).with_name("icon.ico")
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_ico() -> None:
    image = Image.open(SRC).convert("RGBA")
    image.save(OUT, format="ICO", sizes=SIZES)


if __name__ == "__main__":
    make_ico()
    print(f"已生成 {OUT}")
