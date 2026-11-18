"""通用仓储：本地优先写入、软删除与待同步队列。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MODEL_BY_TABLE, Base, Diary, SyncMixin, utcnow


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def serialize(obj: SyncMixin) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in obj.__table__.columns:
        data[column.name] = _jsonable(getattr(obj, column.name))
    return {"table": obj.__tablename__, "data": data}


def mark_dirty(obj: SyncMixin) -> None:
    obj.updated_at = utcnow()
    obj.sync_status = "pending"


def create(session: Session, model: type[SyncMixin], owner_id: str = "local", **fields: Any):
    obj = model(owner_id=owner_id, **fields)
    mark_dirty(obj)
    session.add(obj)
    session.flush()
    return obj


def update(session: Session, obj: SyncMixin, **fields: Any):
    for key, value in fields.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
    mark_dirty(obj)
    session.flush()
    return obj


def soft_delete(session: Session, obj: SyncMixin) -> None:
    update(session, obj, is_deleted=True)


def active(session: Session, model: type[SyncMixin], owner_id: str = "local") -> list:
    stmt = select(model).where(model.owner_id == owner_id, model.is_deleted.is_(False))
    return list(session.scalars(stmt))


def pending_changes(session: Session, owner_id: str = "local", limit: int = 500) -> list[dict]:
    rows: list[dict] = []
    for model in MODEL_BY_TABLE.values():
        stmt = (
            select(model)
            .where(model.owner_id == owner_id, model.sync_status == "pending")
            .limit(limit)
        )
        rows.extend(serialize(item) for item in session.scalars(stmt))
    return rows


def mark_clean(session: Session, changes: Iterable[dict]) -> None:
    for change in changes:
        model = MODEL_BY_TABLE.get(change.get("table"))
        if model is None:
            continue
        record_id = change.get("data", {}).get("id")
        obj = session.get(model, record_id)
        if obj is not None and obj.owner_id == change.get("data", {}).get("owner_id", obj.owner_id):
            obj.sync_status = "clean"


def _parse(column, value: Any) -> Any:
    if value is None:
        return None
    py_type = column.type.python_type
    if py_type is datetime:
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if py_type is date:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if py_type is bool:
        return value if isinstance(value, bool) else bool(value)
    if py_type is float:
        return float(value)
    if py_type is int:
        return int(value)
    return value


def apply_remote(session: Session, changes: Iterable[dict]) -> list[dict]:
    """最后写入获胜：仅当远端 updated_at 更新时才覆盖本地，返回被接受的变更。"""
    applied: list[dict] = []
    for change in changes:
        table = change.get("table")
        data = change.get("data") or {}
        model = MODEL_BY_TABLE.get(table)
        if model is None or not data.get("id"):
            continue
        remote_time = datetime.fromisoformat(data["updated_at"])
        owner = str(data.get("owner_id", "local"))
        obj = session.get(model, int(data["id"]))
        if obj is None and model is Diary and data.get("date"):
            obj = session.scalar(
                select(Diary).where(Diary.owner_id == owner, Diary.date == date.fromisoformat(data["date"][:10]))
            )
        if obj is None:
            obj = model()
            session.add(obj)
        elif obj.owner_id != owner:
            continue
        if obj.updated_at and obj.updated_at >= remote_time:
            continue
        for column in model.__table__.columns:
            if column.name in data:
                setattr(obj, column.name, _parse(column, data[column.name]))
        obj.sync_status = "clean"
        applied.append(change)
    session.flush()
    return applied


def max_updated_at(session: Session, owner_id: str = "local") -> str | None:
    """返回本地最大更新时间，用于 pull 的 since 参数。"""
    values: list[datetime] = []
    for model in MODEL_BY_TABLE.values():
        stmt = select(model.updated_at).where(model.owner_id == owner_id)
        found = session.scalars(stmt).all()
        values.extend(v for v in found if v)
    return max(values).isoformat() if values else None


def purge_deleted(session: Session) -> int:
    """同步完成后物理清理已删除且已同步的记录。"""
    count = 0
    for model in MODEL_BY_TABLE.values():
        stmt = select(model).where(model.is_deleted.is_(True), model.sync_status == "clean")
        for obj in session.scalars(stmt):
            session.delete(obj)
            count += 1
    session.flush()
    return count
