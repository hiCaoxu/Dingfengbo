"""自动保存状态机：版本化快照 + 串行写库 + 失败重排队。

问题背景：日记/笔记的「待保存内容 + 定时器」是模块级可变全局，UI 线程和 Timer 线程
会同时动它。旧实现存在这个交错：Timer 线程读走旧快照 → UI 线程写入新值并注册新
定时器 → Timer 线程把新 pending 清空、把新定时器取消 → **只把旧值写进库**，新输入
永久丢失。

这里的纪律：

- 一把锁只保护「pending / timer / 版本号」这三个字段，**绝不持锁写库**；
- 每次调度捕获**不可变快照**（调用方在 UI 线程里取好字段值，不要在 Timer 里读控件）；
- Timer 回调先比对版本，只有「仍是当前版本」的那一次才落库；
- 写库在单独的串行锁里执行，失败时**仅当期间没有更新的版本**才把快照放回队列；
- ``flush_now`` 用于切页/blur/整页重建前，``discard`` 用于目标已删除时丢弃。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

@dataclass
class _Pending:
    version: int
    key: str
    snapshot: Any
    writer: Callable[[str, Any], None]


_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_pending: _Pending | None = None
_timer: threading.Timer | None = None
_version = 0


def schedule(key: str, snapshot: Any, writer: Callable[[str, Any], None],
             delay_ms: int) -> None:
    """登记一次待保存快照并重置定时器（只动状态，不写库）。"""
    global _pending, _timer, _version
    with _LOCK:
        _version += 1
        version = _version
        _pending = _Pending(version=version, key=key, snapshot=snapshot, writer=writer)
        if _timer is not None:
            _timer.cancel()
        timer = threading.Timer(delay_ms / 1000, _fire, (version,))
        timer.daemon = True
        _timer = timer
    timer.start()


def _fire(version: int) -> None:
    """定时器回调：只有仍是当前版本的那次快照才落库。"""
    global _pending, _timer
    with _LOCK:
        pending = _pending
        if pending is None or pending.version != version:
            return
        _pending = None
        _timer = None
    _write(pending)


def _write(pending: _Pending) -> bool:
    """串行写库；失败且没有更新的输入时把快照放回队列等下次重试。"""
    try:
        with _WRITE_LOCK:
            pending.writer(pending.key, pending.snapshot)
        return True
    except Exception:
        logger.exception("自动保存失败：key=%s", pending.key)
        global _pending, _version
        with _LOCK:
            current = _pending
            if current is None or current.version <= pending.version:
                # 没有更新的输入：保留这份快照，等下一次 flush_now / 新编辑重试。
                _version += 1
                _pending = _Pending(
                    version=_version,
                    key=pending.key,
                    snapshot=pending.snapshot,
                    writer=pending.writer,
                )
        return False


def flush_now() -> bool:
    """立即落库当前待保存快照；没有待保存内容时返回 True。"""
    global _pending, _timer
    with _LOCK:
        pending, _pending = _pending, None
        timer, _timer = _timer, None
    if timer is not None:
        timer.cancel()
    if pending is None:
        return True
    return _write(pending)


def discard() -> None:
    """丢弃待保存内容（目标已删除 / 用户明确放弃）。"""
    global _pending, _timer
    with _LOCK:
        _pending = None
        timer, _timer = _timer, None
    if timer is not None:
        timer.cancel()


def has_pending() -> bool:
    with _LOCK:
        return _pending is not None


def pending_snapshot(key: str | None = None) -> Any:
    """取当前待保存快照（只读，不动状态）；``key`` 不匹配时返回 None。

    整页重建时用它把「刚输入、定时器还没触发」的内容显示回来。
    """
    with _LOCK:
        pending = _pending
    if pending is None or (key is not None and pending.key != key):
        return None
    return pending.snapshot


def reset_for_tests() -> None:
    """清空状态（仅供测试）。"""
    global _pending, _timer, _version
    with _LOCK:
        _pending = None
        _version = 0
        timer, _timer = _timer, None
    if timer is not None:
        timer.cancel()
