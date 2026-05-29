# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Voice Typer — background voice-to-text tray app.

Build:
    pyinstaller scripts\build\voice-typer.spec --noconfirm

Output: dist/VoiceTyper/VoiceTyper.exe (windowed, no console)
"""

import sys
from pathlib import Path

block_cipher = None

# The spec file lives at <project>/scripts/build/voice-typer.spec
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Locate the corrections.json data file relative to project root
_corrections_json = str(_PROJECT_ROOT / "voice_typer" / "corrections.json")
_icon_path = _PROJECT_ROOT / "scripts" / "build" / "voice-typer.ico"

a = Analysis(
    [str(_PROJECT_ROOT / "voice_typer" / "__main__.py")],
    pathex=[str(_PROJECT_ROOT)],
    binaries=[],
    datas=[(_corrections_json, "voice_typer")],
    hiddenimports=[
        "scipy.signal",
        "scipy._lib",
        "scipy._lib._testutils",
        "scipy.signal._arraytools",
        "sounddevice",
        "pynput.keyboard._win32",
        "pynput.keyboard._darwin",
        "pynput.keyboard._xorg",
        "pynput.mouse._win32",
        "pynput.mouse._darwin",
        "pynput.mouse._xorg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter.test",
        "unittest",
        "pytest",
        "test",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="VoiceTyper",
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
    icon=str(_icon_path) if _icon_path.exists() else None,
)
