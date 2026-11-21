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
from ..db.repo import apply_remote, mark_clean, mark_rejected, pending_changes, purge_deleted
from ..db.session import session_scope
from ..services.clock import set_clock_offset

logger = logging.getLogger(__name__)


class SyncClient:
    def __init__(self) -> None:
        self.status = "未连接"
        self.last_sync: datetime | None = None
        self.last_error = ""
        self.clock_offset = 0.0  # 服务端时间 - 本地时间，秒
        self.rejected_count = 0  # 最近一次 push 被服务端永久拒收的条数
        self._lock = threading.Lock()

    def _endpoint(self, path: str) -> str:
        base = load_settings().get("server_url", DEFAULT_SERVER_URL).rstrip("/")
        return base + path

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

    def _pull(self, client: httpx.Client, settings: dict[str, Any]) -> None:
        cursors = self._load_cursors(settings)
        while True:
            response = client.post(
                self._endpoint("/api/sync/pull"),
                json={"cursors": cursors, "limit": 200},
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
            self._observe_server_time(payload)
            remote_changes = payload.get("changes", [])
            with session_scope() as session:
                apply_remote(session, remote_changes, owner_id="local")
            updates = payload.get("cursors", {})
            if isinstance(updates, dict):
                cursors.update(updates)
            self._save_cursors(settings, cursors)
            if not payload.get("has_more", False):
                break
            if not remote_changes:
                raise RuntimeError("同步服务返回了无效的分页状态")

    def _push(self, client: httpx.Client) -> None:
        with session_scope() as session:
            changes = pending_changes(session)
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
        with session_scope() as session:
            mark_clean(session, accepted)
            if rejected:
                self.rejected_count = mark_rejected(session, rejected)
        if rejected:
            logger.warning("同步被拒 %d 条：%s", len(rejected), rejected[:3])

    def sync_once(self) -> str:
        if not self._lock.acquire(blocking=False):
            return self.status
        try:
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
                logger.warning("同步请求被拒：%s", self.status)
            except httpx.RequestError as exc:  # 网络错误：断网不影响本地使用
                self.last_error = str(exc)[:120]
                self.status = "离线可用"
                logger.info("网络不可达：%s", self.last_error)
            except Exception as exc:
                self.last_error = str(exc)[:120]
                self.status = "同步失败"
                logger.exception("同步异常")
            return self.status
        finally:
            self._lock.release()

    def check(self) -> dict[str, Any]:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(self._endpoint("/api/sync/check"), headers=self._headers())
            response.raise_for_status()
            return response.json()


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
