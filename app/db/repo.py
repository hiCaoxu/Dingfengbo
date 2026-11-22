"""通用仓储：本地优先写入、软删除与待同步队列。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete, func, literal, select, union_all, update as sql_update
from sqlalchemy.orm import Session

from ..services.clock import get_clock_offset
from .models import (
    MODEL_BY_TABLE,
    Base,
    Diary,
    HabitLog,
    SyncConflict,
    SyncIssue,
    SyncMixin,
    table_label,
    utcnow,
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def serialize(obj: SyncMixin) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in obj.__table__.columns:
        data[column.name] = _jsonable(getattr(obj, column.name))
    return {"table": obj.__tablename__, "data": data}


def mark_dirty(obj: SyncMixin) -> datetime:
    """把写入时间对齐到服务端时间轴，规避本机时钟回拨/快走导致的 LWW 误判。"""
    stamp = utcnow() + timedelta(seconds=get_clock_offset())
    obj.updated_at = stamp
    obj.sync_status = "pending"
    return stamp


def create(session: Session, model: type[SyncMixin], owner_id: str = "local", **fields: Any):
    obj = model(owner_id=owner_id, **fields)
    stamp = mark_dirty(obj)
    # created_at 必须和 updated_at 用同一个（已对齐的）时钟：它原先走模型默认值
    # = 裸 UTC，而 updated_at 叠了时钟偏移，两者会差出一次请求的往返时间，
    # 出现「更新时间早于创建时间」的倒挂（实测差 1~2 秒）。
    if getattr(obj, "created_at", None) is None:
        obj.created_at = stamp
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


def retry_issue(
    session: Session, table_name: str, record_id: str, owner_id: str = "local"
) -> bool:
    """单条被拒记录放回待同步队列（同步明细页用）。"""
    model = MODEL_BY_TABLE.get(table_name)
    if model is None:
        return False
    result = session.execute(
        sql_update(model)
        .where(
            model.id == str(record_id),
            model.owner_id == owner_id,
            model.sync_status == "error",
        )
        .values(sync_status="pending")
        .execution_options(synchronize_session=False)
    )
    session.execute(
        delete(SyncIssue).where(
            SyncIssue.table_name == table_name, SyncIssue.record_id == str(record_id)
        )
    )
    return bool(result.rowcount)


def ignore_issue(
    session: Session, table_name: str, record_id: str, owner_id: str = "local"
) -> bool:
    """忽略单条被拒记录：标记为 clean 并删除明细，不再重推。"""
    model = MODEL_BY_TABLE.get(table_name)
    if model is None:
        return False
    result = session.execute(
        sql_update(model)
        .where(
            model.id == str(record_id),
            model.owner_id == owner_id,
            model.sync_status == "error",
        )
        .values(sync_status="clean")
        .execution_options(synchronize_session=False)
    )
    session.execute(
        delete(SyncIssue).where(
            SyncIssue.table_name == table_name, SyncIssue.record_id == str(record_id)
        )
    )
    return bool(result.rowcount)


def list_error_records(session: Session, owner_id: str = "local", limit: int = 100) -> list[dict]:
    """列出同步失败（error）记录：表名、ID、标题与最近拒收原因。

    标题尽量取业务字段，让「同步明细」页能直接看懂是哪一条，而不是一串 UUID。
    """
    issues = {
        (issue.table_name, issue.record_id): issue
        for issue in session.scalars(select(SyncIssue))
    }
    rows: list[dict] = []
    for model in MODEL_BY_TABLE.values():
        table = model.__tablename__
        for obj in session.scalars(
            select(model)
            .where(model.owner_id == owner_id, model.sync_status == "error")
            .limit(limit)
        ):
            title = ""
            for field in ("title", "name", "food", "kind"):
                if hasattr(obj, field):
                    title = str(getattr(obj, field) or "")
                    if title:
                        break
            issue = issues.get((table, obj.id))
            rows.append(
                {
                    "table": table,
                    "table_label": table_label(table),
                    "id": obj.id,
                    "title": title or obj.id[:8],
                    "reason": issue.reason if issue else "",
                    "created_at": issue.created_at if issue else obj.updated_at,
                }
            )
    rows.sort(key=lambda row: row["created_at"] or datetime.min, reverse=True)
    return rows


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

# 落库时不允许由载荷决定的字段：同步元数据由各自一侧（客户端队列 / 服务端序列）
# 单独维护。服务端若把客户端带上的 sync_seq 抄进被合并的行，会打乱全局序列；
# 客户端若把服务端序列抄回来也没有意义（游标只用服务端返回值）。
_NOT_COPIED_FIELDS = {"sync_seq", "sync_status"}

# 业务键合并：两个客户端各自新建的同一天记录 id 不同，但业务上是同一行。
# Diary 按日期唯一；HabitLog 按 (习惯, 日期) 唯一（「同天只保留一条」的承诺）。
_MERGE_KEYS: dict[type, tuple[str, ...]] = {
    Diary: ("date",),
    HabitLog: ("habit_id", "date"),
}


def _find_merge_target(session: Session, model, owner: str, data: dict[str, Any]):
    """按业务键找本地等价行；该模型不参与合并或键缺失时返回 None。"""
    keys = _MERGE_KEYS.get(model)
    if not keys:
        return None
    conditions = [model.owner_id == owner]
    for key in keys:
        raw = data.get(key)
        if not raw:
            return None
        column = getattr(model, key)
        if key == "date":
            try:
                conditions.append(column == date.fromisoformat(str(raw)[:10]))
            except ValueError:
                return None
        else:
            conditions.append(column == str(raw))
    return session.scalar(
        # 优先复用未删除的行：历史墓碑可能和活跃行共享同一个业务键。
        select(model).where(*conditions).order_by(model.is_deleted, model.updated_at.desc())
    )


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
    """最后写入获胜；桌面端将远端租户 ID 规范化为本地所有者。

    返回的每条都会带上 ``record_id``（**实际落库行**的主键）。业务键合并时本地会
    保留自己的主键，而载荷里的 ``id`` 是远端 id；服务端要按 ``record_id`` 分配
    ``sync_seq``，否则合并后的行拿不到新序列号，别的设备永远拉不到这次修改。

    ``superseded=True`` 表示"本地（服务端）已有更新版本，本次推送被丢弃"——
    调用方必须把它回执给客户端，否则那条记录会在客户端永久停留在待同步队列。
    """
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
        merged = False
        if obj is None:
            obj = _find_merge_target(session, model, owner, data)
            merged = obj is not None
        if obj is None:
            obj = model()
            session.add(obj)
        elif obj.owner_id != owner:
            continue
        if obj.updated_at and obj.updated_at >= remote_time:
            if obj.updated_at == remote_time and _same_version(obj, model, data):
                applied.append(
                    {"table": table, "data": data, "changed": False, "record_id": obj.id}
                )
            elif obj.updated_at > remote_time:
                # 远端版本更旧：本地获胜，但留下冲突记录供用户核对，并回执让客户端出队。
                _record_conflict(session, str(table), record_id, obj.updated_at, remote_time)
                applied.append(
                    {
                        "table": table,
                        "data": data,
                        "changed": False,
                        "superseded": True,
                        "record_id": obj.id,
                        "local_updated_at": obj.updated_at,
                        "remote_updated_at": remote_time,
                    }
                )
            continue
        for column in model.__table__.columns:
            # 按业务键（日期 / 习惯+日期）合并出的本地行保留本地主键，避免被远端 id 改写行身份。
            if column.name == "id" and merged:
                continue
            if column.name in _NOT_COPIED_FIELDS:
                continue
            if column.name in data:
                setattr(obj, column.name, _parse(column, data[column.name]))
        obj.sync_status = "clean"
        applied.append(
            {"table": table, "data": data, "changed": True, "record_id": obj.id}
        )
    session.flush()
    return applied


def _record_conflict(
    session: Session,
    table_name: str,
    record_id: str,
    local_time: datetime | None,
    remote_time: datetime | None,
) -> None:
    row = session.scalar(
        select(SyncConflict).where(
            SyncConflict.table_name == table_name, SyncConflict.record_id == record_id
        )
    )
    if row is None:
        row = SyncConflict(table_name=table_name, record_id=record_id)
        session.add(row)
    row.local_updated_at = local_time
    row.remote_updated_at = remote_time
    row.created_at = utcnow()


def record_superseded(
    session: Session,
    table_name: str,
    record_id: str,
    server_time: datetime | None,
    pushed_time: datetime | None,
) -> None:
    """记一次「推送被云端更新版本覆盖」，供同步明细的冲突页展示。

    被覆盖的记录已由调用方从待同步队列出队，这里只负责留痕，避免用户以为
    自己的改动"同步成功了"却看不到任何提示。
    """
    _record_conflict(session, str(table_name), str(record_id), server_time, pushed_time)


def list_conflicts(session: Session, limit: int = 50) -> list[dict]:
    """最近的同步冲突（本地获胜的记录，新→旧）。"""
    rows = session.scalars(
        select(SyncConflict).order_by(SyncConflict.created_at.desc()).limit(limit)
    )
    result: list[dict] = []
    for row in rows:
        title = ""
        model = MODEL_BY_TABLE.get(row.table_name)
        if model is not None:
            obj = session.get(model, row.record_id)
            if obj is not None:
                for field in ("title", "name", "food", "kind"):
                    if hasattr(obj, field):
                        title = str(getattr(obj, field) or "")
                        if title:
                            break
        result.append(
            {
                "table": row.table_name,
                "table_label": table_label(row.table_name),
                "id": row.record_id,
                "title": title or row.record_id[:8],
                "local": row.local_updated_at,
                "remote": row.remote_updated_at,
                "created_at": row.created_at,
            }
        )
    return result


def clear_conflicts(session: Session) -> int:
    result = session.execute(delete(SyncConflict))
    return int(result.rowcount or 0)


def max_updated_at(session: Session, owner_id: str = "local") -> str | None:
    """返回本地最大更新时间，用于 pull 的 since 参数。

    每条表下推一条 ``MAX(updated_at)``（UNION ALL 合并），而不是把该租户所有行的
    时间戳取回 Python 再求最大值——`/api/sync/check` 是最高频的探活接口，
    数据量增长后全表搬运会直接拖慢"测试连接"。
    """
    statements = [
        select(func.max(model.updated_at).label("latest")).where(model.owner_id == owner_id)
        for model in MODEL_BY_TABLE.values()
    ]
    latest: datetime | None = None
    for value in session.execute(union_all(*statements)).scalars():
        if value is not None and (latest is None or value > latest):
            latest = value
    return latest.isoformat() if latest else None


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
