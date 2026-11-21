"""通用仓储：本地优先写入、软删除与待同步队列。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, func, literal, select, union_all, update as sql_update
from sqlalchemy.orm import Session

from ..services.clock import get_clock_offset
from .models import MODEL_BY_TABLE, Base, Diary, SyncIssue, SyncMixin, utcnow


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
    # 把写入时间对齐到服务端时间轴，规避本机时钟回拨/快走导致的 LWW 误判。
    obj.updated_at = utcnow() + timedelta(seconds=get_clock_offset())
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
        # 该记录已成功上传，历史上可能残留的拒收原因不再有意义。
        session.execute(
            delete(SyncIssue).where(
                SyncIssue.table_name == str(change.get("table")),
                SyncIssue.record_id == str(record_id),
            )
        )


def mark_rejected(
    session: Session, rejected: Iterable[dict], owner_id: str = "local"
) -> int:
    """被服务端永久拒收的记录标记为 error 并退出待同步队列，避免无限重推。

    ``mark_dirty`` 会把重新编辑的记录重置回 pending，因此用户改动该记录后自愈；
    同时把拒收原因写入 ``sync_issues``，让设置页能展示「哪张表、哪条、为什么」。
    """
    marked = 0
    for item in rejected:
        table_name = str(item.get("table") or "")
        model = MODEL_BY_TABLE.get(table_name)
        record_id = item.get("id")
        if model is None or not record_id:
            continue
        result = session.execute(
            sql_update(model)
            .where(
                model.id == str(record_id),
                model.owner_id == owner_id,
                model.sync_status == "pending",
            )
            .values(sync_status="error")
            .execution_options(synchronize_session=False)
        )
        if not result.rowcount:
            continue
        marked += 1
        _record_issue(session, table_name, str(record_id), str(item.get("reason") or ""))
    return marked


def _record_issue(session: Session, table_name: str, record_id: str, reason: str) -> None:
    issue = session.get(SyncIssue, (table_name, record_id))
    if issue is None:
        session.add(SyncIssue(table_name=table_name, record_id=record_id, reason=reason))
    else:
        issue.reason = reason
        issue.created_at = utcnow()
    session.flush()


def list_sync_issues(session: Session, limit: int = 50) -> list[SyncIssue]:
    """最近的拒收明细（新→旧），供设置页展示。"""
    return list(
        session.scalars(
            select(SyncIssue).order_by(SyncIssue.created_at.desc()).limit(limit)
        )
    )


def retry_rejected(session: Session, owner_id: str = "local") -> int:
    """把所有 error 记录放回待同步队列，并清空对应的拒收明细。

    error 记录不会自行复活（用户没编辑），所以需要显式重置；若载荷仍然非法，
    下一轮 push 会再次被拒并重新写入明细，不会造成静默循环。
    """
    restored = 0
    for model in MODEL_BY_TABLE.values():
        table_name = model.__tablename__
        ids = session.scalars(
            select(model.id).where(
                model.owner_id == owner_id, model.sync_status == "error"
            )
        ).all()
        if not ids:
            continue
        session.execute(
            sql_update(model)
            .where(model.owner_id == owner_id, model.sync_status == "error")
            .values(sync_status="pending")
            .execution_options(synchronize_session=False)
        )
        restored += len(ids)
        for record_id in ids:
            session.execute(
                delete(SyncIssue).where(
                    SyncIssue.table_name == table_name,
                    SyncIssue.record_id == str(record_id),
                )
            )
    return restored


def sync_queue_stats(session: Session, owner_id: str = "local") -> dict[str, int]:
    """统计各同步状态的记录数（待同步/被拒/已同步）。

    用一条 ``UNION ALL`` 汇总十张表，而不是逐表发十条 SQL：状态栏会周期性调用，
    往返次数必须与表数量脱钩。
    """
    statements = [
        select(
            literal(model.__tablename__).label("table_name"),
            model.sync_status.label("status"),
            func.count().label("total"),
        )
        .where(model.owner_id == owner_id)
        .group_by(model.sync_status)
        for model in MODEL_BY_TABLE.values()
    ]
    stats: dict[str, int] = {}
    for _table, status, total in session.execute(union_all(*statements)):
        key = str(status or "pending")
        stats[key] = stats.get(key, 0) + int(total or 0)
    stats.setdefault("pending", 0)
    stats.setdefault("error", 0)
    return stats


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
        merged_by_date = False
        if obj is None and model is Diary and data.get("date"):
            obj = session.scalar(
                select(Diary).where(Diary.owner_id == owner, Diary.date == date.fromisoformat(data["date"][:10]))
            )
            merged_by_date = obj is not None
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
            # 按 (owner,date) 合并出的本地行保留本地主键，避免被远端 id 改写行身份。
            if column.name == "id" and merged_by_date:
                continue
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


TOMBSTONE_RETENTION_DAYS = 30


def purge_deleted(session: Session, retention_days: int = TOMBSTONE_RETENTION_DAYS) -> int:
    """同步完成后物理清理已删除且已同步的墓碑。

    保留 ``retention_days`` 天的恢复窗口（误删后仍可恢复），过期后才真正删除。
    """
    cutoff = utcnow() - timedelta(days=retention_days)
    count = 0
    for model in MODEL_BY_TABLE.values():
        stmt = select(model).where(
            model.is_deleted.is_(True),
            model.sync_status == "clean",
            model.updated_at < cutoff,
        )
        for obj in session.scalars(stmt):
            session.delete(obj)
            count += 1
    session.flush()
    return count
