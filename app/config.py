"""路径与应用设置。应用数据始终与安装目录分离。"""

from __future__ import annotations

import base64
import copy
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
        "home", "todo", "diary", "notes", "health", "fun", "finance", "reports", "timeline", "ai",
    ],
    "hidden_modules": [],
    # 系统集成：开机自启、关闭窗口最小化（有 pystray 时收入托盘）
    "autostart": False,
    "close_to_tray": False,
    # 同步：选择性同步（空列表=全部模块都同步）
    "sync_tables": [],
    # 数据导入导出：最近一次导出目录
    "last_export_dir": "",
    # 安全：DPAPI 不可用时是否允许把令牌/Key 以 base64（等同明文）落盘。
    # 默认关闭——宁可保存失败并明确报错，也不静默把凭据写成可解码文本。
    "allow_weak_secret_storage": False,
}

# settings.json 会被同步线程（游标落盘）与设置界面并发读写，统一串行化避免互相覆盖。
_SETTINGS_LOCK = threading.Lock()

# settings.json 的解析结果按 (mtime_ns, size) 缓存。load_settings() 在导航重建、
# Ctrl+数字跳转这类高频路径上被反复调用，每次都读盘 + DPAPI 解密（ctypes 调
# CryptUnprotectData）代价不小。外部改写文件会改变 mtime，缓存自动失效；
# save_settings 写完主动清空，避免同一时间戳精度内命中旧值。
_SETTINGS_CACHE: tuple[tuple[int, int], dict[str, Any]] | None = None


_B64_PREFIX = "b64:"
_DPAPI_PREFIX = "dpapi:"

# 上次读取时哪些敏感字段「解密失败」。换机器/换 Windows 账户后 DPAPI 解不开，
# load_settings 只能返回空串；此时任何一次后台保存都不能把磁盘上的原密文抹掉。
_DECRYPT_FAILED: set[str] = set()


class InsufficientSecretProtection(RuntimeError):
    """DPAPI 不可用且用户未允许弱保护存储：拒绝把凭据写成 base64。"""


def _is_protected(value: str) -> bool:
    return value.startswith((_DPAPI_PREFIX, _B64_PREFIX))


def secrets_protection() -> str:
    """敏感字段当前的保护级别：dpapi / base64（弱）/ none（未配置）。"""
    raw = _raw_disk_settings()
    protected = [str(raw.get(key) or "") for key in ("sync_token", "ai_api_key")]
    protected = [value for value in protected if value]
    if not protected:
        return "none"
    if all(value.startswith(_DPAPI_PREFIX) for value in protected):
        return "dpapi"
    return "base64"


def dpapi_available() -> bool:
    """当前环境是否能用 DPAPI 保护敏感字段（探测结果会被缓存）。"""
    return _dpapi_crypt(b"probe", protect=True) is not None

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


def _obfuscate_token(value: str, allow_weak: bool = False) -> str:
    """落盘前保护 token/api key：优先 DPAPI 加密。

    DPAPI 不可用时默认**拒绝保存**并抛 :class:`InsufficientSecretProtection`：
    base64 只是混淆，任何能读文件的人都能还原，把凭据写成这样比"保存失败"更危险。
    需要在不支持 DPAPI 的环境（非 Windows / 被策略禁用）使用时，用户必须在设置里
    显式勾选 allow_weak_secret_storage。
    """
    raw = value.encode("utf-8")
    protected = _dpapi_crypt(raw, protect=True)
    if protected is not None:
        return _DPAPI_PREFIX + base64.urlsafe_b64encode(protected).decode("ascii")
    if _DPAPI_AVAILABLE is False and not allow_weak:
        raise InsufficientSecretProtection(
            "当前系统不支持 DPAPI（或已被安全策略禁用）。为避免同步令牌 / AI Key 以"
            "可解码文本落盘，本次未保存。确需在此环境使用，请在设置里勾选"
            "「允许弱保护存储（不推荐）」。"
        )
    return _B64_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")


def _raw_disk_settings() -> dict[str, Any]:
    """按原样读取 settings.json（不解密），用于安全地保留原密文。"""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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


class SettingsUnreadable(RuntimeError):
    """设置文件存在但无法解析：此时禁止回写。

    以前读取失败会静默回退默认值，随后任意一次后台保存（同步游标、设备列表）都会
    把整份配置和凭据覆盖成默认值——用户的同步令牌、AI Key、模块顺序全没了。
    文件损坏时宁可让保存失败并显式报错，也不能覆写原文件。
    """


_LOAD_FAILED = False


def load_settings() -> dict[str, Any]:
    global _SETTINGS_CACHE, _LOAD_FAILED
    try:
        stat = SETTINGS_PATH.stat()
        stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    if stamp is not None and _SETTINGS_CACHE is not None and _SETTINGS_CACHE[0] == stamp:
        # 命中缓存也要返回深拷贝：调用方（同步游标、设置界面）会就地改字段。
        return copy.deepcopy(_SETTINGS_CACHE[1])
    settings = dict(_DEFAULT_SETTINGS)
    if stamp is not None:
        try:
            loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("设置文件顶层不是对象")
            settings.update(loaded)
            _LOAD_FAILED = False
        except (OSError, ValueError):
            _LOAD_FAILED = True
            logger.exception(
                "读取设置文件失败，已回退默认值；为避免覆盖原配置，本次不会回写"
            )
    else:
        _LOAD_FAILED = False
    token = settings.get("sync_token")
    if token:
        raw_token = str(token)
        settings["sync_token"] = _deobfuscate_token(raw_token)
        if settings["sync_token"] or not _is_protected(raw_token):
            _DECRYPT_FAILED.discard("sync_token")
        else:
            _DECRYPT_FAILED.add("sync_token")
    ai_key = settings.get("ai_api_key")
    if ai_key:
        raw_key = str(ai_key)
        settings["ai_api_key"] = _deobfuscate_token(raw_key)
        if settings["ai_api_key"] or not _is_protected(raw_key):
            _DECRYPT_FAILED.discard("ai_api_key")
        else:
            _DECRYPT_FAILED.add("ai_api_key")
    if stamp is not None and not _LOAD_FAILED:
        _SETTINGS_CACHE = (stamp, copy.deepcopy(settings))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    global _SETTINGS_CACHE
    with _SETTINGS_LOCK:
        merged = load_settings()
        if _LOAD_FAILED:
            raise SettingsUnreadable(
                f"设置文件无法解析（{SETTINGS_PATH}），已拒绝覆盖保存；"
                "请修复或删除该文件后重试"
            )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw = _raw_disk_settings()
        merged.update(settings)
        allow_weak = bool(merged.get("allow_weak_secret_storage"))
        for key in ("sync_token", "ai_api_key"):
            if key in settings:
                # 调用方显式给了新值（包括清空）：按新值处理。
                merged[key] = (
                    _obfuscate_token(str(settings[key]), allow_weak=allow_weak)
                    if settings[key]
                    else ""
                )
            elif key in _DECRYPT_FAILED and raw.get(key):
                # 解密失败且本次没改这一项：保留磁盘上的原密文，否则一次后台
                # 保存游标就会把原机上还能用的密钥永久抹掉。
                merged[key] = str(raw[key])
            elif merged.get(key):
                merged[key] = _obfuscate_token(str(merged[key]), allow_weak=allow_weak)
            else:
                merged[key] = str(raw.get(key) or "")
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, SETTINGS_PATH)
        # 主动失效：同一 mtime 精度下连续写入可能拿到旧缓存。
        _SETTINGS_CACHE = None


def get_setting(key: str, default: Any = None) -> Any:
    return load_settings().get(key, default)
