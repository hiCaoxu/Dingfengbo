"""系统集成：开机自启（注册表 Run 项）与可选的系统托盘。

托盘依赖 ``pystray``：未安装时优雅降级为「关闭时最小化到任务栏」，
不影响应用启动；安装后关闭窗口会收入托盘，双击托盘图标恢复。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
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
    except FileNotFoundError as exc:
        logger.debug("is_autostart_enabled：忽略 %s", type(exc).__name__)
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
            except FileNotFoundError as exc:
                logger.debug("set_autostart：忽略 %s", type(exc).__name__)
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
    except Exception as exc:
        logger.info("托盘不可用，关闭窗口将直接退出：%s", type(exc).__name__)
        return False
    return True


_tray_icon = None
_tray_thread: threading.Thread | None = None
# pystray 的 run() 在 setup 阶段抛错（无 explorer/权限/无图标）时线程会直接退出。
# 必须把"线程已启动"和"真的跑起来了"区分开，否则主窗口会被隐藏且再也叫不回来。
_tray_failed = threading.Event()
# 托盘就绪：pystray 的 run(setup=...) 在图标真正可用时回调。没有这个信号时，
# 成功路径也只能靠「等满超时仍没失败」推断，每次关窗口都要白等一截。
_tray_ready = threading.Event()


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
    _tray_failed.clear()
    _tray_ready.clear()
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
        global _tray_icon
        try:
            _tray_icon.run(setup=lambda _icon: _tray_ready.set())
        except Exception:
            logger.exception("托盘线程异常")
            _tray_failed.set()
            _tray_icon = None
            # 窗口可能已经被隐藏：尽力把主窗口恢复出来，别让用户只能杀进程。
            try:
                _schedule(lambda: _show(page))
            except Exception:
                logger.debug("托盘失败后恢复窗口失败", exc_info=True)

    _tray_thread = threading.Thread(target=run, name="dingfengbo-tray", daemon=True)
    _tray_thread.start()
    return True


def tray_running(wait: float = 0.0) -> bool:
    """托盘是否真的可用。

    ``wait`` > 0 时最多等这么久，等到「就绪」或「失败」任一发生就返回——旧实现
    只在失败时被唤醒，成功路径每次都要白等满整个超时。
    """
    if _tray_icon is None:
        return False
    if wait > 0:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if _tray_failed.is_set():
                return False
            if _tray_ready.is_set():
                return True
            time.sleep(0.02)
    return _tray_icon is not None and not _tray_failed.is_set() and (
        _tray_ready.is_set() or wait <= 0
    )


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
