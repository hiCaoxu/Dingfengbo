"""测试隔离：把应用数据目录指向临时目录。

``app.config`` 在导入时解析数据目录并把 engine 绑定到该路径，因此必须在任何
``app.*`` 模块导入之前设置环境变量——conftest 先于测试模块加载，正好满足。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="dingfengbo-tests-")
os.environ["DINGFENGBO_DATA_DIR"] = _TMP_DIR
# 开发机上如果导出了同步令牌，服务端会进入白名单模式，单测用的任意 Bearer 会全部 401。
os.environ.pop("DINGFENGBO_SYNC_TOKEN", None)
os.environ.pop("DINGFENGBO_SYNC_TOKENS", None)


@atexit.register
def _cleanup() -> None:
    # 先释放引擎的连接池再删目录：Windows 上还有打开的句柄时 rmtree 会报
    # WinError 32，退出码变成 1（测试全过但看起来像失败）。
    try:
        from app.db.session import engine

        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
