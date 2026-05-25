# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the OCCT disassembly pipeline."""

import os
import sys
import glob

block_cipher = None

# ── Patch PyInstaller to skip broken subprocess calls ──────
import PyInstaller.building.build_main as _bm
_bm.discover_hook_directories = lambda: []

_orig_find = _bm.find_binary_dependencies
def _safe_find_binary_dependencies(*args, **kwargs):
    try:
        return _orig_find(*args, **kwargs)
    except Exception:
        return []
_bm.find_binary_dependencies = _safe_find_binary_dependencies

# ── Collect OCCT data files (Python wrappers .py/.pyi) ─────
occt_datas = []
try:
    import OCC
    occ_dir = os.path.dirname(OCC.__file__)
    occt_datas.append((occ_dir, 'OCC'))
except ImportError:
    pass

# ── Collect ALL DLLs from conda Library/bin ───────────────
conda_bin = os.path.join(
    os.environ.get('USERPROFILE', ''),
    'miniconda3', 'envs', 'pyoccenv', 'Library', 'bin')

occt_binaries = []
if os.path.isdir(conda_bin):
    for dll_path in glob.glob(os.path.join(conda_bin, '*.dll')):
        occt_binaries.append((dll_path, '.'))

a = Analysis(
    ['pipeline.py'],
    pathex=[],
    binaries=occt_binaries,
    datas=occt_datas,
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
        'numpy',
    ],
    hookspath=[''],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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
