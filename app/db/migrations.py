"""SQLite 架构初始化与一次性 UUID 主键迁移。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, Integer, func, inspect, select, text, update

from .models import SYNC_MODELS, Base, Diary, column_fill_value, utcnow

logger = logging.getLogger(__name__)

# 库级单位/口径迁移的版本号，记录在 SQLite 自带的 ``PRAGMA user_version``：
#   1 = 金额改为整数分（元 → 分）
#   2 = 修正 created_at / updated_at 的历史倒挂（两者曾用不同的时钟）
# 只看数值无法区分「100 元」和「100 分」，必须有显式版本标记，否则重复换算就是灾难。
MONEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("transactions", "amount"),
    ("accounts", "initial_balance"),
    ("budgets", "amount"),
    ("recurring_rules", "amount"),
)
MONEY_SCHEMA_VERSION = 1
TIMESTAMP_SCHEMA_VERSION = 2
TEMPLATE_FLAG_SCHEMA_VERSION = 3
TODO_FIELD_SCHEMA_VERSION = 4
DIARY_UNIQUE_SCHEMA_VERSION = 5
INDEX_SCHEMA_VERSION = 6
# 7 = 按模型定义重建「列可为 NULL」的表（补齐 NOT NULL），并把历史 NULL 回填成默认值
NULLABLE_SCHEMA_VERSION = 7

# 只有本地意义的表：它们没有 owner_id / sync_seq，不该建同步复合索引。
_LOCAL_ONLY_TABLES = ("foods", "sync_sequences", "sync_issues", "sync_conflicts", "todo_reminders")


def _drop_zombie_indexes(connection: Connection) -> None:
    """清掉历史遗留的「本地表同步索引」（常量表达式索引）。

    旧版 ``_ensure_indexes`` 遍历了全部元数据表，对本地表也执行 CREATE INDEX；SQLite
    的双引号宽松解析把它当成字符串常量而不是列名，于是静默生成了
    ``ix_foods_owner_sync_seq`` 这类常量索引。真实库里沉淀了 10 个，这里清理一次。
    """
    if _schema_version(connection) >= INDEX_SCHEMA_VERSION:
        return
    existing = set(inspect(connection).get_table_names())
    dropped = 0
    for table in _LOCAL_ONLY_TABLES:
        if table not in existing:
            continue
        # 先把 PRAGMA 结果取完再执行 DDL：边遍历结果集边改 schema 会报 table is locked。
        names = {
            str(row[1])
            for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")')
        }
        for template, _first, _second in _COMPOSITE_INDEXES:
            name = template % table
            if name in names:
                connection.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')
                dropped += 1
    connection.exec_driver_sql(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")
    if dropped:
        logger.info("已清理 %d 个本地表僵尸索引", dropped)


def _nullable_offenders(inspector, table) -> list:
    """模型声明非空、但库里这一列允许 NULL 的列。"""
    actual = {item["name"]: item for item in inspector.get_columns(table.name)}
    return [
        column
        for column in table.columns
        if not column.nullable and actual.get(column.name, {}).get("nullable")
    ]


def _rebuild_table_strict(connection: Connection, table) -> None:
    """按模型定义重建单表（补上 NOT NULL），数据原样搬过去。

    与日记表重建同一套路：先把旧表改名，删掉被 RENAME 带过来的命名索引（否则新建
    同名表时 CREATE INDEX 会撞名），再用模型 DDL 建新表、回灌数据、删旧表。
    模型里没有任何外键，因此重建单表不会牵扯级联约束。
    """
    legacy = f"__nullable_{table.name}"
    connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{legacy}"')
    connection.exec_driver_sql(f'ALTER TABLE "{table.name}" RENAME TO "{legacy}"')
    # 先取完 PRAGMA 结果再执行 DDL：边遍历结果集边改 schema 会报 table is locked。
    stale_indexes = [
        str(row[1])
        for row in connection.exec_driver_sql(f'PRAGMA index_list("{legacy}")')
        if str(row[3]) == "c"
    ]
    for name in stale_indexes:
        connection.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')
    table.create(connection)
    old_columns = {item["name"] for item in inspect(connection).get_columns(legacy)}
    # 逐列生成取值表达式：旧表有的列用 COALESCE（NULL 回填默认值），旧表没有的列
    # （比如更早版本还没有 sync_seq）直接给默认值——新表这些列是 NOT NULL，
    # 缺列又没默认值会直接 IntegrityError。
    target = ", ".join(f'"{column.name}"' for column in table.columns)
    selections: list[str] = []
    parameters: list[Any] = []
    for column in table.columns:
        if column.name in old_columns:
            selections.append(f'COALESCE("{column.name}", ?)')
        else:
            selections.append("?")
        parameters.append(column_fill_value(column))
    connection.exec_driver_sql(
        f'INSERT INTO "{table.name}" ({target}) SELECT {", ".join(selections)} FROM "{legacy}"',
        tuple(parameters),
    )
    connection.exec_driver_sql(f'DROP TABLE "{legacy}"')


def _normalize_nullable_columns(connection: Connection, *, fresh: bool = False) -> None:
    """把「模型非空、库里可空」的列补齐（历史云端库就是这种形态）。

    这些列在本地新库上是 NOT NULL，但云端的 ``cloud.db`` 由更早版本的模型创建，
    SQLite 的 ``ALTER TABLE ADD COLUMN`` 又没法后补 NOT NULL，于是同一张表在两端
    DDL 不一致：云端可以存 NULL，客户端拉下来写进非空列直接 IntegrityError。
    这里按模型定义重建受影响的表，并把历史 NULL 回填成模型默认值。

    ``fresh`` 表示库是空的、刚刚由 ``create_all`` 按当前模型建出来——这种情况下
    不可能有可空偏差，直接打版本号即可，省掉每张表一轮 PRAGMA 检查
    （测试里每个用例都会建一次库，这一圈扫描很显眼）。
    """
    if _schema_version(connection) >= NULLABLE_SCHEMA_VERSION:
        return
    if fresh:
        connection.exec_driver_sql(f"PRAGMA user_version = {NULLABLE_SCHEMA_VERSION}")
        return
    # 快速路径：先比「DDL 里 NOT NULL 的个数」与模型要求的个数。建表语句本身就是
    # 模型生成的，个数一致就说明没有列漏掉约束；只有对不上才逐列 PRAGMA 检查。
    # 这条路径让每个用例的建库开销保持在一两次查询的量级。
    ddl = {
        str(name): (sql or "")
        for name, sql in connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        ).all()
    }
    inspector = inspect(connection)
    existing = set(ddl)
    rebuilt = 0
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        expected = sum(1 for column in table.columns if not column.nullable)
        if ddl[table.name].count("NOT NULL") == expected:
            continue
        offenders = _nullable_offenders(inspector, table)
        if not offenders:
            continue
        for column in offenders:
            fill = column_fill_value(column)
            connection.exec_driver_sql(
                f'UPDATE "{table.name}" SET "{column.name}" = ? WHERE "{column.name}" IS NULL',
                (fill,),
            )
        # 逐表记一笔：迁移后核对「哪几张表被重建、补了哪些列」全靠这行。
        logger.debug(
            "重建 %s：补齐 NOT NULL（%s）",
            table.name,
            "、".join(column.name for column in offenders),
        )
        _rebuild_table_strict(connection, table)
        rebuilt += 1
    connection.exec_driver_sql(f"PRAGMA user_version = {NULLABLE_SCHEMA_VERSION}")
    if rebuilt:
        logger.info("已按模型定义重建 %d 张表（补齐非空约束并回填 NULL）", rebuilt)

# 历史上任务弹窗直接存了中文优先级，而象限推导/搜索只认英文码。
_PRIORITY_CODES = (("高", "high"), ("中", "medium"), ("低", "low"))


def _schema_version(connection: Connection) -> int:
    return int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def _migrate_money_to_cents(connection: Connection) -> None:
    """把历史「元」的浮点金额就地换算成整数分（幂等，只执行一次）。

    必须在任何 ORM 写入之前运行：``MoneyCents`` 类型装饰器会在写入时把元乘 100，
    如果先写入再迁移，这批数据会被乘两次。
    """
    if _schema_version(connection) >= MONEY_SCHEMA_VERSION:
        return
    existing = set(inspect(connection).get_table_names())
    converted = 0
    for table, column in MONEY_COLUMNS:
        if table not in existing:
            continue
        columns = {item["name"] for item in inspect(connection).get_columns(table)}
        if column not in columns:
            continue
        result = connection.exec_driver_sql(
            f'UPDATE "{table}" SET "{column}" = ROUND("{column}" * 100)'
        )
        converted += result.rowcount or 0
    connection.exec_driver_sql(f"PRAGMA user_version = {MONEY_SCHEMA_VERSION}")
    if converted:
        logger.info("金额已迁移为整数分：%d 行", converted)


def _normalize_timestamp_inversions(connection: Connection) -> None:
    """修正 ``updated_at < created_at`` 的历史倒挂。

    旧实现里 created_at 走模型默认的裸 UTC，updated_at 却叠加了同步时钟偏移
    （含一次请求往返），同一行会出现「更新早于创建」。新写入已改为同钟，这里把
    存量行按 ``updated_at = created_at`` 归一；不改内容、不改同步状态，
    下次 pull 若云端版本更「新」，LWW 会用同样的内容覆盖，不会丢数据。
    """
    if _schema_version(connection) >= TIMESTAMP_SCHEMA_VERSION:
        return
    existing = set(inspect(connection).get_table_names())
    fixed = 0
    for model in SYNC_MODELS:
        table = model.__tablename__
        if table not in existing:
            continue
        result = connection.exec_driver_sql(
            f'UPDATE "{table}" SET "updated_at" = "created_at" '
            f'WHERE "updated_at" < "created_at"'
        )
        fixed += result.rowcount or 0
    connection.exec_driver_sql(f"PRAGMA user_version = {TIMESTAMP_SCHEMA_VERSION}")
    if fixed:
        logger.info("已修正 %d 条时间倒挂（updated_at 对齐 created_at）", fixed)


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
    # 用 next(..., None) 而不是裸 next：没有 id 列的表（理论上不该出现，但外部
    # 手工建的表可能有）会抛 StopIteration，在迁移事务里表现成一次不明原因的回滚。
    id_column = next((column for column in columns if column["name"] == "id"), None)
    return id_column is not None and isinstance(id_column["type"], Integer)


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


_COMPOSITE_INDEXES = (
    ("ix_%s_owner_sync_seq", "owner_id", "sync_seq"),
    ("ix_%s_owner_sync_status", "owner_id", "sync_status"),
    # /api/sync/check 与旧客户端 since 探测都是 MAX(updated_at) WHERE owner_id=?：
    # 没有这个复合索引时 SQLite 只能倒扫 updated_at 再逐行核对 owner，多租户下会
    # 随着别的租户写入量线性变慢。
    ("ix_%s_owner_updated_at", "owner_id", "updated_at"),
)


def _ensure_indexes(connection: Connection) -> None:
    """给存量库补复合索引，并收敛历史重复打卡后建唯一索引。

    ``create_all`` 只建缺失的表，不会给已存在的表补索引；升级后的老库需要这一步
    才能拿到与新建库一致的索引。习惯打卡的重复行必须先收敛，否则唯一索引建不上。

    只遍历 ``SYNC_MODELS``：早期实现遍历全部元数据表，对没有 ``owner_id``/``sync_seq``
    的本地表（foods / sync_sequences / sync_issues / sync_conflicts / todo_reminders）
    也执行 CREATE INDEX。SQLite 的双引号宽松解析（DQS）把它当成字符串常量，于是静默
    生成了 10 个「常量表达式」僵尸索引——schema 污染，且在关闭 DQS 的环境里会让库
    直接打不开。
    """
    existing = set(inspect(connection).get_table_names())
    for model in SYNC_MODELS:
        table = model.__tablename__
        if table not in existing:
            continue
        for template, first, second in _COMPOSITE_INDEXES:
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {_quote(template % table)} "
                    f"ON {_quote(table)} ({_quote(first)}, {_quote(second)})"
                )
            )
    if "habit_logs" not in existing:
        return
    duplicates = connection.execute(
        text(
            "SELECT owner_id, habit_id, date, COUNT(*) AS n FROM habit_logs "
            "GROUP BY owner_id, habit_id, date HAVING n > 1"
        )
    ).all()
    for owner_id, habit_id, day, _count in duplicates:
        keep = connection.execute(
            text(
                "SELECT id FROM habit_logs WHERE owner_id = :o AND habit_id = :h "
                "AND date = :d AND is_deleted = 0 "
                "ORDER BY updated_at DESC, id DESC LIMIT 1"
            ),
            {"o": owner_id, "h": habit_id, "d": day},
        ).scalar()
        if keep is None:
            continue
        connection.execute(
            text(
                "UPDATE habit_logs SET is_deleted = 1, sync_status = 'pending' "
                "WHERE owner_id = :o AND habit_id = :h AND date = :d "
                "AND is_deleted = 0 AND id != :keep"
            ),
            {"o": owner_id, "h": habit_id, "d": day, "keep": keep},
        )
        logger.info("已收敛历史重复打卡：habit=%s date=%s", habit_id, day)
    connection.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_habit_logs_active "
            "ON habit_logs (owner_id, habit_id, date) WHERE is_deleted = 0"
        )
    )


def _repair_template_flags(connection: Connection) -> None:
    """修正历史：``is_template=1`` 但 ``completed=0`` 的待办。

    「完成循环任务」会把这一期标成 is_template 以从列表里隐去，但旧实现「取消完成」
    时没有复位，于是这一行既不算未完成、又在「全部」里显示为未完成。这里把它改回
    普通未完成任务，并标脏重新同步，避免别的设备再从云端把旧标记拉回来。
    """
    if _schema_version(connection) >= TEMPLATE_FLAG_SCHEMA_VERSION:
        return
    existing = set(inspect(connection).get_table_names())
    fixed = 0
    if "todos" in existing:
        columns = {item["name"] for item in inspect(connection).get_columns("todos")}
        if {"is_template", "completed"} <= columns:
            result = connection.execute(
                text(
                    'UPDATE "todos" SET "is_template" = 0, "sync_status" = \'pending\', '
                    '"updated_at" = :now '
                    'WHERE "is_template" = 1 AND "completed" = 0'
                ),
                {"now": utcnow().isoformat(sep=" ")},
            )
            fixed += result.rowcount or 0
    connection.exec_driver_sql(f"PRAGMA user_version = {TEMPLATE_FLAG_SCHEMA_VERSION}")
    if fixed:
        logger.info("已修正 %d 条状态矛盾的待办（is_template 已复位）", fixed)


def _normalize_todo_fields(connection: Connection) -> None:
    """把待办的优先级收敛成英文码，并清空已废弃的手选象限。

    历史弹窗把「高/中/低」直接写进 ``todos.priority``，而四象限推导与
    「高优先级」搜索只认 high/medium/low，导致这些任务永远被判成"不重要"。
    改动的行标脏重新同步，避免别的设备再从云端把中文值拉回来。
    """
    if _schema_version(connection) >= TODO_FIELD_SCHEMA_VERSION:
        return
    existing = set(inspect(connection).get_table_names())
    fixed = 0
    if "todos" in existing:
        columns = {item["name"] for item in inspect(connection).get_columns("todos")}
        stamp = utcnow().isoformat(sep=" ")
        if "priority" in columns:
            for label, code in _PRIORITY_CODES:
                result = connection.execute(
                    text(
                        'UPDATE "todos" SET "priority" = :code, "sync_status" = \'pending\', '
                        '"updated_at" = :now WHERE "priority" = :label'
                    ),
                    {"code": code, "label": label, "now": stamp},
                )
                fixed += result.rowcount or 0
        if "quadrant" in columns:
            # 手选象限已废弃：清空后完全由自动规则推导，避免多端归类不一致。
            result = connection.execute(
                text(
                    'UPDATE "todos" SET "quadrant" = \'\', "sync_status" = \'pending\', '
                    '"updated_at" = :now WHERE "quadrant" != \'\''
                ),
                {"now": stamp},
            )
            fixed += result.rowcount or 0
    connection.exec_driver_sql(f"PRAGMA user_version = {TODO_FIELD_SCHEMA_VERSION}")
    if fixed:
        logger.info("已归一 %d 条待办字段（优先级英文码 / 清空手选象限）", fixed)


_DEDUPE_ACTIVE_DIARIES = """
UPDATE diaries SET is_deleted = 1
WHERE is_deleted = 0
  AND id NOT IN (
    SELECT id FROM (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY owner_id, date ORDER BY updated_at DESC, id DESC
      ) AS rn
      FROM diaries WHERE is_deleted = 0
    ) WHERE rn = 1
  )
