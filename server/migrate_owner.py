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
    parser.add_argument("--old-token", required=True, help="旧的同步令牌")
    parser.add_argument(
        "--db",
        default=os.environ.get("DINGFENGBO_SERVER_DB", r"D:\Data\Dingfengbo\cloud.db"),
        help="云端数据库路径（默认取 DINGFENGBO_SERVER_DB）",
    )
    args = parser.parse_args()

    old_owner = args.old_token[:64]
    new_owner = owner_digest(args.old_token)

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
