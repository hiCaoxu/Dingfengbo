"""FastAPI 增量同步服务端：token 鉴权、用户隔离、最后写入获胜。"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.db.engine import create_sqlite_engine
from app.db.migrations import initialize_schema, sequence_value
from app.db.models import MODEL_BY_TABLE, utcnow
from app.db.repo import apply_remote, max_updated_at, serialize
from app.logging_setup import setup_logging

DB_PATH = Path(os.environ.get("DINGFENGBO_SERVER_DB", r"D:\Data\Dingfengbo\cloud.db"))
setup_logging(DB_PATH.parent / "logs")
logger = logging.getLogger(__name__)

engine = create_sqlite_engine(DB_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
initialize_schema(engine)
with engine.connect() as _connection:
    # 部署后肉眼可核对：该值应不小于各表 MAX(sync_seq)，否则存量客户端会漏拉。
    logger.info("同步序列当前值：%s", sequence_value(_connection))

app = FastAPI(title="定风波同步服务", version="1.0.0")
# 桌面客户端为原生 HTTP（不受 CORS 限制），默认不放行任何浏览器来源；
# 如需浏览器访问，通过环境变量 DINGFENGBO_CORS_ORIGINS 显式配置（逗号分隔）。
_cors_origins = [
    origin.strip()
    for origin in os.environ.get("DINGFENGBO_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 图片落盘目录：与数据库同级的 files/（可用 DINGFENGBO_SERVER_FILES 覆盖）。
# 清理策略：上传只增不删——文件名是内容摘要，可能仍被多端记录的图片引用，
# 服务端无法判断「已无人引用」，因此不做自动删除。磁盘占用需人工巡检：
# 定期备份 data 卷后，可按修改时间清理半年前的 *.jpg/png/webp 并接受旧图 404。
FILES_DIR = Path(os.environ.get("DINGFENGBO_SERVER_FILES", DB_PATH.parent / "files"))
FILES_DIR.mkdir(parents=True, exist_ok=True)


class _ImmutableStaticFiles(StaticFiles):
    """图片文件名就是内容摘要，内容永不改变，可以放心长缓存。

    不加这个头时每次打开日记/笔记都要回源取一遍封面，图片多了明显变慢。
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# 文件名是内容摘要，本身不可猜，直接静态暴露，客户端拿到的就是可长期引用的链接。
app.mount("/files", _ImmutableStaticFiles(directory=str(FILES_DIR)), name="files")


class PullCursor(BaseModel):
    sequence: int = Field(ge=0)


class PullRequest(BaseModel):
    cursors: dict[str, PullCursor] = Field(default_factory=dict)
    limit: int = Field(default=200, ge=1, le=500)
    since: str | None = None  # 兼容旧客户端


