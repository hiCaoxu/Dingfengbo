"""一次性数据迁移：把 cloud.db 中旧的明文 owner_id 更新为 sha256 摘要。

背景：owner 键从「token 明文前 64 位」改为 sha256 摘要后，存量库里的旧
owner_id 无法匹配新鉴权结果。旧值是截断后的 token，无法反向还原完整 token，
因此迁移需要用户提供旧令牌。

用法（先停止服务，再执行）：
    python -m server.migrate_owner --old-token <旧令牌> [--db /data/cloud.db]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
from pathlib import Path

from sqlalchemy import update as sql_update
from sqlalchemy.orm import sessionmaker

from app.db.engine import create_sqlite_engine
from app.db.migrations import initialize_schema
from app.db.models import SYNC_MODELS

logger = logging.getLogger(__name__)

def owner_digest(token: str) -> str:
    return hashlib.sha256(f"dingfengbo-v1\0{token}".encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--old-token",
        default=None,
        help="旧的同步令牌（不推荐：会留在 shell 历史与进程列表里；"
        "建议改用环境变量 DINGFENGBO_OLD_TOKEN 或交互输入）",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("DINGFENGBO_SERVER_DB", r"D:\Data\Dingfengbo\cloud.db"),
        help="云端数据库路径（默认取 DINGFENGBO_SERVER_DB）",
    )
    args = parser.parse_args()

    old_token = args.old_token or os.environ.get("DINGFENGBO_OLD_TOKEN", "").strip()
    if not old_token:
        # 交互输入不落 shell 历史；getpass 给出提示但回显为空。
        import getpass

        old_token = getpass.getpass("旧同步令牌：").strip()
    if not old_token:
        raise SystemExit("缺少旧同步令牌（--old-token / DINGFENGBO_OLD_TOKEN / 交互输入）")
    if args.old_token:
        print("提示：令牌通过命令行传入，可能已进入 shell 历史；下次建议用环境变量。")

    old_owner = old_token[:64]
    new_owner = owner_digest(old_token)

    engine = create_sqlite_engine(Path(args.db))
    initialize_schema(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    total = 0
    with Session() as session:
        for model in SYNC_MODELS:
            result = session.execute(
                sql_update(model.__table__)
                .where(model.__table__.c.owner_id == old_owner)
                .values(owner_id=new_owner)
            )
            total += result.rowcount or 0
        session.commit()

    print(f"迁移完成：共更新 {total} 行，owner_id {old_owner} -> {new_owner}")
    engine.dispose()


if __name__ == "__main__":
    main()
