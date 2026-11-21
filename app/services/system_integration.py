"""系统集成：开机自启（注册表 Run 项）与可选的系统托盘。

托盘依赖 ``pystray``：未安装时优雅降级为「关闭时最小化到任务栏」，
不影响应用启动；安装后关闭窗口会收入托盘，双击托盘图标恢复。
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Dingfengbo"


def autostart_supported() -> bool:
    return sys.platform == "win32"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    main_py = Path(__file__).resolve().parents[2] / "main.py"
    executable = Path(sys.executable)
    windowed = executable.with_name("pythonw.exe")
    if windowed.exists():
        executable = windowed
    return f'"{executable}" "{main_py}"'


def is_autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _APP_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError:
        logger.debug("读取开机自启设置失败", exc_info=True)
        return False


def set_autostart(enabled: bool) -> tuple[bool, str]:
    """写入/删除开机自启项，返回（是否成功, 提示文案）。"""
    if not autostart_supported():
        return False, "开机自启仅支持 Windows"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _launch_command())
                return True, "已设置开机自启"
            try:
                winreg.DeleteValue(key, _APP_NAME)
            except FileNotFoundError:
                pass
            return True, "已关闭开机自启"
    except OSError as exc:
        logger.warning("设置开机自启失败：%s", exc)
        return False, f"设置失败：{exc}"


# ------------------------------------------------------------------ 托盘


def tray_available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


_tray_icon = None
_tray_thread: threading.Thread | None = None


def _icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill=(74, 124, 89, 255))
    draw.text((22, 18), "定", fill=(255, 255, 255, 255))
    return image


def start_tray(page, on_quit=None) -> bool:
    """启动托盘图标；已启动或依赖缺失返回 False。"""
    global _tray_icon, _tray_thread
    if _tray_icon is not None or not tray_available():
        return False
    try:
        import pystray
    except Exception:
        logger.info("pystray 不可用，跳过托盘")
        return False

    def show_window(icon=None, item=None):
        _schedule(lambda: _show(page))

    def sync_now(icon=None, item=None):
        _schedule(lambda: _sync(page))

    def quit_app(icon=None, item=None):
        if _tray_icon is not None:
            _tray_icon.stop()
        if on_quit is not None:
            _schedule(on_quit)

    menu = pystray.Menu(
        pystray.MenuItem("显示主窗口", show_window, default=True),
        pystray.MenuItem("立即同步", sync_now),
        pystray.MenuItem("退出", quit_app),
    )
    try:
        _tray_icon = pystray.Icon(_APP_NAME, _icon_image(), "定风波 · 个人工作台", menu)
    except Exception:
        logger.exception("创建托盘图标失败")
        _tray_icon = None
        return False

    def run() -> None:
        try:
            _tray_icon.run()
        except Exception:
            logger.exception("托盘线程异常")

    _tray_thread = threading.Thread(target=run, name="dingfengbo-tray", daemon=True)
    _tray_thread.start()
    return True


def tray_running() -> bool:
    return _tray_icon is not None


def stop_tray() -> None:
    global _tray_icon
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception:
            logger.debug("停止托盘失败", exc_info=True)
        _tray_icon = None


_loop = None


def bind_loop(loop) -> None:
    """由 UI 启动时注入 asyncio 事件循环，供托盘线程回主线程。"""
    global _loop
    _loop = loop


def _schedule(action) -> None:
    """从托盘线程回到 Flet 事件循环执行 UI 操作。"""
    import asyncio
    import inspect

    if _loop is None:
        logger.debug("事件循环未注入，忽略托盘操作")
        return
    try:
        result = action()
        if inspect.isawaitable(result):
            asyncio.run_coroutine_threadsafe(result, _loop)
    except Exception:
        logger.debug("托盘操作调度失败", exc_info=True)


async def _show(page) -> None:
    try:
        page.window.skip_task_bar = False
        page.window.visible = True
        page.window.to_front()
        page.update()
    except Exception:
        logger.debug("显示窗口失败", exc_info=True)


async def _sync(page) -> None:
    """托盘「立即同步」：网络请求必须在后台线程，协程里同步调用会冻结整个 UI。"""
    from .sync import sync_client

    def worker() -> None:
        try:
            sync_client.sync_once()
        except Exception:
            logger.exception("托盘同步失败")
            return

        async def apply() -> None:
            try:
                page.update()
            except Exception:
                logger.debug("托盘同步后刷新页面失败", exc_info=True)

        try:
            page.run_task(apply)
        except Exception:
            logger.debug("托盘同步结果回 UI 线程失败", exc_info=True)

    page.run_thread(worker)