class Change(BaseModel):
    """push 单条变更：只做最小校验，单条失败不影响整批。"""

    table: str
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("table")
    @classmethod
    def _known_table(cls, value: str) -> str:
        if value not in MODEL_BY_TABLE:
            raise ValueError(f"未知数据表: {value}")
        return value

    @field_validator("data")
    @classmethod
    def _valid_record(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value.get("id"):
            raise ValueError("记录缺少 id")
        if "updated_at" not in value:
            raise ValueError("记录缺少 updated_at")
        try:
            datetime.fromisoformat(str(value["updated_at"]))
        except (TypeError, ValueError):
            raise ValueError("updated_at 格式错误")
        return value


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
    # 身份键与存储分离：DB 里只存不可逆摘要，库文件泄露不再等于凭证泄露。
    return hashlib.sha256(f"dingfengbo-v1\0{token}".encode()).hexdigest()


def next_sequences(session, n: int) -> list[int]:
    """一个事务内原子领取 n 个全局序列号（单个 UPDATE 自增，规避读-改-写竞态）。"""
    if n <= 0:
        return []
    session.execute(
        text("INSERT INTO sync_sequences(id, value) VALUES(1, 0) ON CONFLICT(id) DO NOTHING")
    )
    hi = int(
        session.execute(
            text("UPDATE sync_sequences SET value = value + :n RETURNING value"), {"n": n}
        ).scalar_one()
    )
    return list(range(hi - n + 1, hi + 1))


# 内存滑动窗口限流：按 owner（token 摘要）限制 push 频率，防单 token 刷爆服务端。
_PUSH_RATE_LIMIT = 60  # 每窗口最多 push 次数
_PUSH_WINDOW_SECONDS = 60.0
_push_rate_lock = threading.Lock()
_push_rate_log: dict[str, deque[float]] = defaultdict(deque)


def _enforce_rate_limit(owner: str) -> None:
    now = time.monotonic()
    with _push_rate_lock:
        window = _push_rate_log[owner]
        while window and now - window[0] > _PUSH_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= _PUSH_RATE_LIMIT:
            logger.warning("push 限流触发：owner=%s", owner)
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        window.append(now)
        if len(_push_rate_log) > 1000:
            _push_rate_log.clear()


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
    _enforce_rate_limit(owner)
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in payload.changes:
        try:
            change = Change.model_validate(raw)
        except ValidationError as exc:
            errors = exc.errors()
            reason = errors[0].get("msg", "无效变更") if errors else "无效变更"
            # 回传 table/id：客户端据此把该记录标为 error 退出待同步队列，避免无限重推。
            record = raw.get("data") if isinstance(raw, dict) else None
            record = record if isinstance(record, dict) else {}
            rejected.append(
                {
                    "table": raw.get("table") if isinstance(raw, dict) else None,
                    "id": record.get("id"),
                    "reason": str(reason),
                }
            )
            logger.warning(
                "拒收变更：table=%s id=%s reason=%s",
                rejected[-1]["table"], rejected[-1]["id"], reason,
            )
            continue
        data = dict(change.data)
        data["owner_id"] = owner
        valid.append({"table": change.table, "data": data})

    with SessionLocal() as session:
        accepted = apply_remote(session, valid)
        changed = [
            c for c in accepted
            if c.get("changed") and c.get("table") and c.get("data", {}).get("id")
        ]
        sequences = next_sequences(session, len(changed))
        for change, seq in zip(changed, sequences):
            model = MODEL_BY_TABLE.get(change.get("table"))
            obj = session.get(model, str(change.get("data", {}).get("id"))) if model else None
            if obj is not None:
                obj.sync_seq = seq
        # 回填 sync_seq 供客户端 mark_clean 精确确认（幂等推送返回现存序列号）。
        for change in accepted:
            model = MODEL_BY_TABLE.get(change.get("table"))
            record_id = change.get("data", {}).get("id")
            obj = session.get(model, str(record_id)) if model and record_id else None
            if obj is not None:
                change["data"]["sync_seq"] = obj.sync_seq
        session.commit()
    logger.info("push 完成：accepted=%d rejected=%d", len(accepted), len(rejected))
    return {"accepted": accepted, "rejected": rejected, "server_time": utcnow().isoformat()}


@app.get("/api/sync/check")
def check(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    owner = owner_of(authorization)
    with SessionLocal() as session:
        latest = max_updated_at(session, owner)
    return {"latest_updated_at": latest, "server_time": utcnow().isoformat()}


# ---------------------------------------------------------------- 图片上传
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
# 只放行真正的图片（后缀可伪造，按文件头判断），否则服务器会变成任意文件仓库。
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def _image_suffix(data: bytes) -> str:
    """按文件头识别图片格式，返回后缀；不是图片返回空串。"""
    for magic, suffix in _IMAGE_MAGIC:
        if data.startswith(magic):
            return suffix
    # WEBP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def _public_base(request: Request) -> str:
    """对外可访问的站点前缀；反向代理/自定义域名时用环境变量覆盖。"""
    configured = os.environ.get("DINGFENGBO_SERVER_PUBLIC_URL", "").strip().rstrip("/")
    return configured or str(request.base_url).rstrip("/")


@app.post("/api/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """接收客户端压缩后的图片，按内容摘要落盘，返回可直接引用的 URL。

    摘要去重：同一张图重复上传只占一份空间，地址保持稳定。
    """
    owner = owner_of(authorization)
    # 先看声明的长度：超大请求体等到 read() 才发现，已经被完整接收/落盘了一遍。
    # 留 4KB 余量给 multipart 边界与头部。
    declared = request.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + 4096:
        raise HTTPException(status_code=413, detail="图片过大（超过 12MB）")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片过大（超过 12MB）")
    suffix = _image_suffix(data)
    if not suffix:
        raise HTTPException(status_code=400, detail="只支持 JPG/PNG/GIF/WEBP/BMP 图片")

    digest = hashlib.sha256(data).hexdigest()
    name = f"{owner[:12]}-{digest[:24]}{suffix}"
    target = FILES_DIR / name
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    logger.info("上传图片：%s（%d 字节）", name, len(data))
    return {"url": f"{_public_base(request)}/files/{name}", "name": name}
