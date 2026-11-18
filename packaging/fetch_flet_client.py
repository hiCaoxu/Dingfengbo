"""下载并缓存 PyInstaller 所需的 Flet 桌面客户端压缩包。"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

FLET_VERSION = "0.86.5"
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
