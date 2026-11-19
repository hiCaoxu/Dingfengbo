"""FastAPI 增量同步服务端：token 鉴权、用户隔离、最后写入获胜。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.migrations import initialize_schema
from app.db.models import MODEL_BY_TABLE, utcnow
from app.db.repo import apply_remote, max_updated_at, serialize

DB_PATH = Path(os.environ.get("DINGFENGBO_SERVER_DB", r"D:\Data\Dingfengbo\cloud.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
initialize_schema(engine)

app = FastAPI(title="定风波同步服务", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PullCursor(BaseModel):
    sequence: int = Field(ge=0)


class PullRequest(BaseModel):
    cursors: dict[str, PullCursor] = Field(default_factory=dict)
    limit: int = Field(default=200, ge=1, le=500)
    since: str | None = None  # 兼容旧客户端


class PushRequest(BaseModel):
    changes: list[dict[str, Any]] = Field(default_factory=list)


def owner_of(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="缺少同步令牌")
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="同步令牌无效")
    # 以令牌作为用户隔离键；部署时可在前面接入用户表换成真实 user_id。
    return token[:64]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": utcnow().isoformat()}


@app.post("/api/sync/pull")
def pull(payload: PullRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    owner = owner_of(authorization)
    since = None
    if payload.since:
        try:
            since = datetime.fromisoformat(payload.since)
        except ValueError:
            raise HTTPException(status_code=400, detail="since 格式应为 ISO 时间")
    with SessionLocal() as session:
        changes: list[dict[str, Any]] = []
        next_cursors: dict[str, dict[str, int]] = {}
        has_more = False
        for table, model in MODEL_BY_TABLE.items():
            stmt = select(model).where(model.owner_id == owner)
            cursor = payload.cursors.get(table)
            if cursor:
                stmt = stmt.where(model.sync_seq > cursor.sequence)
            elif since:
                stmt = stmt.where(model.updated_at > since)
            rows = list(
                session.scalars(stmt.order_by(model.sync_seq).limit(payload.limit + 1))
            )
            if len(rows) > payload.limit:
                has_more = True
                rows = rows[: payload.limit]
            if rows:
                last = rows[-1]
                next_cursors[table] = {"sequence": last.sync_seq}
                changes.extend(serialize(item) for item in rows)
    return {
        "changes": changes,
        "cursors": next_cursors,
        "has_more": has_more,
        "server_time": utcnow().isoformat(),
    }


@app.post("/api/sync/push")
def push(payload: PushRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    owner = owner_of(authorization)
    for change in payload.changes:
        data = change.setdefault("data", {})
        data["owner_id"] = owner
    with SessionLocal() as session:
        accepted = apply_remote(session, payload.changes)
        next_sequences: dict[str, int] = {}
        for change in accepted:
            table = change.get("table")
            model = MODEL_BY_TABLE.get(table)
            record_id = change.get("data", {}).get("id")
            if model is None or not record_id:
                continue
            obj = session.get(model, str(record_id))
            if obj is None:
                continue
            if change.get("changed"):
                if table not in next_sequences:
                    next_sequences[table] = session.scalar(select(func.max(model.sync_seq))) or 0
                next_sequences[table] += 1
                obj.sync_seq = next_sequences[table]
            change["data"]["sync_seq"] = obj.sync_seq
        session.commit()
    return {"accepted": accepted, "server_time": utcnow().isoformat()}


@app.get("/api/sync/check")
def check(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    owner = owner_of(authorization)
    with SessionLocal() as session:
        latest = max_updated_at(session, owner)
    return {"latest_updated_at": latest, "server_time": utcnow().isoformat()}
