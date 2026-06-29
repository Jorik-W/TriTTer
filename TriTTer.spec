# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for TriTTer.

One-file:   pyinstaller TriTTer.spec                           -> dist/TriTTer.exe
One-folder: TRITTER_ONEFILE=0 pyinstaller TriTTer.spec         -> dist/TriTTer/
            (or via make.ps1 buildfolder)
"""

import os
from PyInstaller.utils.hooks import collect_submodules

spec_dir = os.path.dirname(os.path.abspath(SPEC))
src_dir  = os.path.join(spec_dir, "src")

# --- data files -----------------------------------------------------------
platform_tools_dir = os.path.join(spec_dir, "platform-tools")
datas = [
    (os.path.join(src_dir, "analyze", "icons"), "icons"),
    (platform_tools_dir, "platform-tools"),
]

# --- build mode: onefile (default) or onefolder --------------------------
onefile = os.environ.get("TRITTER_ONEFILE", "1") == "1"

# --- hidden imports -------------------------------------------------------
hiddenimports = collect_submodules("pyqtgraph")

# --- analysis -------------------------------------------------------------
a = Analysis(
    [os.path.join(src_dir, "main.py")],
    pathex=[
        src_dir,
        os.path.join(src_dir, "core"),
        os.path.join(src_dir, "analyze"),
        os.path.join(src_dir, "plan"),
        os.path.join(src_dir, "ui"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt6", "PySide6", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [] if not onefile else a.binaries,
    [] if not onefile else a.datas,
    [] if not onefile else [],
    name="TriTTer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    exclude_binaries=not onefile,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(src_dir, "analyze", "icons", "logo_blue.ico"),
)

if not onefile:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="TriTTer",
    )
