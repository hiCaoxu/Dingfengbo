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

logger = logging.getLogger(__name__)

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


def _beep() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        logger.debug("提示音播放失败", exc_info=True)


def notify(title: str, message: str) -> bool:
    """弹出系统通知；返回是否成功走了 Toast（否则至少响了一声）。"""
    if sys.platform == "win32" and _powershell_toast(title, message):
        return True
    _beep()
    return False
