# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 模式，产出 dist\\Dingfengbo\\（隐藏控制台）。

onedir 与 onefile 的区别只有一个：依赖和资源不解压到临时目录，而是常驻在
exe 旁边的 ``_internal\\`` 里。18MB 霞鹜文楷字体因此不必每次启动解压，冷启动
快得多；代价是发布物从单个 exe 变成文件夹，必须整包分发（见 build.ps1）。
"""

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
# 内置压缩包后首次启动无需联网下载（onedir 下落进 _internal/flet_desktop/app/）。
datas.append((os.path.join(SPEC_DIR, "flet-windows.zip"), "flet_desktop/app"))

# 霞鹜文楷字体（assets/fonts）：app/main.py 按 __file__ 推导 assets 目录。
# onedir 下模块仍在 _internal 里，推导结果与 onefile 的 _MEIPASS 一致。
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
    [],
    exclude_binaries=True,  # onedir：二进制与数据交给下面的 COLLECT
    name="Dingfengbo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 压缩易触发杀软误报，且解压反而更慢
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPEC_DIR, "icon.ico"),
    version=os.path.join(SPEC_DIR, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Dingfengbo",
)
