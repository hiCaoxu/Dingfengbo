"""路径与应用设置。应用数据始终与安装目录分离。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = Path(r"D:\Data\Dingfengbo")


def _resolve_data_dir() -> Path:
    override = os.environ.get("DINGFENGBO_DATA_DIR")
    if override:
        candidate = Path(override)
    elif os.name == "nt":
        candidate = DEFAULT_DATA_DIR
    else:
        candidate = Path.home() / "Dingfengbo"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        fallback = Path.home() / "Dingfengbo"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR: Path = _resolve_data_dir()
DB_PATH: Path = DATA_DIR / "dingfengbo.db"
SETTINGS_PATH: Path = DATA_DIR / "settings.json"

_DEFAULT_SETTINGS: dict[str, Any] = {
    "server_url": "http://127.0.0.1:8000",
    "sync_token": "",
    "sync_enabled": True,
    "height_cm": 165.0,
    "gender": "男",
    "user_name": "",
}


def load_settings() -> dict[str, Any]:
    settings = dict(_DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update(loaded)
        except (OSError, ValueError):
            pass
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = load_settings()
    merged.update(settings)
    SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)
