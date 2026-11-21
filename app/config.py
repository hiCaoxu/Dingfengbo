"""路径与应用设置。应用数据始终与安装目录分离。"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(r"D:\Data\Dingfengbo")
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_HEIGHT_CM = 165.0
DEFAULT_WEIGHT_KG = 65.0

# 国产大模型厂商预设：均为 OpenAI 兼容协议，key 为 provider id。
# base_url 需以 /v1 等兼容路径结尾；model 为默认模型，豆包因需推理接入点 id 故留空。
AI_PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "zhipu": {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "kimi": {"name": "Kimi（月之暗面）", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "qwen": {"name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "doubao": {"name": "豆包（火山方舟）", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": ""},
    "custom": {"name": "自定义", "base_url": "", "model": ""},
}


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
LOGS_DIR: Path = DATA_DIR / "logs"

_DEFAULT_SETTINGS: dict[str, Any] = {
    "server_url": DEFAULT_SERVER_URL,
    "sync_token": "",
    "sync_enabled": True,
    "height_cm": DEFAULT_HEIGHT_CM,
    "gender": "男",
    "user_name": "",
    # AI 助手配置
    "ai_provider": "custom",
    "ai_base_url": "",
    "ai_api_key": "",
    "ai_model": "",
    # 首次向第三方大模型发送引用数据前的知情确认是否已完成
    "ai_data_notice_ack": False,
}

# settings.json 会被同步线程（游标落盘）与设置界面并发读写，统一串行化避免互相覆盖。
_SETTINGS_LOCK = threading.Lock()


def _obfuscate_token(value: str) -> str:
    """本地落盘前对 token 做 base64 混淆（非加密，仅避免明文裸奔）。"""
    return "b64:" + base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


def _deobfuscate_token(value: str) -> str:
    if value.startswith("b64:"):
        try:
            return base64.urlsafe_b64decode(value[4:].encode("ascii")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return value
    return value


def load_settings() -> dict[str, Any]:
    settings = dict(_DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update(loaded)
        except (OSError, ValueError):
            logger.exception("读取设置文件失败，使用默认设置")
    token = settings.get("sync_token")
    if token:
        settings["sync_token"] = _deobfuscate_token(str(token))
    ai_key = settings.get("ai_api_key")
    if ai_key:
        settings["ai_api_key"] = _deobfuscate_token(str(ai_key))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    with _SETTINGS_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        merged = load_settings()
        merged.update(settings)
        if merged.get("sync_token"):
            merged["sync_token"] = _obfuscate_token(str(merged["sync_token"]))
        if merged.get("ai_api_key"):
            merged["ai_api_key"] = _obfuscate_token(str(merged["ai_api_key"]))
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, SETTINGS_PATH)


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)
