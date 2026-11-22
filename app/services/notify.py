"""Windows 系统通知：优先原生 Toast，失败回退提示音。

不引入第三方依赖：Toast 走 PowerShell 的 WinRT 投影（PowerShell 5.1 自带），
用 Windows PowerShell 已注册的 AppUserModelID 发通知，通常无需额外安装步骤。
任何失败都静默降级为系统提示音，不影响主流程。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from enum import Enum

logger = logging.getLogger(__name__)


class NotifyResult(str, Enum):
    """提醒的送达程度：用来决定「要不要重试」。

    ``BEEP`` 表示 Toast 失败但提示音响了——用户已经被提醒到，重试只会重复打扰；
    只有 ``FAILED``（Toast 与提示音都没成功）才值得退避重试。
    """

    TOAST = "toast"
    BEEP = "beep"
    FAILED = "failed"

# Windows 已注册的 PowerShell AppID：直接拿来发 Toast 可避免「通知不显示」。
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_TEMPLATE = """
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode('{title}')) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode('{message}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def _escape(text: str, limit: int = 160) -> str:
    cleaned = (text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned.replace("'", "''")


def _powershell_toast(title: str, message: str) -> bool:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return False
    script = _TOAST_TEMPLATE.format(
        title=_escape(title), message=_escape(message), app_id=_APP_ID
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=15,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("Toast 调用失败", exc_info=True)
        return False
    if result.returncode != 0:
        logger.debug(
            "Toast 返回码 %s：%s",
            result.returncode,
            (result.stderr or b"").decode("utf-8", "replace")[:200],
        )
        return False
    return True


def _beep() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        return True
    except Exception:
        logger.debug("提示音播放失败", exc_info=True)
        return False


def notify(title: str, message: str) -> NotifyResult:
    """弹出系统通知，返回送达程度（Toast / 仅提示音 / 完全失败）。"""
    if sys.platform == "win32" and _powershell_toast(title, message):
        return NotifyResult.TOAST
    if _beep():
        return NotifyResult.BEEP
    return NotifyResult.FAILED
