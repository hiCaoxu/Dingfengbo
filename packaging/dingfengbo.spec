# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件打包配置：生成 定风波.exe（隐藏控制台）。"""

import os
import subprocess
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(SPEC_DIR)

# 构建时确保图标、版本资源与 Flet 桌面客户端压缩包存在
subprocess.run([sys.executable, os.path.join(SPEC_DIR, "make_icon.py")], check=True)
subprocess.run([sys.executable, os.path.join(SPEC_DIR, "make_version_info.py")], check=True)
subprocess.run([sys.executable, os.path.join(SPEC_DIR, "fetch_flet_client.py")], check=True)

datas = []
for package in ("flet", "flet_desktop", "matplotlib", "zhdate"):
    datas += collect_data_files(package)

# Flet 0.86 的 flet_desktop 运行时会从包内 app/ 目录解压客户端；
# 内置压缩包后 exe 首次启动无需联网下载。
datas.append((os.path.join(SPEC_DIR, "flet-windows.zip"), "flet_desktop/app"))

# 霞鹜文楷字体（assets/fonts）：app/main.py 按 __file__ 推导 assets 目录，
# 打包后同样落在 _MEIPASS/assets，路径一致。
datas.append((os.path.join(ROOT, "assets"), "assets"))

hiddenimports = []
for package in ("app", "flet", "flet_desktop"):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Dingfengbo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPEC_DIR, "icon.ico"),
    version=os.path.join(SPEC_DIR, "version_info.txt"),
)
