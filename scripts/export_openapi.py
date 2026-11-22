"""导出服务端 OpenAPI 契约到 docs/openapi.json。

用途：给客户端/第三方一份机读的接口定义，同时便于在评审里 diff「接口有没有变」。
生成的文件是 FastAPI 现有路由的忠实投影，**不要手改**；改了接口就重新跑本脚本。

    python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "openapi.json"


def main() -> int:
    # 导入 server.main 会初始化数据库：指到一个临时目录，绝不碰真实库。
    os.environ.setdefault(
        "DINGFENGBO_SERVER_DB", str(Path(tempfile.mkdtemp(prefix="dfb-openapi-")) / "cloud.db")
    )
    sys.path.insert(0, str(ROOT))
    import server.main as server  # noqa: PLC0415  （必须在设置环境变量之后导入）

    spec = server.app.openapi()
    spec["info"]["description"] = (
        "定风波同步服务：多端数据同步（游标拉取 + LWW 推送）与图片上传。"
        "认证使用 Authorization: Bearer <同步令牌>，令牌决定数据归属命名空间。"
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    paths = ", ".join(sorted(spec["paths"]))
    print(f"已写入 {TARGET.relative_to(ROOT)}：{len(spec['paths'])} 条路径")
    print(f"  {paths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
