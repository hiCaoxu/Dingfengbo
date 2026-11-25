"""手机端启动入口。

必须在导入 app 之前决定数据目录：config 和数据库在导入时就绑定路径。
手机用 Flet 的应用私有目录；在电脑上跑这个入口时用项目下的 data/mobile，
避免和桌面端的 D:\\Data\\Dingfengbo 抢同一个库。
"""

from __future__ import annotations

import os
from pathlib import Path


def prepare_data_dir() -> None:
    storage = os.environ.get("FLET_APP_STORAGE_DATA", "").strip()
    if storage:
        os.environ["DINGFENGBO_DATA_DIR"] = storage
        return
    root = Path(__file__).resolve().parent / "data" / "mobile"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["DINGFENGBO_DATA_DIR"] = str(root)


prepare_data_dir()

from app.mobile.main import run  # noqa: E402

if __name__ == "__main__":
    run()
