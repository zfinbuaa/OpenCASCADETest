# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OCCT disassembly pipeline.

OCC packaging is handled entirely by hooks/hook-OCC.py
DLL search path setup is handled by hooks/rthook-occ.py
"""

import pathlib
_HERE = pathlib.Path(SPECPATH).resolve()

import PyInstaller.building.build_main as _bm
_bm.discover_hook_directories = lambda: []

_orig_find = _bm.find_binary_dependencies
def _safe_find_binary_dependencies(binaries, *args, **kwargs):
    try:
        return _orig_find(binaries, *args, **kwargs)
    except Exception:
        import sys
        print("WARN: binary dependency scan failed, continuing", file=sys.stderr)
        return set()
_bm.find_binary_dependencies = _safe_find_binary_dependencies

block_cipher = None

a = Analysis(
    ['pipeline.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pipeline',
        'pipeline.stp_reader',
        'pipeline.xcaf_utils',
        'pipeline.mesher',
        'pipeline.gltf_exporter',
        'pipeline.contact_detector',
        'pipeline.fastener_identifier',
        'pipeline.direction_calc',
        'pipeline.dag_builder',
        'pipeline.collision_check',
        'pipeline.path_searcher',
        'pipeline.path_validator',
        'pipeline.assembly_json',
        'pipeline.bom_loader',
        'pipeline.dependency_chain',
        'pipeline._occ_lock',
        'pipeline.compound_utils',
        'numpy',
        'openpyxl',
        'trimesh',
    ],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=['hooks/rthook-occ.py'],
    excludes=[
        'tkinter',
        '_tkinter',
        'OCC.Display',
        'OCC.Display.qtDisplay',
        'OCC.Display.tkDisplay',
        'OCC.Display.wxDisplay',
        'OCC.Display.SimpleGui',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
        'matplotlib',
        'IPython',
        'jupyter',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_version_file = str(_HERE / 'version_info.txt')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoModel',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_HERE / 'app_icon.ico') if (_HERE / 'app_icon.ico').exists() else None,
    version=_version_file if pathlib.Path(_version_file).exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AutoModel',
)
