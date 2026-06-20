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
_spec_script = next(a for a in sys.argv if a.endswith(".spec"))
_PROJECT_ROOT = Path(_spec_script).resolve().parent.parent.parent

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
        # Windows volume ducking — lazy-imported inside
        # WinVolumeBackend.initialize(), so PyInstaller won't
        # auto-detect them without these hiddenimports.
        "pycaw",
        "comtypes",
        "comtypes.gen",
        # macOS volume ducking — lazy-imported inside
        # MacVolumeBackend.initialize().
        "CoreAudio",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # BUILD-003: exclude unused stdlib modules to reduce binary size.
        # IMPORTANT: do NOT exclude "logging.handlers" — app.py imports
        # logging.handlers.RotatingFileHandler at module level.
        # IMPORTANT: do NOT exclude "http.bsddb" — it is not a valid
        # Python 3 module (Python 2 leftover), causes build warning.
        "tkinter",
        "tkinter.test",
        "unittest",
        "pytest",
        "test",
        "test.test_json",
        "test.test_asyncio",
        "distutils",
        "distutils.tests",
        "lib2to3",
        "lib2to3.tests",
        "turtle",
        "turtledemo",
        "idlelib",
        "idlelib.idle_test",
        "pydoc_data",
        "cgi",
        "cgitb",
        "smtpd",
        "asynchat",
        "asyncore",
        "imp",
        "msilib",
        "nis",
        "ossaudiodev",
        "spwd",
        "sunau",
        "telnetlib",
        "uu",
        "xdrlib",
        "zipapp",
        # BUILD-003: additional large stdlib modules not used by Voice Typer
        "xml.dom",
        "xml.sax",
        "xml.etree",
        "html",
        "html.parser",
        "html.entities",
        "http.server",
        "http.cookiejar",
        "email.mime",
        "email.charset",
        "email.contentmanager",
        "email.headerregistry",
        "multiprocessing.pool",
        "multiprocessing.shared_memory",
        "multiprocessing.spawn",
        "concurrent.futures.process",
        "concurrent.futures.thread",
        # BUILD-003 Round 5: additional safe exclusions (verified app.py
        # does not import these; logging.handlers and http.client are
        # intentionally kept because the app depends on them)
        "pydoc",
        "tabnanny",
        "mailcap",
        "quopri",
        "binhex",
        "macpath",
        "nturl2path",
        "plistlib",
        "py_compile",
        "compileall",
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
