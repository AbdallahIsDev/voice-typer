# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Voice Typer — background voice-to-text tray app.

Build:
    pyinstaller scripts\build\voice-typer.spec --noconfirm

Output: dist/VoiceTyper/VoiceTyper.exe (windowed, no console)
"""

import sys
from pathlib import Path

block_cipher = None

# Locate the corrections.json data file
_corrections_json = str(Path("voice_typer") / "corrections.json")
_icon_path = str(Path("scripts") / "build" / "voice-typer.ico")

a = Analysis(
    ["voice_typer/__main__.py"],
    pathex=["."],
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
    icon=_icon_path if Path(_icon_path).exists() else None,
)
