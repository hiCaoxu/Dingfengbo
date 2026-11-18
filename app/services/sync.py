"""本地优先增量同步客户端：启动/定时/写后触发。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from ..config import load_settings, save_settings
from ..db.repo import apply_remote, mark_clean, max_updated_at, pending_changes, purge_deleted
from ..db.session import session_scope


class SyncClient:
    def __init__(self) -> None:
        self.status = "未连接"
        self.last_sync: datetime | None = None
        self.last_error = ""

    def _endpoint(self, path: str) -> str:
        base = load_settings().get("server_url", "http://127.0.0.1:8000").rstrip("/")
        return base + path

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {load_settings().get('sync_token', '')}"}

    def sync_once(self) -> str:
        settings = load_settings()
        if not settings.get("sync_enabled", True):
            self.status = "同步已关闭"
            return self.status
        token = settings.get("sync_token", "").strip()
        if not token:
            self.status = "未配置令牌"
            return self.status
        try:
            with session_scope() as session:
                since = max_updated_at(session)
                with httpx.Client(timeout=6.0) as client:
                    pulled = client.post(
                        self._endpoint("/api/sync/pull"),
                        json={"since": since},
                        headers=self._headers(),
                    )
                    pulled.raise_for_status()
                    remote_changes = pulled.json().get("changes", [])
                    apply_remote(session, remote_changes)

                    changes = pending_changes(session)
                    pushed = client.post(
                        self._endpoint("/api/sync/push"),
                        json={"changes": changes},
                        headers=self._headers(),
                    )
                    pushed.raise_for_status()
                    accepted = pushed.json().get("accepted", [])
                    mark_clean(session, accepted)
                    purge_deleted(session)
            self.status = f"已同步 {datetime.now():%H:%M}"
            self.last_sync = datetime.now()
            self.last_error = ""
        except Exception as exc:  # 断网不影响本地使用
            self.last_error = str(exc)[:120]
            self.status = "离线可用"
        return self.status

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
