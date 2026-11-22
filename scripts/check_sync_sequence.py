"""同步序列自检：比对全局计数器与各表存量 MAX(sync_seq)。

用途：升级服务端或怀疑「客户端漏拉」时，在服务端库上跑一遍：
    python scripts/check_sync_sequence.py [cloud.db 路径]

判读：
- 计数器 >= 各表最大 sync_seq：正常，新推送的号严格大于所有历史号。
- 计数器落后：说明库是在修复前初始化过的，重新启动服务端（会重跑 initialize_schema
  把计数器锚定到存量最大值）即可自愈；存量客户端若已在漏拉状态下保存过游标，
  需删除 settings.json 里对应的 sync_cursors 键（代价是一次全量重拉）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(r"D:\Data\Dingfengbo\cloud.db")


def main() -> int:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        print(f"未找到数据库：{db_path}")
        return 1

    connection = sqlite3.connect(db_path)
    try:
        tables = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if not name.startswith("sqlite_") and name != "sync_sequences"
        ]
        counter = connection.execute("SELECT value FROM sync_sequences WHERE id = 1").fetchone()
        counter_value = int(counter[0]) if counter else 0
        print(f"数据库：{db_path}")
        print(f"全局序列计数器：{counter_value}")

        max_seq = 0
        for table in tables:
            # 表名来自 sqlite_master（可被恶意库/手工建表注入引号），统一成标准标识符：
            # 引号内的 " 需要成对转义，否则能拼出任意 SQL。
            ident = '"' + str(table).replace('"', '""') + '"'
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({ident})")}
            if "sync_seq" not in columns:
                continue
            found = connection.execute(f"SELECT MAX(sync_seq) FROM {ident}").fetchone()[0] or 0
            max_seq = max(max_seq, int(found))
            print(f"  {table:<16} MAX(sync_seq)={found}")

        if counter_value >= max_seq:
            print(f"结论：正常（计数器 >= 存量最大值 {max_seq}）")
            return 0
        print(f"结论：计数器落后于存量最大值 {max_seq}，重启服务端即可自愈")
        return 2
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
