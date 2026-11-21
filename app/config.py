"""路径与应用设置。应用数据始终与安装目录分离。"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
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
    # 最近一次成功备份的日期（YYYY-MM-DD），用于「每天最多备份一次」
    "last_backup_date": "",
    # 备份中心：自定义目录（空=默认 DATA_DIR/backups）与保留份数
    "backup_dir": "",
    "backup_keep": 7,
    # 界面：light / dark；首页默认聚焦视图；导航模块排序与隐藏
    "theme_mode": "light",
    "home_view": "focus",
    "module_order": [
        "home", "todo", "diary", "notes", "health", "fun", "finance", "habits", "reports", "timeline", "ai",
    ],
    "hidden_modules": [],
    # 系统集成：开机自启、关闭窗口最小化（有 pystray 时收入托盘）
    "autostart": False,
    "close_to_tray": False,
    # 同步：选择性同步（空列表=全部模块都同步）
    "sync_tables": [],
    # 数据导入导出：最近一次导出目录
    "last_export_dir": "",
}

# settings.json 会被同步线程（游标落盘）与设置界面并发读写，统一串行化避免互相覆盖。
_SETTINGS_LOCK = threading.Lock()


_B64_PREFIX = "b64:"
_DPAPI_PREFIX = "dpapi:"

# DPAPI 可用性只探测一次：不可用时（非 Windows / 受策略限制）退回 base64 混淆。
_DPAPI_AVAILABLE: bool | None = None


def _dpapi_crypt(data: bytes, protect: bool) -> bytes | None:
    """以「当前 Windows 用户」身份加/解密，不可用时返回 None。

    DPAPI 的密钥由系统按用户绑定管理，settings.json 被复制到别的机器或别的
    Windows 账户也无法解开，比 base64 混淆多一层实际保护。
    """
    global _DPAPI_AVAILABLE
    if not data or _DPAPI_AVAILABLE is False:
        return None
    if sys.platform != "win32":
        _DPAPI_AVAILABLE = False
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(_Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_Blob),
        ]
        function.restype = wintypes.BOOL

        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        if not function(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        logger.warning("DPAPI 不可用，敏感字段退回 base64 混淆", exc_info=True)
        _DPAPI_AVAILABLE = False
        return None
    _DPAPI_AVAILABLE = True
    return result


def _b64_decode(payload: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(payload.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


def _obfuscate_token(value: str) -> str:
    """落盘前保护 token/api key：优先 DPAPI 加密，失败退回 base64 混淆。"""
    raw = value.encode("utf-8")
    protected = _dpapi_crypt(raw, protect=True)
    if protected is not None:
        return _DPAPI_PREFIX + base64.urlsafe_b64encode(protected).decode("ascii")
    return _B64_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def _deobfuscate_token(value: str) -> str:
    if value.startswith(_DPAPI_PREFIX):
        payload = _b64_decode(value[len(_DPAPI_PREFIX):])
        raw = _dpapi_crypt(payload, protect=False) if payload else None
        if raw is None:
            logger.warning("密钥解密失败（可能换了 Windows 用户或机器），请重新填写")
            return ""
        return raw.decode("utf-8", "replace")
    if value.startswith(_B64_PREFIX):
        payload = _b64_decode(value[len(_B64_PREFIX):])
        if payload is None:
            return value
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
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
