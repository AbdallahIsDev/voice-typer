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

# PLAT-037: Windows application manifest to set requestedExecutionLevel
# to asInvoker. This prevents UAC elevation prompts on launch and
# ensures the app runs with the user's normal privileges.
_manifest_xml = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity
    type="win32"
    name="VoiceTyper"
    version="1.0.0.0"
    processorArchitecture="*"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Windows 10 / 11 -->
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
      <!-- Windows 8.1 -->
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
      <!-- Windows 8 -->
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>
      <!-- Windows 7 -->
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>
    </application>
  </compatibility>
  <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
  <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
</assembly>
"""

# Write manifest to a temp file so PyInstaller can embed it
import tempfile
_manifest_file = tempfile.NamedTemporaryFile(
    mode="w", suffix=".manifest", delete=False, encoding="utf-8"
)
_manifest_file.write(_manifest_xml)
_manifest_file.close()

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
        # BUILD-N24: pywin32 (win32com.client) is used by platform.py
        # to create Desktop/Start Menu shortcuts via the native COM
        # approach (WScript.Shell). Without this hiddenimport, the
        # bundled EXE falls back to the slower PowerShell path.
        "win32com",
        "win32com.client",
        # macOS volume ducking — lazy-imported inside
        # MacVolumeBackend.initialize().
        "CoreAudio",
        # NEW-BUILD-001: ASR engine hiddenimports (lazy-imported).
        "voice_typer.server.parakeet_engine",
        "voice_typer.server.qwen_engine",
        "voice_typer.server.cloud_engines",
        "voice_typer.server.asr_registry",
        "transformers",
        "transformers.models",
        "accelerate",
        "ctranslate2",
        "tokenizers",
        "huggingface_hub",
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
    upx=False,  # TEST-034: upx=False  # TEST-034: disabled to prevent AV false positives triggers AV false positives. UPX compression
                # causes many antivirus engines (Windows Defender, Kaspersky, etc.)
                # to flag the binary as potentially malicious. The ~15% size savings
                # is not worth the support burden of AV false positives.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_icon_path) if _icon_path.exists() else None,
    # PLAT-037: embed the Windows application manifest with
    # requestedExecutionLevel=asInvoker to prevent UAC prompts
    manifest=_manifest_file.name,
)
