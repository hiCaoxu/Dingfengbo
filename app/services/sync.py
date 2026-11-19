"""本地优先增量同步客户端：启动/定时/写后触发。"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime
from typing import Any

import httpx

from ..config import load_settings, save_settings
from ..db.repo import apply_remote, mark_clean, pending_changes, purge_deleted
from ..db.session import session_scope


class SyncClient:
    def __init__(self) -> None:
        self.status = "未连接"
        self.last_sync: datetime | None = None
        self.last_error = ""
        self._lock = threading.Lock()

    def _endpoint(self, path: str) -> str:
        base = load_settings().get("server_url", "http://127.0.0.1:8000").rstrip("/")
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
        accepted = response.json().get("accepted", [])
        with session_scope() as session:
            mark_clean(session, accepted)
            purge_deleted(session)

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
                now = datetime.now()
                self.status = f"已同步 {now:%H:%M}"
                self.last_sync = now
                self.last_error = ""
            except Exception as exc:  # 断网不影响本地使用
                self.last_error = str(exc)[:120]
                self.status = "离线可用"
            return self.status
        finally:
            self._lock.release()

    def check(self) -> dict[str, Any]:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(self._endpoint("/api/sync/check"), headers=self._headers())
            response.raise_for_status()
            return response.json()


sync_client = SyncClient()


def mark_pending_write() -> None:
    """本地写操作后由 UI 调用，10 秒内触发一次同步。"""
    from ..ui.scheduler import schedule_sync

    schedule_sync(delay_ms=10_000)
