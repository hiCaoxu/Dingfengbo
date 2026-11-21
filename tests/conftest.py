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


@atexit.register
def _cleanup() -> None:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
