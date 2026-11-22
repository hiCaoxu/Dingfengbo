"""数据导入导出：Markdown / CSV / JSON，防止数据被锁在本机。

导出只读、不改库；导入统一走 ``repo`` 的写入路径（标脏 → 进同步队列），
因此导入的数据同样会参与多端同步。CSV 按模型列名写表头，导入时按列类型
强转，无法解析的单元格回退默认值而不是整行失败。
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import update as sql_update

from ..db.models import MODEL_BY_TABLE, Account, table_label, utcnow
from ..db.repo import apply_remote, serialize
from ..db.session import session_scope
from .clock import get_clock_offset
from .money import to_float

logger = logging.getLogger(__name__)

# 可导出的表（保持稳定顺序，供界面展示）
EXPORT_TABLES: tuple[str, ...] = (
    "todos",
    "diaries",
    "notes",
    "exercises",
    "diets",
    "weights",
    "sleeps",
    "entertainments",
    "accounts",
    "transactions",
    "budgets",
    "recurring_rules",
    "habits",
    "habit_logs",
)

MARKDOWN_TABLES = {"todos", "diaries", "notes", "transactions"}


def _rows(table: str) -> list[dict[str, Any]]:
    model = MODEL_BY_TABLE.get(table)
    if model is None:
        return []
    with session_scope() as session:
        objects = session.scalars(
            select(model).where(model.is_deleted.is_(False)).order_by(model.created_at)
        )
        return [serialize(obj)["data"] for obj in objects]


def counts() -> dict[str, int]:
    """各表可导出记录数（每表一条 COUNT，均走 is_deleted 过滤）。"""
    result: dict[str, int] = {}
    with session_scope() as session:
        for table, model in MODEL_BY_TABLE.items():
            total = session.scalar(
                select(func.count()).select_from(model).where(model.is_deleted.is_(False))
            )
            result[table] = int(total or 0)
    return result


# ------------------------------------------------------------------ JSON


def _iter_rows(table: str):
    """逐行产出导出数据（生成器）。

    导出不再先把整表读成 list 再 ``json.dumps`` 整串：大库上这一步会同时驻留
    原对象、字典列表和整段字符串，内存放大 3~4 倍。
    """
    model = MODEL_BY_TABLE.get(table)
    if model is None:
        return
    with session_scope() as session:
        result = session.scalars(
            select(model).where(model.is_deleted.is_(False)).order_by(model.created_at)
        )
        for obj in result:
            yield serialize(obj)["data"]


def _atomic_write_text(target: Path, text: str) -> None:
    """先写同目录临时文件再原子替换。

    导出中途失败（磁盘满、进程被杀）会留下半截文件；直接写在目标路径上，
    用户看到的是一份「看起来存在但导入报错」的备份。临时文件与目标同目录，
    ``os.replace`` 才是原子的。
    """
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def export_json(dest: Path | str, tables: list[str] | None = None) -> Path:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    selected = tables or list(EXPORT_TABLES)
    tmp = target.with_suffix(target.suffix + ".tmp")
    stamp = datetime.now().isoformat(timespec="seconds")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            # 手工拼 JSON：逐表逐行写入，内存占用与库大小无关。
            handle.write("{\n")
            handle.write('  "app": "dingfengbo",\n')
            handle.write('  "version": 1,\n')
            handle.write(f'  "exported_at": {json.dumps(stamp)},\n')
            handle.write('  "tables": {\n')
            for index, table in enumerate(selected):
                handle.write(f'    "{table}": [')
                first = True
                for row in _iter_rows(table):
                    handle.write("" if first else ",")
                    handle.write("\n      ")
                    handle.write(json.dumps(row, ensure_ascii=False))
                    first = False
                handle.write("\n    ]" + ("," if index < len(selected) - 1 else "") + "\n")
            handle.write("  }\n}\n")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%d/%m/%Y", "%m/%d/%Y")
# 日/月与月/日都能解析的那两种写法：11/22/2026 只有月/日成立，05/06/2026 两种都成立
# 且结果不同——静默按「日/月」解释会把 5 月 6 日读成 6 月 5 日。
_AMBIGUOUS_DATE_FORMATS = ("%d/%m/%Y", "%m/%d/%Y")
# 金额列导入时统一量化到分，避免 CSV 里的 0.1 变成二进制浮点后累加出分位误差。
_MONEY_FIELDS = {"amount", "initial_balance"}


def _coerce_date(value: Any) -> date | None:
    """宽松解析日期：兼容 Excel/本地化导出的 `2026/1/1`、`2026.01.01` 等写法。

    只认常见分隔符与「年月日」；解析不出来、或者「日/月 vs 月/日」有两种不同结果时
    返回 None，由调用方决定是跳过还是报错——直接把字符串塞进 Date 列会触发
    SQLAlchemy 的 StatementError，整行失败且提示含糊。
    """
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        logger.debug("_coerce_date：忽略 %s", type(exc).__name__)
        pass
    ambiguous: set[date] = set()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text[:19], fmt).date()
        except ValueError as exc:
            logger.debug("_coerce_date：忽略 %s", type(exc).__name__)
            continue
        if fmt not in _AMBIGUOUS_DATE_FORMATS:
            return parsed
        ambiguous.add(parsed)
    if len(ambiguous) > 1:
        logger.warning("日期写法有歧义（日/月 与 月/日 都成立），已拒绝：%s", text)
        return None
    return next(iter(ambiguous), None)


def _coerce(column, value: Any) -> Any:
    if value in (None, "", "None"):
        return None
    python_type = column.type.python_type
    try:
        if python_type is datetime:
            text = str(value).strip()
            try:
                return datetime.fromisoformat(text[:19])
            except ValueError as exc:
                logger.debug("_coerce：忽略 %s", type(exc).__name__)
                parsed = _coerce_date(value)
                return datetime.combine(parsed, datetime.min.time()) if parsed else None
        if python_type is date:
            return _coerce_date(value)
        if python_type is bool:
            return str(value).strip().lower() in ("1", "true", "yes", "是", "y")
        if python_type is float:
            parsed = float(value)
            if not math.isfinite(parsed):
                # 'inf'/'nan' 能通过 float()，但会把下游统计与 round 全部污染
                raise ValueError("非有限数值")
            return to_float(parsed) if column.name in _MONEY_FIELDS else parsed
        if python_type is int:
            return int(float(value))
        return str(value)
    except ArithmeticError:
        # int(float('inf')) 抛 OverflowError：只该丢这一列，不该整行失败
        logger.debug("_coerce：数值溢出，按无法解析处理")
        return None
    except (TypeError, ValueError) as exc:
        logger.debug("_coerce：忽略 %s", type(exc).__name__)
        return None


# 备份文件是外部输入：同步元数据与软删除标记不允许由文件决定，
# 否则构造一份带 sync_seq / is_deleted 的 JSON 就能污染同步队列或伪造墓碑。
_IMPORT_BLOCKED_FIELDS = {"owner_id", "sync_status", "sync_seq", "is_deleted"}

# 导入允许文件带 updated_at，但不允许「未来时间」：未来版本会让这条记录在本机和
# 其他设备上产生的较早编辑被 LWW 判旧（推送被拒、版本分叉）。5 分钟容差吸收
# 多端时钟抖动，超过就钳到当前对齐时间并告警。
_IMPORT_FUTURE_TOLERANCE = timedelta(minutes=5)


def _aligned_now() -> datetime:
    """当前时间按服务端时间轴对齐（与 repo.mark_dirty 同一口径）。"""
    return utcnow() + timedelta(seconds=get_clock_offset())


def _clamp_future_updated_at(data: dict[str, Any]) -> bool:
    """把数据里明显在未来的 updated_at 钳到当前时间，返回是否做了钳制。"""
    raw = data.get("updated_at")
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(str(raw)[:19])
    except ValueError as exc:
        logger.debug("_clamp_future_updated_at：忽略 %s", type(exc).__name__)
        return False
    limit = _aligned_now()
    if parsed <= limit + _IMPORT_FUTURE_TOLERANCE:
        return False
    data["updated_at"] = limit.isoformat(sep=" ")
    return True


def _sanitize(model, row: dict[str, Any]) -> dict[str, Any]:
    """按模型列白名单过滤导入载荷。"""
    allowed = {
        column.name
        for column in model.__table__.columns
        if column.name not in _IMPORT_BLOCKED_FIELDS
    }
    return {key: value for key, value in row.items() if key in allowed}


def _requeue_pending(session, applied: list[dict]) -> int:
    """把导入实际落到本地的行重置回 pending，让其进入推送队列。

    ``apply_remote`` 是「拉取远端变更」用的，落库统一标 clean（远端本来就有，
    无需回推）。导入的数据却只存在于本地：不翻回 pending 的话，推送只发 pending，
    这批数据永远上传不到服务端——「导入完成」就成了假象。
    """
    queued = 0
    for change in applied:
        model = MODEL_BY_TABLE.get(change.get("table"))
        record_id = str((change.get("data") or {}).get("id") or "")
        if model is None or not record_id:
            continue
        result = session.execute(
            sql_update(model)
            .where(model.id == record_id, model.owner_id == "local")
            .values(sync_status="pending")
            .execution_options(synchronize_session=False)
        )
        queued += result.rowcount or 0
    return queued


def import_json(path: Path | str) -> dict[str, Any]:
    """导入 JSON 备份（LWW 合并），返回导入统计。

    逐条兜底：一行格式坏了只跳过这一行并计数，不让整次导入失败；导入成功落库的
    行会重新进入待同步队列，因此导入的数据同样会参与多端同步。
    """
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    tables = payload.get("tables") if isinstance(payload, dict) else None
    if not isinstance(tables, dict):
        raise ValueError("文件格式不正确：缺少 tables 字段")
    applied: list[dict] = []
    skipped = 0
    superseded = 0
    unchanged = 0
    errors: list[str] = []
    tables_seen: set[str] = set()
    queued = 0
    with session_scope() as session:
        for table, rows in tables.items():
            model = MODEL_BY_TABLE.get(table)
            if model is None or not isinstance(rows, list):
                continue
            tables_seen.add(table)
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, dict) or not row.get("id"):
                    skipped += 1
                    continue
                data = _sanitize(model, row)
                if not data.get("updated_at"):
                    skipped += 1
                    errors.append(f"{table_label(table)} 第 {index} 条：缺少 updated_at，已跳过")
                    continue
                # 导入是「恢复」语义：文件里的记录一律视为未删除，否则会写进墓碑行，
                # 用户看到「导入成功」但界面上什么都没有。
                data["is_deleted"] = False
                if _clamp_future_updated_at(data):
                    errors.append(
                        f"{table_label(table)} 第 {index} 条：updated_at 在未来，"
                        "已钳制到当前时间（否则后续编辑会被判旧）"
                    )
                try:
                    # 单条一个 savepoint：某条落库失败只回滚它自己，后面的行继续。
                    with session.begin_nested():
                        result = apply_remote(
                            session, [{"table": table, "data": data}], owner_id="local"
                        )
                    if any(item.get("superseded") for item in result):
                        superseded += 1
                    elif result and not result[0].get("changed"):
                        unchanged += 1
                    applied.extend(result)
                except Exception as exc:
                    logger.warning("导入 %s 第 %d 条失败：%s", table, index, exc)
                    skipped += 1
                    errors.append(f"{table_label(table)} 第 {index} 条：{type(exc).__name__}")
        queued = _requeue_pending(session, applied)
    imported = sum(1 for item in applied if item.get("changed"))
    skipped += superseded + unchanged
    if superseded:
        errors.append(f"{superseded} 条因本地版本更新被跳过（未覆盖较新数据）")
    elif unchanged:
        errors.append(f"{unchanged} 条与本地版本一致，未重复写入")
    if queued:
        from .sync import mark_pending_write

        mark_pending_write()
    # 导入是破坏性操作（LWW 合并）：把「读了几张表、写入几条、跳过几条」一次性记全，
    # 用户反馈「导入后数据不对」时不用再猜。只记数量，不记正文。
    logger.info(
        "JSON 导入完成：表 %d 张，写入 %d 条，跳过 %d 条（旧版本 %d / 一致 %d），入队 %d 条",
        len(tables_seen), imported, skipped, superseded, unchanged, queued,
    )
    return {
        "imported": imported,
        "tables": len(tables_seen),
        "skipped": skipped,
        "superseded": superseded,
        "unchanged": unchanged,
        "queued": queued,
        "errors": errors[:10],
    }


# ------------------------------------------------------------------ CSV


_CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def _csv_safe(value: Any) -> Any:
    """CSV 公式注入防护：文本单元格以危险字符开头时加前导单引号。

    数值（int/float/bool/None）原样写出——负数本来就要以 ``-`` 开头，加引号会把它
    变成文本，回灌时类型就错了。文本既查首字符，也查「前导空白 + 危险字符」，
    以及制表符/回车/换行开头这些变体。
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    stripped = text.lstrip()
    if text[:1] in _CSV_DANGEROUS_PREFIXES or stripped[:1] in _CSV_DANGEROUS_PREFIXES:
        return "'" + text
    return text


