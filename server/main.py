"""FastAPI 增量同步服务端：token 鉴权、用户隔离、最后写入获胜。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, MODEL_BY_TABLE, utcnow
from app.db.repo import apply_remote, max_updated_at, serialize

DB_PATH = Path(os.environ.get("DINGFENGBO_SERVER_DB", r"D:\Data\Dingfengbo\cloud.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base.metadata.create_all(engine)

app = FastAPI(title="定风波同步服务", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PullRequest(BaseModel):
    since: str | None = None


class PushRequest(BaseModel):
    changes: list[dict[str, Any]] = []


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
        for model in MODEL_BY_TABLE.values():
            stmt = select(model).where(model.owner_id == owner)
            if since:
                stmt = stmt.where(model.updated_at > since)
            changes.extend(serialize(item) for item in session.scalars(stmt.limit(1000)))
    return {"changes": changes, "server_time": utcnow().isoformat()}


@app.post("/api/sync/push")
def push(payload: PushRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    owner = owner_of(authorization)
    for change in payload.changes:
        data = change.get("data") or {}
        if data.get("owner_id") and data["owner_id"] != owner:
            raise HTTPException(status_code=403, detail="变更包含其他用户数据")
        data["owner_id"] = owner
    with SessionLocal() as session:
        accepted = apply_remote(session, payload.changes)
        session.commit()
    return {"accepted": accepted, "server_time": utcnow().isoformat()}


@app.get("/api/sync/check")
def check(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    owner = owner_of(authorization)
    with SessionLocal() as session:
        latest = max_updated_at(session, owner)
    return {"latest_updated_at": latest, "server_time": utcnow().isoformat()}
