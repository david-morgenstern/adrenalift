# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Adrenalift WEB CONSOLE (browser front-end).

Builds a single .exe that launches the Flask web server and opens the browser
(see src/web/exe_entry.py).  Unlike build.spec (the PySide6 desktop GUI) this
bundle ships the HTML/CSS/JS assets and Flask instead of Qt.

Requires the driver files in drivers/ before building:
  inpoutx64.dll, WinRing0x64.dll, WinRing0x64.sys
  (and optionally WinRing0x64_patched.sys).
At first run the exe copies the drivers next to itself.  Run as Administrator.

Build:  python -m PyInstaller --noconfirm build_web.spec
"""

import json
import os

block_cipher = None

# Read version info for exe naming
_version_path = os.path.join(SPECPATH, "version.json")
with open(_version_path, "r", encoding="utf-8") as _vf:
    _version_info = json.load(_vf)
_exe_name = f"Adrenalift_Web_{_version_info['version']}_{_version_info['build']}"

# Upp package for RDNA4 VBIOS parsing (cloned into deps/)
upp_src = os.path.join(SPECPATH, "deps", "upp", "src")
upp_available = os.path.isdir(upp_src)

# Collect InpOut32/WinRing0 driver files from drivers/ if present
driver_files = []
drivers_dir = os.path.join(SPECPATH, "drivers")
driver_names = [
    "inpoutx64.dll",
    "WinRing0x64.dll",
    "WinRing0x64.sys",
    "WinRing0x64_patched.sys",
]
for name in driver_names:
    path = os.path.join(drivers_dir, name)
    if os.path.isfile(path):
        driver_files.append((path, "."))

if not any("inpoutx64" in p for p, _ in driver_files):
    print("WARNING: inpoutx64.dll not found in drivers/. Copy driver files to drivers/ before building.")
if not upp_available:
    print("WARNING: upp package not found at deps/upp/src. Clone it: git clone https://github.com/sibradzic/upp.git deps/upp")

# Web assets (Jinja templates + static JS/CSS) are bundled below via Tree() so
# the frozen server can serve them.  They unpack under sys._MEIPASS/src/web/...,
# which create_app() resolves via _resource_dir().

a = Analysis(
    ["src/web/exe_entry.py"],
    pathex=[SPECPATH, os.path.join(SPECPATH, "src")] + ([upp_src] if upp_available else []),
    binaries=[],
    datas=driver_files + [(_version_path, ".")],
    hiddenimports=[
        # Flask stack (usually auto-detected; listed for safety)
        "flask",
        "jinja2",
        "werkzeug",
        "upp",
        "upp.decode",
        "upp.atom_gen",
        "upp.atom_gen.atombios",
        "upp.atom_gen.smu_v14_0_2_navi40",
        "src",
        "src.web",
        "src.web.server",
        "src.web.hardware_service",
        "src.web.jobs",
        "src.web.tooltips",
        # The web layer reaches into a few app/engine modules:
        "src.app.constants",
        "src.app.settings",
        "src.app.help_texts",
        "src.engine",
        "src.engine.overclock_engine",
        "src.engine.od_table",
        "src.engine.smu",
        "src.engine.smu_metrics",
        "src.io",
        "src.io.mmio",
        "src.io.vbios_parser",
        "src.io.vbios_storage",
        "src.tools",
        "src.tools.reg_patch",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The web console does not need Qt; excluding it keeps the exe smaller.
    excludes=["PySide6", "shiboken6", "PyQt5", "PyQt6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Add the web asset trees to the bundle's data collection.
a.datas += Tree(
    os.path.join(SPECPATH, "src", "web", "templates"),
    prefix=os.path.join("src", "web", "templates"),
)
a.datas += Tree(
    os.path.join(SPECPATH, "src", "web", "static"),
    prefix=os.path.join("src", "web", "static"),
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=_exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Console kept so the server URL + log are visible and Ctrl+C works.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
    icon=None,
    manifest=os.path.join(SPECPATH, "app.manifest"),
)
