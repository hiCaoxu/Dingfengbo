"""本地优先增量同步客户端：启动/定时/写后触发。"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from typing import Any

import httpx

from ..config import DEFAULT_SERVER_URL, load_settings, save_settings
from ..db.models import utcnow
from ..db.repo import (
    apply_remote,
    mark_clean,
    mark_rejected,
    pending_changes,
    purge_deleted,
    record_superseded,
    sync_queue_stats,
)
from ..db.session import session_scope
from ..services.clock import set_clock_offset

logger = logging.getLogger(__name__)

# 自动同步暂停开关：恢复备份期间必须让所有同步路径（启动/周期/写后/手动）都停下。
# 只靠 UI 层"不要再启动新线程"不够——检查与应用之间还有窗口，正在跑的线程也可能
# 在库被整库替换后才提交。因此校验下沉到 sync_once 里，进入临界区前后各看一次。
_auto_pause = threading.Event()
_auto_pause_reason = ""


def pause_auto_sync(reason: str = "") -> None:
    global _auto_pause_reason
    _auto_pause_reason = reason
    _auto_pause.set()


def resume_auto_sync() -> None:
    global _auto_pause_reason
    _auto_pause_reason = ""
    _auto_pause.clear()


def auto_sync_paused() -> bool:
    return _auto_pause.is_set()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _remember_superseded(session, item: dict) -> None:
    """把「云端版本更新、本次推送被丢弃」写进本地冲突表，用户可在同步明细里核对。"""
    table = str(item.get("table") or "")
    record_id = str(item.get("id") or "")
    if not table or not record_id:
        return
    record_superseded(
        session,
        table,
        record_id,
        _parse_iso(item.get("local_updated_at")),
        _parse_iso(item.get("remote_updated_at")),
    )


class SyncClient:
    def __init__(self) -> None:
        self.status = "未连接"
        self.last_sync: datetime | None = None
        self.last_error = ""
        self.clock_offset = 0.0  # 服务端时间 - 本地时间，秒
        self.rejected_count = 0  # 最近一次 push 被服务端永久拒收的条数
        self.pending_upload = 0  # 最近一次 push 上传条数（同步明细页展示）
        self._lock = threading.Lock()

    def _endpoint(self, path: str) -> str:
        base = load_settings().get("server_url", DEFAULT_SERVER_URL).rstrip("/")
        return base + path

    def wait_idle(self, timeout: float = 15.0) -> bool:
        """等待进行中的同步结束，返回是否等到了空闲。

        恢复备份要先关连接池再整库替换，正好撞上后台 pull 就会写进已关闭的库、
        报 ``database is closed``。恢复与停机前先 ``wait_idle``，把「正在同步」
        等成「没有同步」再动手。
        """
        if self._lock.acquire(timeout=timeout):
            self._lock.release()
            return True
        logger.warning("等待同步结束超时（%.0f 秒），继续执行", timeout)
        return False

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {load_settings().get('sync_token', '')}"}

    @staticmethod
    def _cursor_key(settings: dict[str, Any]) -> str:
        server = str(settings.get("server_url", "")).rstrip("/")
        token = str(settings.get("sync_token", ""))
        return hashlib.sha256(f"{server}\n{token}".encode()).hexdigest()

    def _load_cursors(self, settings: dict[str, Any]) -> dict[str, dict[str, str]]:
        stored = settings.get("sync_cursors", {})
        if not isinstance(stored, dict):
            return {}
        cursors = stored.get(self._cursor_key(settings), {})
        if not isinstance(cursors, dict):
            return {}
        return {
            table: dict(cursor)
            for table, cursor in cursors.items()
            if isinstance(cursor, dict) and isinstance(cursor.get("sequence"), int)
        }

    def _save_cursors(
        self, settings: dict[str, Any], cursors: dict[str, dict[str, str]]
    ) -> None:
        latest = load_settings()
        stored = latest.get("sync_cursors", {})
        stored = dict(stored) if isinstance(stored, dict) else {}
        stored[self._cursor_key(settings)] = cursors
        save_settings({"sync_cursors": stored})

    def _observe_server_time(self, payload: dict[str, Any]) -> None:
        raw = payload.get("server_time")
        if not raw:
            return
        try:
            server_now = datetime.fromisoformat(str(raw))
        except ValueError:
            return
        self.clock_offset = (server_now - utcnow()).total_seconds()
        set_clock_offset(self.clock_offset)
        if abs(self.clock_offset) > 120:
            self.status = "本机时钟异常，请校准系统时间"
            logger.warning("时钟偏移 %.0f 秒，超过阈值", self.clock_offset)

    @staticmethod
    def selected_tables(settings: dict[str, Any]) -> set[str]:
        """选择性同步：设置里勾选的表；空列表表示全部。"""
        raw = settings.get("sync_tables") or []
        tables = {str(item) for item in raw if str(item)} if isinstance(raw, list) else set()
        return tables

    def _remember_device(self, settings: dict[str, Any]) -> None:
        """记录本机同步过的服务端（设备列表是本地记录，不上传）。"""
        latest = load_settings()
        devices = latest.get("sync_devices", {})
        devices = dict(devices) if isinstance(devices, dict) else {}
        key = self._cursor_key(settings)
        entry = dict(devices.get(key) or {})
        entry.update(
            {
                "server": str(settings.get("server_url", "")),
                "last_sync": datetime.now().isoformat(timespec="seconds"),
                "host": _hostname(),
            }
        )
        entry.setdefault("first_seen", entry["last_sync"])
        devices[key] = entry
        save_settings({"sync_devices": devices})

    def _remember_history(self, status: str) -> None:
        latest = load_settings()
        history = latest.get("sync_history", [])
        history = list(history) if isinstance(history, list) else []
        history.insert(
            0,
            {"time": datetime.now().isoformat(timespec="seconds"), "status": status},
        )
        save_settings({"sync_history": history[:30]})

    def _pull(self, client: httpx.Client, settings: dict[str, Any]) -> None:
        cursors = self._load_cursors(settings)
        selected = self.selected_tables(settings)
        while True:
            before = {table: dict(cursor) for table, cursor in cursors.items()}
            response = client.post(
                self._endpoint("/api/sync/pull"),
                json={"cursors": cursors, "limit": 200},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            self._observe_server_time(payload)
            raw_changes = payload.get("changes", [])
            remote_changes = raw_changes
            if selected:
                remote_changes = [
                    change for change in raw_changes
                    if str(change.get("table")) in selected
                ]
            with session_scope() as session:
                apply_remote(session, remote_changes, owner_id="local")
            updates = payload.get("cursors", {})
            if isinstance(updates, dict):
                # 选择性同步时不要推进未选表的游标，否则以后勾选回来会漏拉历史。
                if selected:
                    updates = {
                        table: cursor for table, cursor in updates.items() if table in selected
                    }
                cursors.update(updates)
            self._save_cursors(settings, cursors)
            if not payload.get("has_more", False):
                break
            # 选择性同步的过滤不应被误判成「分页无进展」：判定要看服务端原始返回。
            if not raw_changes:
                raise RuntimeError("同步服务返回了无效的分页状态")
            # 服务端按「任意表还有下一页」置 has_more。若被过滤掉的是未勾选的表，
            # 它的游标永远不会推进，has_more 就会一直为真——必须在这种情况下退出，
            # 否则同一页会被无限重拉（CPU/流量打满，且 sync 锁永不释放）。
            if all(
                (cursors.get(table) or {}).get("sequence")
                == (before.get(table) or {}).get("sequence")
                for table in cursors
            ):
                logger.warning(
                    "同步分页无进展（可能是未勾选的表积压超过一页），本轮回退退出"
                )
                break

    def _push(self, client: httpx.Client) -> None:
        selected = self.selected_tables(load_settings())
        with session_scope() as session:
            changes = pending_changes(session)
        if selected:
            changes = [change for change in changes if str(change.get("table")) in selected]
        self.pending_upload = len(changes)
        if not changes:
            return
        response = client.post(
            self._endpoint("/api/sync/push"),
            json={"changes": changes},
            headers=self._headers(),
        )
        response.raise_for_status()
        payload = response.json()
        self._observe_server_time(payload)
        accepted = payload.get("accepted", [])
        rejected = payload.get("rejected", []) or []
        superseded = payload.get("superseded", []) or []
        with session_scope() as session:
            mark_clean(session, accepted)
            # 每轮都复算，避免上一轮的被拒数一直挂在状态栏上。
            self.rejected_count = mark_rejected(session, rejected) if rejected else 0
            for item in superseded:
                _remember_superseded(session, item)
        if rejected:
            logger.warning("同步被拒 %d 条：%s", len(rejected), rejected[:3])
        if superseded:
            logger.warning("同步被云端更新版本覆盖 %d 条（本地改动已出队）", len(superseded))

    def sync_once(self) -> str:
        if not self._lock.acquire(blocking=False):
            return self.status
        try:
            if _auto_pause.is_set():
                self.status = f"同步已暂停（{_auto_pause_reason or '恢复备份中'}）"
                return self.status
            settings = load_settings()
            if not settings.get("sync_enabled", True):
                self.status = "同步已关闭"
                return self.status
            token = settings.get("sync_token", "").strip()
            if not token:
                self.status = "未配置令牌"
                return self.status
            try:
                with httpx.Client(timeout=10.0) as client:
                    self._pull(client, settings)
                    self._push(client)
                # 墓碑清理放在成功路径末尾：只 pull 不 push 的客户端也能清理已确认的墓碑。
                with session_scope() as session:
                    purge_deleted(session)
                now = datetime.now()
                self.last_sync = now
                self.last_error = ""
                self.status = (
                    "已同步 · 时钟异常" if abs(self.clock_offset) > 120 else f"已同步 {now:%H:%M}"
                )
                self._remember_device(settings)
                self._remember_history(self.status)
                logger.info("同步成功")
            except httpx.HTTPStatusError as exc:
                self.last_error = str(exc)[:120]
                code = exc.response.status_code
                if code in (401, 403):
                    self.status = "令牌无效"
                elif code >= 500:
                    self.status = "服务异常"
                else:
                    self.status = f"同步失败({code})"
                self._remember_history(self.status)
                logger.warning("同步请求被拒：%s", self.status)
            except httpx.RequestError as exc:  # 网络错误：断网不影响本地使用
                self.last_error = str(exc)[:120]
                self.status = "离线可用"
                self._remember_history(self.status)
                logger.info("网络不可达：%s", self.last_error)
            except Exception as exc:
                self.last_error = str(exc)[:120]
                self.status = "同步失败"
                self._remember_history(self.status)
                logger.exception("同步异常")
            return self.status
        finally:
            self._lock.release()

    def check(self) -> dict[str, Any]:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(self._endpoint("/api/sync/check"), headers=self._headers())
            response.raise_for_status()
            return response.json()


def _hostname() -> str:
    import socket

    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def sync_history() -> list[dict[str, Any]]:
    """最近的同步历史（新→旧），供同步明细页展示。"""
    history = load_settings().get("sync_history", [])
    return list(history) if isinstance(history, list) else []


def sync_devices() -> list[dict[str, Any]]:
    """本机记录过的服务端设备（本地记录，不上传）。"""
    devices = load_settings().get("sync_devices", {})
    if not isinstance(devices, dict):
        return []
    rows = []
    for key, entry in devices.items():
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "key": key[:8],
                "server": entry.get("server", ""),
                "host": entry.get("host", ""),
                "last_sync": entry.get("last_sync", ""),
                "first_seen": entry.get("first_seen", ""),
            }
        )
    rows.sort(key=lambda row: str(row["last_sync"]), reverse=True)
    return rows


def queue_counts() -> tuple[int, int]:
    """返回（待同步, 同步失败）记录数，供状态栏显示队列可见性。

    只读一次单条聚合 SQL；异常时返回 (0, 0)，不让状态栏刷新影响主流程。
    """
    try:
        with session_scope() as session:
            stats = sync_queue_stats(session)
    except Exception:
        logger.exception("读取同步队列统计失败")
        return 0, 0
    return stats.get("pending", 0), stats.get("error", 0)


def probe(server_url: str, token: str) -> str:
    """用尚未保存的配置探测同步服务，返回面向用户的结果文案。"""
    base = (server_url or DEFAULT_SERVER_URL).strip().rstrip("/")
    if not token.strip():
        return "请先填写同步令牌"
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{base}/api/sync/check",
                headers={"Authorization": f"Bearer {token.strip()}"},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            return "令牌无效（401），请核对同步令牌"
        return f"同步服务返回错误（{code}）"
    except httpx.RequestError as exc:
        logger.info("同步探测失败：%s", type(exc).__name__)
        return "网络不可达，请检查服务地址与网络"
    except Exception:
        logger.exception("同步探测异常")
        return "探测失败，详见日志"
    return "同步服务连接成功"


sync_client = SyncClient()


def mark_pending_write() -> None:
    """本地写操作后由 UI 调用，10 秒内触发一次同步。"""
    from ..ui.scheduler import schedule_sync

    schedule_sync(delay_ms=10_000)
