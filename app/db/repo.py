"""通用仓储：本地优先写入、软删除与待同步队列。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from sqlalchemy import select, update as sql_update
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


def mark_clean(
    session: Session, changes: Iterable[dict], owner_id: str = "local"
) -> None:
    """只确认本次上传的版本，避免覆盖同步期间产生的新编辑。"""
    for change in changes:
        model = MODEL_BY_TABLE.get(change.get("table"))
        data = change.get("data") or {}
        record_id = data.get("id")
        updated_at = data.get("updated_at")
        if model is None or not record_id or not updated_at:
            continue
        try:
            version = datetime.fromisoformat(str(updated_at))
        except ValueError:
            continue
        stmt = (
            sql_update(model)
            .where(
                model.id == str(record_id),
                model.owner_id == owner_id,
                model.sync_status == "pending",
                model.updated_at == version,
            )
            .values(sync_status="clean")
            .execution_options(synchronize_session=False)
        )
        session.execute(stmt)


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


_VERSION_AGNOSTIC_FIELDS = {"owner_id", "sync_status", "sync_seq"}


def _same_version(obj: SyncMixin, model, data: dict[str, Any]) -> bool:
    """比较业务字段，忽略归属与同步元数据，用于幂等推送确认。"""
    for column in model.__table__.columns:
        if column.name in _VERSION_AGNOSTIC_FIELDS or column.name not in data:
            continue
        try:
            expected = _parse(column, data[column.name])
        except (TypeError, ValueError):
            return False
        if getattr(obj, column.name) != expected:
            return False
    return True


def apply_remote(
    session: Session, changes: Iterable[dict], owner_id: str | None = None
) -> list[dict]:
    """最后写入获胜；桌面端将远端租户 ID 规范化为本地所有者。"""
    applied: list[dict] = []
    for change in changes:
        table = change.get("table")
        data = dict(change.get("data") or {})
        model = MODEL_BY_TABLE.get(table)
        if model is None or not data.get("id"):
            continue
        if owner_id is not None:
            data["owner_id"] = owner_id
        remote_time = datetime.fromisoformat(data["updated_at"])
        owner = str(data.get("owner_id", "local"))
        record_id = str(data["id"])
        obj = session.get(model, record_id)
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
            if obj.updated_at == remote_time and _same_version(obj, model, data):
                applied.append({"table": table, "data": data, "changed": False})
            continue
        for column in model.__table__.columns:
            if column.name in data:
                setattr(obj, column.name, _parse(column, data[column.name]))
        obj.sync_status = "clean"
        applied.append({"table": table, "data": data, "changed": True})
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
