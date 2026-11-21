"""本地数据库备份：SQLite 在线快照 + 按份数保留。

个人数据只存在本地一份 SQLite，误删/盘损/升级翻车都会一次性丢光。这里用
``sqlite3.Connection.backup``（SQLite 官方在线备份 API）生成一致性快照：
它在 WAL 模式下也能安全读取，等价于 ``VACUUM INTO`` 但不需要额外空间预留。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from ..config import DATA_DIR, DB_PATH, get_setting, save_settings

logger = logging.getLogger(__name__)

BACKUP_DIR: Path = DATA_DIR / "backups"
DEFAULT_KEEP = 7
_PATTERN = "dingfengbo-*.db"


def backup_database(
    db_path: Path | None = None,
    dest_dir: Path | None = None,
    keep: int = DEFAULT_KEEP,
    label: str = "auto",
) -> Path:
    """生成一份一致性快照并清理超期备份，返回新文件路径。"""
    source = Path(db_path or DB_PATH)
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    target_dir = Path(dest_dir or BACKUP_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)

    # 微秒级时间戳：既保证同秒多次备份不重名，也让文件名字典序等于时间序。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = target_dir / f"dingfengbo-{stamp}-{label}.db"
    with closing(sqlite3.connect(source, timeout=15)) as src, closing(
        sqlite3.connect(target)
    ) as dst:
        src.backup(dst)
    _prune(target_dir, keep)
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
    keep: int = DEFAULT_KEEP,
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
