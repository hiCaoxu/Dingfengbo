"""本地数据库备份：SQLite 在线快照、备份列表/校验/恢复、完整数据导出。

个人数据只存在本地一份 SQLite，误删/盘损/升级翻车都会一次性丢光。这里用
``sqlite3.Connection.backup``（SQLite 官方在线备份 API）生成一致性快照：
它在 WAL 模式下也能安全读取，等价于 ``VACUUM INTO`` 但不需要额外空间预留。

恢复是这类应用的「最后一道保险」，因此流程固定为：校验备份 → 恢复前再备份
当前库 → 释放连接 → 复制文件。任何一步失败都不会动到正在使用的数据库。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import DATA_DIR, DB_PATH, SETTINGS_PATH, get_setting, save_settings

logger = logging.getLogger(__name__)

BACKUP_DIR: Path = DATA_DIR / "backups"
DEFAULT_KEEP = 7
_PATTERN = "dingfengbo-*.db"
_NAME_RE = re.compile(
    r"^dingfengbo-(?P<stamp>\d{8}-\d{6}-\d{6})-(?P<label>.+)\.db$"
)


def configured_dir() -> Path:
    """备份目录：设置页可自定义，空值走默认目录。"""
    custom = str(get_setting("backup_dir") or "").strip()
    return Path(custom) if custom else BACKUP_DIR


def configured_keep() -> int:
    try:
        return max(1, int(get_setting("backup_keep", DEFAULT_KEEP) or DEFAULT_KEEP))
    except (TypeError, ValueError):
        return DEFAULT_KEEP


def backup_database(
    db_path: Path | None = None,
    dest_dir: Path | None = None,
    keep: int | None = None,
    label: str = "auto",
) -> Path:
    """生成一份一致性快照并清理超期备份，返回新文件路径。"""
    source = Path(db_path or DB_PATH)
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    target_dir = Path(dest_dir or configured_dir())
    target_dir.mkdir(parents=True, exist_ok=True)

    # 微秒级时间戳：既保证同秒多次备份不重名，也让文件名字典序等于时间序。
    # Windows 上 datetime.now() 的分辨率可能只有毫秒级，连续备份会撞名并互相覆盖，
    # 这里按微秒递补直到拿到未被占用的文件名。
    moment = datetime.now()
    target = target_dir / f"dingfengbo-{moment:%Y%m%d-%H%M%S-%f}-{label}.db"
    attempt = 0
    while target.exists() and attempt < 1000:
        attempt += 1
        moment = moment + timedelta(microseconds=1)
        target = target_dir / f"dingfengbo-{moment:%Y%m%d-%H%M%S-%f}-{label}.db"
    with closing(sqlite3.connect(source, timeout=15)) as src, closing(
        sqlite3.connect(target)
    ) as dst:
        src.backup(dst)
    _prune(target_dir, configured_keep() if keep is None else keep)
    return target


def _prune(directory: Path, keep: int) -> int:
    if keep <= 0:
        return 0
    files = sorted(directory.glob(_PATTERN), key=lambda path: path.name, reverse=True)
    removed = 0
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            logger.warning("删除旧备份失败：%s", stale)
    return removed


def maybe_daily_backup(
    db_path: Path | None = None,
    dest_dir: Path | None = None,
    keep: int | None = None,
    today: date | None = None,
) -> Path | None:
    """每天最多备份一次；已备份过或失败都返回 None（不影响启动）。"""
    today = today or date.today()
    if str(get_setting("last_backup_date") or "") == today.isoformat():
        return None
    try:
        path = backup_database(db_path=db_path, dest_dir=dest_dir, keep=keep, label="auto")
    except Exception:
        logger.exception("每日自动备份失败")
        return None
    save_settings({"last_backup_date": today.isoformat()})
    logger.info("已完成每日备份：%s", path.name)
    return path


def list_backups(directory: Path | None = None) -> list[dict]:
    """列出备份文件（新→旧），含大小、时间与来源标签。"""
    folder = Path(directory or configured_dir())
    if not folder.exists():
        return []
    items: list[dict] = []
    for path in folder.glob(_PATTERN):
        match = _NAME_RE.match(path.name)
        if match:
            try:
                created = datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S-%f")
            except ValueError:
                created = datetime.fromtimestamp(path.stat().st_mtime)
            label = match.group("label")
        else:
            created = datetime.fromtimestamp(path.stat().st_mtime)
            label = "unknown"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        items.append(
            {
                "path": path,
                "name": path.name,
                "size": size,
                "created": created,
                "label": label,
            }
        )
    items.sort(key=lambda item: item["created"], reverse=True)
    return items


def verify_backup(path: Path | str) -> tuple[bool, str]:
    """用 ``PRAGMA quick_check`` 校验备份是否可读，并统计表数量。"""
    target = Path(path)
    if not target.exists():
        return False, "文件不存在"
    try:
        with closing(sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=10)) as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
            message = str(result[0]) if result else "无返回"
            if message.lower() != "ok":
                return False, f"校验未通过：{message[:80]}"
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()
            return True, f"校验通过（{int(tables[0]) if tables else 0} 张表）"
    except sqlite3.DatabaseError as exc:
        return False, f"无法读取：{exc}"
    except OSError as exc:
        return False, f"无法打开：{exc}"


def restore_database(backup_path: Path | str, db_path: Path | None = None) -> dict:
    """从备份恢复：先校验、再备份当前库、最后替换文件。

    返回 ``{"restored": Path, "safety": Path|None}``；校验失败抛 ValueError。
    """
    source = Path(backup_path)
    target = Path(db_path or DB_PATH)
    ok, message = verify_backup(source)
    if not ok:
        raise ValueError(f"备份文件不可用：{message}")

    safety: Path | None = None
    if target.exists():
        try:
            safety = backup_database(db_path=target, label="pre-restore")
        except Exception:
            logger.exception("恢复前备份失败")
            raise

    # 释放连接池：Windows 上文件被占用时无法覆盖。
    try:
        from .session import engine

        engine.dispose()
    except Exception:
        logger.debug("释放数据库连接失败", exc_info=True)

    for suffix in ("-wal", "-shm"):
        stale = Path(str(target) + suffix)
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                logger.warning("删除 %s 失败", stale)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    logger.info("已从备份恢复数据库：%s", source.name)
    return {"restored": target, "safety": safety}


# 导出包里不允许出现凭证：zip 常被随手丢进云盘或发给别人，带上 token 等于
# 把同步服务端和模型账号一起送出去。导入时缺凭证只会要求重新填一次。
_CREDENTIAL_KEYS = ("sync_token", "ai_api_key")


def _redacted_settings() -> dict | None:
    """读一份脱敏后的设置副本；读不到就返回 None（导出包里不放设置）。"""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("读取设置文件失败，导出包不含设置", exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    for key in _CREDENTIAL_KEYS:
        if key in data:
            data[key] = ""
    return data


def export_full_data(dest_zip: Path | str, db_path: Path | None = None) -> Path:
    """导出完整数据：数据库一致性快照 + 脱敏后的 settings.json（打包为 zip）。"""
    dest = Path(dest_zip)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dfb-export-") as tmp:
        snapshot = backup_database(
            db_path=db_path or DB_PATH, dest_dir=Path(tmp), keep=0, label="export"
        )
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, arcname="dingfengbo.db")
            settings = _redacted_settings()
            if settings is not None:
                archive.writestr(
                    "settings.json", json.dumps(settings, ensure_ascii=False, indent=2)
                )
    logger.info("已导出完整数据：%s", dest)
    return dest


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def open_folder(path: Path) -> bool:
    opener = getattr(os, "startfile", None)
    if opener is None:
        return False
    opener(Path(path))
    return True
