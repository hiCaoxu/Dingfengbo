"""SQLite 架构初始化与一次性 UUID 主键迁移。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, Integer, func, inspect, select, text, update

from .models import SYNC_MODELS, Base


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


def _assign_missing_sequences(connection: Connection) -> None:
    for model in SYNC_MODELS:
        sequence = connection.scalar(select(func.max(model.sync_seq))) or 0
        ids = connection.scalars(
            select(model.id).where(model.sync_seq == 0).order_by(model.updated_at, model.id)
        ).all()
        for record_id in ids:
            sequence += 1
            connection.execute(
                update(model).where(model.id == record_id).values(sync_seq=sequence)
            )


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
        _assign_missing_sequences(connection)
