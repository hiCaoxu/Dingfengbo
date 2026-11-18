"""生成简约线性应用图标 icon.ico（构建时自动调用）。"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

PRIMARY = "#4A7C59"

OUT = Path(__file__).with_name("icon.ico")


def make_png(size: int = 256) -> bytes:
    fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#FAF7F2")
    box = FancyBboxPatch(
        (0.08, 0.08), 0.84, 0.84,
        boxstyle="round,pad=0.02,rounding_size=0.16",
        linewidth=0.035, edgecolor=PRIMARY, facecolor="none",
    )
    ax.add_patch(box)
    ax.text(0.5, 0.50, "定", ha="center", va="center", fontsize=52,
            color=PRIMARY, family=["Microsoft YaHei", "SimHei", "sans-serif"])
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, transparent=False)
    plt.close(fig)
    return buffer.getvalue()


def make_ico(png: bytes) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


if __name__ == "__main__":
    OUT.write_bytes(make_ico(make_png()))
    print(f"已生成 {OUT}")
