# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from PyInstaller.depend import bindepend
import PyInstaller.building.build_main as build_main
from pathlib import Path


def _collect_binary_dependencies_without_importing_every_package(binaries, _packages, patterns):
    """Avoid importing unrelated packages from a shared development Python.

    PyInstaller has already collected the native binaries through the standard
    hooks.  Importing every collected package just to discover extra DLL search
    paths can block on optional packages that are not part of autoLabel.
    """
    return bindepend.binary_dependency_analysis(
        binaries,
        search_paths=[],
        symlink_suppression_patterns=patterns,
    )


build_main.find_binary_dependencies = _collect_binary_dependencies_without_importing_every_package


datas = [('assets', 'assets'), ('groundingdino/config', 'groundingdino/config')]
datas += collect_data_files('transformers')
# CLIP opens its BPE vocabulary by a path relative to clip/__file__ at runtime.
# It is not imported as Python code, so the normal dependency scan misses it.
datas += collect_data_files('clip', includes=['bpe_simple_vocab_16e6.txt.gz'])
# Ship every model offered by the training UI, plus the reference model used
# internally by Ultralytics' GPU AMP check.  Missing weights must fail the
# build rather than become a slow first-run download on an offline machine.
_project_root = Path(SPECPATH)
for _name in ('yolov8n.pt', 'yolov8s.pt', 'yolo11n.pt', 'yolo26n.pt'):
    _source = next(
        (path for path in (_project_root / 'weights' / _name, _project_root / _name) if path.is_file()),
        None,
    )
    if _source is None:
        raise FileNotFoundError(f'Missing pretrained weight required for offline build: {_name}')
    datas.append((str(_source), '.'))
# PyInstaller's regular PySide6 hook chooses only the modules reached by the
# import graph.  Ship the wheel's complete root DLL set so QtCore has every
# matching Microsoft runtime and Qt dependency on a clean Windows machine.
# Do not bundle third-party ICU DLLs: current Windows provides the compatible
# system ICU, and mixing in another ICU release breaks Qt's procedure lookup.
binaries = collect_dynamic_libs('PySide6')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=['ultralytics', 'groundingdino.util.slconfig', 'groundingdino.models', 'groundingdino.util.misc', 'groundingdino.util.inference', 'groundingdino.datasets.transforms'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The application uses PySide6 exclusively.  Other Qt bindings may be
    # installed as transitive development dependencies, but PyInstaller cannot
    # freeze multiple bindings into one executable.
    excludes=['litellm', 'django', 'tensorflow', 'tensorboard', 'gradio', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
    optimize=0,
)
# A PDF utility on the build machine contributes ICU 78 DLLs at the bundle
# root.  Qt6Core resolves icuuc.dll from that root and then fails because its
# exports do not match the Windows ICU API.  Qt uses the system ICU on Windows.
a.binaries = [
    item for item in a.binaries
    if item[0].lower() not in {'icuuc.dll', 'icudt78.dll'}
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    # Windows' GBK locale cannot encode Unicode status markers emitted by
    # Ultralytics during training.  Apply UTF-8 before Python initializes.
    [('X utf8', None, 'OPTION')],
    [],
    exclude_binaries=True,
    name='autoLabel-current',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    upx_exclude=[],
    name='autoLabel-current',
)