"""


def _has_table_unique_constraint(connection: Connection, table: str) -> bool:
    """表上是否还有内联 UNIQUE 约束（SQLite 会为它建 origin='u' 的自动索引）。"""
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{table}")'):
        # 行结构：(seq, name, unique, origin, partial)
        if len(row) >= 4 and str(row[3]) == "u" and int(row[2]):
            return True
    return False


def _rebuild_diaries_partial_unique(connection: Connection) -> None:
    """把 diaries 的全表 UNIQUE(owner_id, date) 换成「仅活跃行唯一」的部分唯一索引。

    全表约束会让墓碑一直占着 ``(owner_id, date)``：删掉某天的日记后，在墓碑被物理
    清理前重建同日日记必然 IntegrityError。SQLite 不能 DROP 内联 UNIQUE 约束，
    只能重建表；重建前先把重复的**活跃行**收敛成一行（留 updated_at 最新的），
    否则新索引建不起来。
    """
    if _schema_version(connection) >= DIARY_UNIQUE_SCHEMA_VERSION:
        return
    existing = set(inspect(connection).get_table_names())
    rebuilt = False
    if "diaries" in existing and _has_table_unique_constraint(connection, "diaries"):
        connection.exec_driver_sql(_DEDUPE_ACTIVE_DIARIES)
        legacy = "diaries_legacy_unique"
        connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{legacy}"')
        connection.exec_driver_sql(f'ALTER TABLE "diaries" RENAME TO "{legacy}"')
        # SQLite 的 RENAME 会把普通索引一起带过去、名字不变（ix_diaries_date 等），
        # 新建同名表时 CREATE INDEX 就会撞名。先把旧表的命名索引删干净再建新表。
        # 注意要先取完 PRAGMA 结果再执行 DDL：边遍历结果集边改 schema 会报
        # "database table is locked"。
        stale_indexes = [
            str(row[1])
            for row in connection.exec_driver_sql(f'PRAGMA index_list("{legacy}")')
            if str(row[3]) == "c"
        ]
        for name in stale_indexes:
            connection.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')
        Diary.__table__.create(connection)
        old_columns = {item["name"] for item in inspect(connection).get_columns(legacy)}
        columns = [column.name for column in Diary.__table__.columns if column.name in old_columns]
        column_list = ", ".join(f'"{name}"' for name in columns)
        connection.exec_driver_sql(
            f'INSERT INTO "diaries" ({column_list}) SELECT {column_list} FROM "{legacy}"'
        )
        connection.exec_driver_sql(f'DROP TABLE "{legacy}"')
        rebuilt = True
    connection.exec_driver_sql(f"PRAGMA user_version = {DIARY_UNIQUE_SCHEMA_VERSION}")
    if rebuilt:
        logger.info("diaries 已重建为「仅活跃行唯一」的部分唯一索引")


def initialize_schema(engine: Engine, *, assign_sequences: bool = True) -> None:
    """创建新表；旧版整数主键库在一个事务内迁移为 UUID。

    ``assign_sequences=False`` 给客户端用：客户端的 ``sync_seq`` 一律由服务端分配，
    本地每次启动逐行补号既浪费写入，也会让「缺号」在两端各算一次。服务端仍保留该步骤
    （升级存量库时要先把计数器锚定到全表最大值）。
    """
    with engine.begin() as connection:
        # 库级迁移必须最先做：后续所有写入都会经过 MoneyCents（元 → 分）。
        _migrate_money_to_cents(connection)
        _normalize_timestamp_inversions(connection)
        _repair_template_flags(connection)
        _normalize_todo_fields(connection)
        _rebuild_diaries_partial_unique(connection)
        _drop_zombie_indexes(connection)
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        legacy_tables = [
            model.__tablename__
            for model in SYNC_MODELS
            if model.__tablename__ in existing
            and _uses_integer_ids(connection, model.__tablename__)
        ]
        if not legacy_tables:
            fresh_database = not existing
            Base.metadata.create_all(connection)
            _add_missing_columns(connection)
            _ensure_indexes(connection)
            # 必须在建表/补列之后：这两步产出的才是「当前模型」的 DDL，
            # 反过来先重建会把旧版整数主键库当成已迁移，UUID 迁移就被跳过了。
            _normalize_nullable_columns(connection, fresh=fresh_database)
            if not fresh_database:
                _ensure_indexes(connection)  # 重建表会带走复合索引，幂等补回
            if assign_sequences:
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
                # 金额列上面已经换算成整数分；再走 ORM 绑定会被再乘 100，
                # 因此先换回「元」交给类型装饰器重新换算。
                for money_field in ("amount", "initial_balance"):
                    if values.get(money_field) is not None:
                        values[money_field] = float(
                            Decimal(str(values[money_field])) / 100
                        )
                if table == "transactions":
                    # 旧库可能允许 account_id 为 NULL：直接 str(None) 会把空引用写成
                    # 字面量 "None"，而 to_account_id 分支已有同样保护，这里补齐。
                    old_account_id = values.get("account_id")
                    if old_account_id is not None:
                        values["account_id"] = account_ids.get(
                            (owner, old_account_id), str(old_account_id)
                        )
                    else:
                        values["account_id"] = ""
                    old_to_id = values.get("to_account_id")
                    if old_to_id is not None:
                        values["to_account_id"] = account_ids.get(
                            (owner, old_to_id), str(old_to_id)
                        )
                connection.execute(new_table.insert().values(**values))

        for table in legacy_tables:
            connection.execute(text(f"DROP TABLE {_quote(f'_legacy_uuid_{table}')}"))
        _add_missing_columns(connection)
        _ensure_indexes(connection)
        _normalize_nullable_columns(connection)
        _ensure_indexes(connection)
        if assign_sequences:
            _assign_missing_sequences(connection)
