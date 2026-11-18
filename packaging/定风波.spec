# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件打包配置：生成 定风波.exe（隐藏控制台）。"""

import os
import subprocess
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(SPEC_DIR)

# 构建时确保图标存在（简约线性图标，非二进制手写）
subprocess.run([sys.executable, os.path.join(SPEC_DIR, "make_icon.py")], check=True)

datas = []
for package in ("flet", "flet_desktop", "matplotlib", "zhdate"):
    datas += collect_data_files(package)

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
    name="定风波",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPEC_DIR, "icon.ico"),
)