def export_csv(table: str, dest: Path | str) -> Path:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    model = MODEL_BY_TABLE.get(table)
    if model is None:
        raise ValueError(f"未知数据表：{table}")
    columns = [column.name for column in model.__table__.columns]
    rows = _rows(table)
    # 先写临时文件再原子替换：中途失败不会在目标路径留下半截 CSV。
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _csv_safe(row.get(column)) for column in columns})
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def _prepare_row(model, mapping: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把 CSV 行转成模型字段；只接受模型已有列。

    返回 (字段, 无法解析的列名)。日期/时间列有值却解析不出来时必须报错而不是静默
    丢弃——后者会把「到期日」变成空，用户看到的是数据"导进来了但日期没了"。
    同步元数据与软删除标记不允许由文件决定（与 JSON 导入保持同一白名单）。
    """
    values: dict[str, Any] = {}
    bad: list[str] = []
    for column in model.__table__.columns:
        if column.name in _IMPORT_BLOCKED_FIELDS:
            continue
        if column.name not in mapping:
            continue
        raw = mapping[column.name]
        if str(raw).strip() == "":
            continue
        coerced = _coerce(column, raw)
        if coerced is None:
            if column.type.python_type in (date, datetime):
                bad.append(column.name)
            continue
        values[column.name] = coerced
    return values, bad


# CSV 没有可靠的 id（用户经常手工编辑再导入），按业务键找已有行：
# 同一份 CSV 导入两次不会变成两份数据。
# 收支**故意不参与**业务键去重：同一天两笔金额、分类、备注都相同的交易是合法的，
# 按业务键合并会静默少一笔。重复导入同一份 CSV 会追加重复行，但导入前有确认与
# 自动备份，且导出的 CSV 自带 id（带 id 时走精确更新，不会重复）。
_CSV_NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "todos": ("title", "due_date"),
    "diaries": ("date",),
    "notes": ("title",),
    "habits": ("name",),
    "habit_logs": ("habit_id", "date"),
    "accounts": ("name",),
    "weights": ("date",),
    "sleeps": ("date",),
    "exercises": ("date", "kind"),
    "diets": ("date", "meal"),
    "entertainments": ("title", "kind"),
    "budgets": ("month",),
    "recurring_rules": ("name",),
    "life_events": ("title",),
}


def _find_csv_duplicate(session, model, table: str, values: dict[str, Any]):
    """按业务键找已经存在的行；没有可用业务键时返回 None。"""
    keys = _CSV_NATURAL_KEYS.get(table)
    if not keys:
        return None
    conditions = [model.is_deleted.is_(False), model.owner_id == "local"]
    for key in keys:
        if key not in values or values[key] in (None, ""):
            return None
        conditions.append(getattr(model, key) == values[key])
    return session.scalar(select(model).where(*conditions))


def import_csv(table: str, path: Path | str) -> dict[str, Any]:
    """导入 CSV；返回 {imported, skipped, errors}。"""
    model = MODEL_BY_TABLE.get(table)
    if model is None:
        raise ValueError(f"未知数据表：{table}")
    source = Path(path)
    imported = 0
    skipped = 0
    errors: list[str] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=2):
            try:
                values, bad = _prepare_row(model, row)
                if bad:
                    skipped += 1
                    errors.append(f"第 {index} 行：无法解析 {'、'.join(bad)}")
                    continue
                if not values:
                    skipped += 1
                    continue
                if table == "transactions":
                    account_id = str(values.get("account_id") or "").strip()
                    if not account_id:
                        with session_scope() as session:
                            account = session.scalar(
                                select(Account).where(Account.is_deleted.is_(False))
                            )
                        account_id = account.id if account else ""
                    if not account_id:
                        skipped += 1
                        errors.append(f"第 {index} 行：没有可用账户")
                        continue
                    values["account_id"] = account_id
                    values.setdefault("kind", "expense")
                    # 必须是 date 对象：SQLite 的 Date 列只接受 Python date。
                    values.setdefault("date", date.today())
                if table == "todos" and not str(values.get("title") or "").strip():
                    skipped += 1
                    continue
                if table == "notes" and not str(values.get("title") or "").strip():
                    values["title"] = "导入笔记"
                if table == "diaries":
                    if not values.get("date"):
                        skipped += 1
                        continue
                    values.setdefault("title", str(values["date"]))
                from ..db.repo import create, update

                with session_scope() as session:
                    existing = None
                    record_id = values.get("id")
                    if record_id:
                        existing = session.get(model, str(record_id))
                    if existing is None:
                        # 没有匹配的 id：按业务键找同一条记录，避免重复导入变双份。
                        existing = _find_csv_duplicate(session, model, table, values)
                    incoming_updated = values.get("updated_at")
                    if (
                        existing is not None
                        and isinstance(incoming_updated, datetime)
                        and existing.updated_at
                        and incoming_updated <= existing.updated_at
                    ):
                        # CSV 是一份旧快照：本地已经有更新版本，不拿旧的覆盖新的。
                        skipped += 1
                        errors.append(f"第 {index} 行：本地数据更新，已跳过")
                        continue
                    if existing is not None:
                        fields = {
                            key: value
                            for key, value in values.items()
                            if hasattr(existing, key) and key not in ("id", "owner_id")
                        }
                        update(session, existing, **fields)
                    else:
                        values.pop("id", None)
                        values.pop("owner_id", None)
                        create(session, model, **values)
                imported += 1
            except Exception as exc:  # 单行失败不影响整体导入
                logger.warning("导入第 %d 行失败：%s", index, exc)
                skipped += 1
                errors.append(f"第 {index} 行：{type(exc).__name__}")
    if imported:
        from .sync import mark_pending_write

        mark_pending_write()
    logger.info("CSV 导入完成：%s 写入 %d 行，跳过 %d 行", table, imported, skipped)
    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


# ------------------------------------------------------------------ Markdown


def _table_markdown(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
}


def _safe_filename(text: str, fallback: str, used: set[str]) -> str:
    """生成 Windows 可用的文件名：去掉非法字符/保留名，并对同名自动加序号。

    同名笔记若直接写同一个文件会互相覆盖，导出结果静默少一条——这是「导出丢数据」
    的典型来源，所以这里按 `used` 集合去重。
    """
    cleaned = "".join(ch for ch in str(text or "") if ch not in '\\/:*?"<>|').strip().strip(".")
    cleaned = cleaned[:40] or fallback
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    candidate = cleaned
    index = 2
    while candidate.lower() in used:
        candidate = f"{cleaned}-{index}"
        index += 1
    used.add(candidate.lower())
    return candidate


def export_markdown(dest_dir: Path | str, tables: list[str] | None = None) -> list[Path]:
    """导出 Markdown：待办/收支为表格，日记/笔记按篇落文件。"""
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    selected = [table for table in (tables or MARKDOWN_TABLES) if table in MODEL_BY_TABLE]
    written: list[Path] = []
    used_diary_names: set[str] = set()
    used_note_names: set[str] = set()
    for table in selected:
        rows = _rows(table)
        if table == "diaries":
            for row in rows:
                day = str(row.get("date") or "")[:10] or "未知日期"
                stem = _safe_filename(f"日记-{day}", "日记-未知日期", used_diary_names)
                path = folder / f"{stem}.md"
                path.write_text(
                    f"# {row.get('title') or day}\n\n{row.get('content') or ''}\n", encoding="utf-8"
                )
                written.append(path)
        elif table == "notes":
            for index, row in enumerate(rows, start=1):
                title = str(row.get("title") or "未命名笔记")
                stem = _safe_filename(f"笔记-{title}", f"笔记{index}", used_note_names)
                path = folder / f"{stem}.md"
                tags = row.get("tags") or "[]"
                path.write_text(
                    f"# {title}\n\n> 标签：{tags}\n\n{row.get('content') or ''}\n",
                    encoding="utf-8",
                )
                written.append(path)
        elif table == "todos":
            headers = ["标题", "状态", "到期", "优先级", "标签", "备注"]
            body = [
                [
                    row.get("title") or "",
                    "已完成" if row.get("completed") else "未完成",
                    row.get("due_date") or "",
                    row.get("priority") or "",
                    row.get("tags") or "",
                    row.get("note") or "",
                ]
                for row in rows
            ]
            path = folder / "任务.md"
            path.write_text(
                "# 任务导出\n\n" + _table_markdown(headers, body) + "\n", encoding="utf-8"
            )
            written.append(path)
        elif table == "transactions":
            headers = ["日期", "类型", "分类", "金额", "备注"]
            body = [
                [
                    row.get("date") or "",
                    "收入" if row.get("kind") == "income" else "支出",
                    row.get("category") or "",
                    row.get("amount") or "",
                    row.get("note") or "",
                ]
                for row in rows
            ]
            path = folder / "收支.md"
            path.write_text(
                "# 收支导出\n\n" + _table_markdown(headers, body) + "\n", encoding="utf-8"
            )
            written.append(path)
        else:
            path = folder / f"{table_label(table)}.md"
            headers = ["记录"]
            body = [
                [json.dumps(row, ensure_ascii=False)]
                for row in rows
            ]
            path.write_text(
                f"# {table_label(table)}\n\n" + _table_markdown(headers, body) + "\n",
                encoding="utf-8",
            )
            written.append(path)
    return written


def export_pack(dest_dir: Path | str, tables: list[str] | None = None) -> dict[str, Any]:
    """一键导出：JSON + Markdown + 每表 CSV，返回写入的文件列表。"""
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    selected = tables or list(EXPORT_TABLES)
    files: list[Path] = [export_json(folder / f"dingfengbo-{stamp}.json", selected)]
    files.extend(export_markdown(folder / f"markdown-{stamp}", selected))
    csv_dir = folder / f"csv-{stamp}"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for table in selected:
        try:
            files.append(export_csv(table, csv_dir / f"{table}.csv"))
        except Exception:
            logger.exception("导出 %s CSV 失败", table)
    return {"dir": folder, "files": files, "count": len(files)}
