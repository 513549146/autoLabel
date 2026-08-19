# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

project_root = os.path.abspath(SPECPATH)

datas = []
binaries = []
hiddenimports = []

# 本地 groundingdino 包：收集子模块（含可能的动态导入）与数据文件
gd_datas, gd_bins, gd_hidden = collect_all("groundingdino")
datas += gd_datas
binaries += gd_bins
hiddenimports += gd_hidden

# 本地 gui 包
gui_datas, gui_bins, gui_hidden = collect_all("gui")
datas += gui_datas
binaries += gui_bins
hiddenimports += gui_hidden

# groundingdino 运行时会按路径读取 config 下的 .py 文件（SLConfig.fromfile）
datas += collect_data_files("groundingdino", includes=["config/*.py"], include_py_files=True)

# 第三方包：需要一并收集数据文件与隐藏导入
for pkg in ("ultralytics", "supervision", "transformers"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["main.py"],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "notebook",
        "jupyter",
        "pytest",
        "pip",
        "setuptools",
        "pkg_resources",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="autoLabel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="autoLabel",
)
