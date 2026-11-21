"""SQLite 引擎工厂：带 WAL + busy_timeout，供客户端与服务端复用。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event


def create_sqlite_engine(path: Path) -> Engine:
    """创建带 WAL + busy_timeout 的 SQLite 引擎。

    WAL 让读不阻塞写、写不阻塞读；busy_timeout 使瞬时写锁冲突等待而非直接报
    ``database is locked``（UI 线程写入与同步线程 apply_remote 并发时尤其重要）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 15},
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
