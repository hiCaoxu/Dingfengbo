"""下载并缓存 PyInstaller 所需的 Flet 桌面客户端压缩包。

版本优先级：环境变量 FLET_VERSION > 已安装的 flet_desktop 版本 > 0.86.5 兜底。
以前这里是硬编码 0.86.5，和 requirements.txt / 实际安装版本三方不一致，
构建出来的客户端可能与运行时协议不匹配。
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path


def _installed_version() -> str:
    """已安装的 flet-desktop 版本。

    注意：``flet_desktop`` 并没有 ``__version__`` 属性（1.x 里是
    ``flet_desktop.version.version`` / 包元数据），只读 ``__version__`` 会静默回退到
    兜底值，于是一路下载错误版本的客户端——运行时协议对不上，启动即失败。
    """
    try:
        from importlib.metadata import version

        return str(version("flet-desktop"))
    except Exception:
        pass
    try:
        import flet_desktop
        from flet_desktop.version import version as package_version

        return str(getattr(flet_desktop, "__version__", "") or package_version)
    except Exception:
        return ""


FLET_VERSION = os.environ.get("FLET_VERSION") or _installed_version() or "0.86.5"
ARTIFACT = "flet-windows.zip"
DEST = Path(__file__).with_name(ARTIFACT)
URL = os.environ.get(
    "FLET_CLIENT_URL",
    f"https://github.com/flet-dev/flet/releases/download/v{FLET_VERSION}/{ARTIFACT}",
)


def main() -> None:
    if DEST.exists() and DEST.stat().st_size > 1024:
        print(f"Flet client already cached: {DEST}")
        return
    print(f"Downloading Flet desktop client from {URL}")
    urllib.request.urlretrieve(URL, DEST)
    if DEST.stat().st_size <= 1024:
        DEST.unlink(missing_ok=True)
        raise RuntimeError("Downloaded Flet client archive is too small")
    print(f"Saved: {DEST} ({DEST.stat().st_size} bytes)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to fetch Flet client: {exc}", file=sys.stderr)
        raise
