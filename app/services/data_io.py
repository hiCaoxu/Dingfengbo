"""数据导入导出：Markdown / CSV / JSON，防止数据被锁在本机。

导出只读、不改库；导入统一走 ``repo`` 的写入路径（标脏 → 进同步队列），
因此导入的数据同样会参与多端同步。CSV 按模型列名写表头，导入时按列类型
强转，无法解析的单元格回退默认值而不是整行失败。
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..db.models import MODEL_BY_TABLE, Account, table_label
from ..db.repo import apply_remote, serialize
from ..db.session import session_scope

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
    """各表可导出记录数（一条聚合查询）。"""
    result: dict[str, int] = {}
    with session_scope() as session:
        from sqlalchemy import func

        for table, model in MODEL_BY_TABLE.items():
            total = session.scalar(
                select(func.count()).select_from(model).where(model.is_deleted.is_(False))
            )
            result[table] = int(total or 0)
    return result


# ------------------------------------------------------------------ JSON


def export_json(dest: Path | str, tables: list[str] | None = None) -> Path:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    selected = tables or list(EXPORT_TABLES)
    payload = {
        "app": "dingfengbo",
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tables": {table: _rows(table) for table in selected},
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _coerce(column, value: Any) -> Any:
    if value in (None, ""):
        return None
    python_type = column.type.python_type
    try:
        if python_type is datetime:
            return datetime.fromisoformat(str(value))
        if python_type is date:
            return date.fromisoformat(str(value)[:10])
        if python_type is bool:
            return str(value).strip().lower() in ("1", "true", "yes", "是", "y")
        if python_type is float:
            return float(value)
        if python_type is int:
            return int(float(value))
        return str(value)
    except (TypeError, ValueError):
        return None


def import_json(path: Path | str) -> dict[str, Any]:
    """导入 JSON 备份（LWW 合并），返回导入统计。"""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    tables = payload.get("tables") if isinstance(payload, dict) else None
    if not isinstance(tables, dict):
        raise ValueError("文件格式不正确：缺少 tables 字段")
    changes: list[dict] = []
    for table, rows in tables.items():
        if table not in MODEL_BY_TABLE or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                changes.append({"table": table, "data": row})
    if not changes:
        return {"imported": 0, "tables": 0}
    with session_scope() as session:
        applied = apply_remote(session, changes, owner_id="local")
    imported = sum(1 for item in applied if item.get("changed"))
    from .sync import mark_pending_write

    mark_pending_write()
    return {"imported": imported, "tables": len({item["table"] for item in changes})}


# ------------------------------------------------------------------ CSV


def export_csv(table: str, dest: Path | str) -> Path:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    model = MODEL_BY_TABLE.get(table)
    if model is None:
        raise ValueError(f"未知数据表：{table}")
    columns = [column.name for column in model.__table__.columns]
    rows = _rows(table)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    return target


def _prepare_row(model, mapping: dict[str, Any]) -> dict[str, Any]:
    """把 CSV 行转成模型字段；只接受模型已有列。"""
    values: dict[str, Any] = {}
    for column in model.__table__.columns:
        if column.name not in mapping:
            continue
        raw = mapping[column.name]
        coerced = _coerce(column, raw)
        if coerced is None and column.name not in ("due_date", "due_time", "completed_at",
                                                   "body_fat", "distance_km", "rating",
                                                   "to_account_id", "last_generated",
                                                   "next_date"):
            continue
        values[column.name] = coerced
    return values


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
                values = _prepare_row(model, row)
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
                    values.setdefault("date", date.today().isoformat())
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
    return {"imported": imported, "skipped": skipped, "errors": errors[:10]}


# ------------------------------------------------------------------ Markdown


def _table_markdown(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def export_markdown(dest_dir: Path | str, tables: list[str] | None = None) -> list[Path]:
    """导出 Markdown：待办/收支为表格，日记/笔记按篇落文件。"""
    folder = Path(dest_dir)
    folder.mkdir(parents=True, exist_ok=True)
    selected = [table for table in (tables or MARKDOWN_TABLES) if table in MODEL_BY_TABLE]
    written: list[Path] = []
    for table in selected:
        rows = _rows(table)
        if table == "diaries":
            for row in rows:
                day = str(row.get("date") or "")[:10] or "未知日期"
                path = folder / f"日记-{day}.md"
                path.write_text(
                    f"# {row.get('title') or day}\n\n{row.get('content') or ''}\n", encoding="utf-8"
                )
                written.append(path)
        elif table == "notes":
            for index, row in enumerate(rows, start=1):
                title = str(row.get("title") or "未命名笔记")
                safe = "".join(ch for ch in title if ch not in '\\/:*?"<>|')[:40] or f"笔记{index}"
                path = folder / f"笔记-{safe}.md"
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
            path = folder / "待办.md"
            path.write_text(
                "# 待办导出\n\n" + _table_markdown(headers, body) + "\n", encoding="utf-8"
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
