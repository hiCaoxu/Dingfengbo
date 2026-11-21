"""SQLite 架构初始化与一次性 UUID 主键迁移。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, Integer, func, inspect, select, text, update

from .models import SYNC_MODELS, Base

logger = logging.getLogger(__name__)


def sequence_value(connection: Connection) -> int:
    """读取全局同步序列当前值，供部署后核对。"""
    if "sync_sequences" not in set(inspect(connection).get_table_names()):
        return 0
    return _sync_sequence_value(connection)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _legacy_value(column, value: Any) -> Any:
    if value is None:
        return None
    python_type = column.type.python_type
    if python_type is datetime:
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if python_type is date:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if python_type is bool:
        return bool(value)
    return value


def _uses_integer_ids(connection: Connection, table: str) -> bool:
    columns = inspect(connection).get_columns(table)
    id_column = next(column for column in columns if column["name"] == "id")
    return isinstance(id_column["type"], Integer)


def _sync_sequence_value(connection: Connection) -> int:
    return int(connection.scalar(text("SELECT value FROM sync_sequences WHERE id = 1")) or 0)


def _assign_missing_sequences(connection: Connection) -> None:
    """锚定全局序列计数器，并让缺号补偿也从同一计数器领取。

    升级存量库时各行带着旧方案分配的表内序列号（可能已达数百），若计数器仍从 0
    起步，新推送的记录会拿到比存量游标更小的号，存量客户端 ``sync_seq > cursor``
    将永久漏拉。因此先把计数器顶到全表最大 ``sync_seq``，再让缺号行从计数器领取，
    保证「全局单调且永不重用」这一游标语义根基。
    """
    connection.execute(
        text("INSERT INTO sync_sequences(id, value) VALUES(1, 0) ON CONFLICT(id) DO NOTHING")
    )
    max_seq = 0
    for model in SYNC_MODELS:
        found = connection.scalar(select(func.max(model.sync_seq))) or 0
        max_seq = max(max_seq, int(found))
    if max_seq:
        connection.execute(
            text("UPDATE sync_sequences SET value = :value WHERE id = 1 AND value < :value"),
            {"value": max_seq},
        )

    for model in SYNC_MODELS:
        ids = connection.scalars(
            select(model.id).where(model.sync_seq == 0).order_by(model.updated_at, model.id)
        ).all()
        if not ids:
            continue
        hi = int(
            connection.execute(
                text("UPDATE sync_sequences SET value = value + :n RETURNING value"),
                {"n": len(ids)},
            ).scalar_one()
        )
        start = hi - len(ids) + 1
        for offset, record_id in enumerate(ids):
            connection.execute(
                update(model).where(model.id == record_id).values(sync_seq=start + offset)
            )
    logger.info("同步序列已初始化：当前全局序列号 %s", _sync_sequence_value(connection))


def _column_default_sql(column) -> str | None:
    """把模型里的 Python 侧默认值转成 ALTER TABLE 可用的 SQL 字面量。"""
    default = getattr(column, "default", None)
    value = getattr(default, "arg", None) if default is not None else None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _add_missing_columns(connection: Connection) -> None:
    """给存量库补上模型新增的列（create_all 只会建缺失的表，不会改已有表）。

    SQLite 的 ADD COLUMN 带 DEFAULT 时会把已有行填成该默认值，因此升级后老数据
    立刻满足「非空 + 合理默认」，不需要额外的数据回填脚本。
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.tables.values():
        if table.name not in existing_tables:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }
        for column in table.columns:
            if column.name in existing_columns:
                continue
            ddl_type = column.type.compile(dialect=connection.dialect)
            default_sql = _column_default_sql(column)
            clause = (
                f"ALTER TABLE {_quote(table.name)} "
                f"ADD COLUMN {_quote(column.name)} {ddl_type}"
            )
            if default_sql is not None:
                clause += f" DEFAULT {default_sql}"
            connection.execute(text(clause))
            logger.info("已为存量库补充列：%s.%s", table.name, column.name)


def initialize_schema(engine: Engine) -> None:
    """创建新表；旧版整数主键库在一个事务内迁移为 UUID。"""
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        legacy_tables = [
            model.__tablename__
            for model in SYNC_MODELS
            if model.__tablename__ in existing
            and _uses_integer_ids(connection, model.__tablename__)
        ]
        if not legacy_tables:
            Base.metadata.create_all(connection)
            _add_missing_columns(connection)
            _assign_missing_sequences(connection)
            return
        if "transactions" in legacy_tables and "accounts" not in legacy_tables:
            raise RuntimeError("无法迁移交易引用：账户表未使用旧版整数主键")

        for table in legacy_tables:
            for index in inspect(connection).get_indexes(table):
                name = index.get("name")
                if name and not name.startswith("sqlite_autoindex_"):
                    connection.execute(text(f"DROP INDEX IF EXISTS {_quote(name)}"))
            legacy_name = f"_legacy_uuid_{table}"
            connection.execute(
                text(f"ALTER TABLE {_quote(table)} RENAME TO {_quote(legacy_name)}")
            )

        Base.metadata.create_all(connection)
        account_ids: dict[tuple[str, int], str] = {}
        for model in SYNC_MODELS:
            table = model.__tablename__
            if table not in legacy_tables:
                continue
            legacy_name = f"_legacy_uuid_{table}"
            new_table = model.__table__
            rows = connection.execute(text(f"SELECT * FROM {_quote(legacy_name)}")).mappings()
            for row in rows:
                owner = str(row["owner_id"] or "local")
                identity = f"dingfengbo:{table}:{row['id']}:{row['created_at']}"
                new_id = str(uuid5(NAMESPACE_URL, identity))
                if table == "accounts":
                    account_ids[(owner, row["id"])] = new_id
                values = {
                    column.name: _legacy_value(column, row[column.name])
                    for column in new_table.columns
                    if column.name != "id" and column.name in row
                }
                values["id"] = new_id
                if table == "transactions":
                    old_account_id = values["account_id"]
                    values["account_id"] = account_ids.get(
                        (owner, old_account_id), str(old_account_id)
                    )
                    old_to_id = values.get("to_account_id")
                    if old_to_id is not None:
                        values["to_account_id"] = account_ids.get(
                            (owner, old_to_id), str(old_to_id)
                        )
                connection.execute(new_table.insert().values(**values))

        for table in legacy_tables:
            connection.execute(text(f"DROP TABLE {_quote(f'_legacy_uuid_{table}')}"))
        _add_missing_columns(connection)
        _assign_missing_sequences(connection)
