# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Voice Typer — background voice-to-text tray app.

This is the **PyInstaller fallback** for ADR-0020 §4.5 — used when Nuitka
is unavailable or the Nuitka build fails on a given platform. The primary
build path is Nuitka (see `scripts/build/nuitka_freeze.sh` + the per-platform
`build_sidecar_<platform>.sh` scripts), which produces a smaller, faster
single-file binary. This spec exists so a packaging failure on Nuitka does
NOT block a release — PyInstaller is the safety net.

Two output modes are selected via the `VOICE_TYPER_TAURI_SIDECAR` env var:

1. **Legacy Electron path** (default, `VOICE_TYPER_TAURI_SIDECAR` unset):
   Build:
       pyinstaller scripts\build\voice-typer.spec --noconfirm
   Output: dist/VoiceTyper/VoiceTyper.exe (windowed, no console)
   This is the standalone Electron-replacement exe used by electron-builder's
   `extraResources` to ship a pre-built Python backend alongside the
   Electron shell. NOT used on the Tauri path.

2. **Tauri sidecar path** (`VOICE_TYPER_TAURI_SIDECAR=1`):
   Build (Windows):
       set VOICE_TYPER_TAURI_SIDECAR=1
       pyinstaller scripts\build\voice-typer.spec --noconfirm
   Build (macOS/Linux):
       VOICE_TYPER_TAURI_SIDECAR=1 pyinstaller scripts/build/voice-typer.spec --noconfirm
   Output: dist/python-sidecar-<triple>[.exe]
   Where <triple> matches the Rust target triple Tauri's `externalBin`
   mechanism expects (see `src-tauri/src/sidecar/spawn.rs::target_triple_for`):
     - Windows x86_64: python-sidecar-x86_64-pc-windows-msvc.exe
     - Windows aarch64: python-sidecar-aarch64-pc-windows-msvc.exe
     - macOS x86_64:    python-sidecar-x86_64-apple-darwin
     - macOS aarch64:   python-sidecar-aarch64-apple-darwin
     - Linux x86_64:    python-sidecar-x86_64-unknown-linux-gnu
     - Linux aarch64:   python-sidecar-aarch64-unknown-linux-gnu
   The output binary MUST be copied to `src-tauri/bin/python-sidecar-<triple>[.exe]`
   (which is where Tauri's `externalBin` looks for it) before running
   `cargo tauri build`. The `scripts/build/nuitka_freeze.sh` wrapper does
   this automatically for the Nuitka path; for the PyInstaller fallback,
   copy the file manually:
       cp dist/python-sidecar-<triple> src-tauri/bin/python-sidecar-<triple>

ADR-0020 §4.5 notes: the PyInstaller fallback produces a larger binary
(~150-200 MB vs ~80-120 MB for Nuitka) and has slower cold-start (PyInstaller's
bootloader is slower than Nuitka's compiled init). It is acceptable for an
emergency release but should not be the default.

The Tauri sidecar path uses `console=True` because the Rust host reads the
`server_started` JSON from the sidecar's stdout pipe (see
`src-tauri/src/sidecar/spawn.rs::spawn_sidecar_release`). The legacy Electron
path uses `console=False` because Electron spawns the exe with its own
stdio handling.
"""

import os
import platform
import sys
from pathlib import Path

block_cipher = None

# ─── Mode selection: Tauri sidecar vs legacy Electron exe (ADR-0020 §4.5) ────
# When VOICE_TYPER_TAURI_SIDECAR=1 is set, this spec produces a Tauri-compatible
# `python-sidecar-<triple>` binary (console on, onefile, triple-suffixed name).
# Otherwise it produces the legacy `VoiceTyper` windowed exe for Electron.
_TAURI_SIDECAR = os.environ.get("VOICE_TYPER_TAURI_SIDECAR", "") == "1"

# Compute the Rust target triple for the current platform, mirroring
# `target_triple_for` in src-tauri/src/sidecar/spawn.rs. This is the suffix
# Tauri's externalBin expects on the binary name.
# NOTE: ``platform.machine()`` is used (NOT ``os.uname().machine``) because
# ``os.uname`` is Unix-only — it raises ``AttributeError`` on Windows when the
# dict literal below is eagerly evaluated at spec-load time. ``platform.machine``
# is cross-platform and returns the same string ("x86_64"/"aarch64"/"amd64")
# on Linux, macOS, and Windows.
_ARCH = {
    "win32": os.environ.get("PLATFORM", "x86_64"),  # default x86_64; override for aarch64 cross-build
    "darwin": "x86_64" if sys.maxsize > 2**32 and platform.machine() in ("x86_64", "amd64") else "aarch64",
    "linux": platform.machine().replace("amd64", "x86_64").replace("arm64", "aarch64"),
}.get(sys.platform, "x86_64")
# On macOS, platform.machine() is the host arch; for cross-arch builds the caller
# sets VOICE_TYPER_TARGET_ARCH to override.
_ARCH = os.environ.get("VOICE_TYPER_TARGET_ARCH", _ARCH)
_TRIPLE = {
    "win32": f"{_ARCH}-pc-windows-msvc",
    "darwin": f"{_ARCH}-apple-darwin",
    "linux": f"{_ARCH}-unknown-linux-gnu",
}.get(sys.platform, f"{_ARCH}-unknown-{sys.platform}")

if _TAURI_SIDECAR:
    # Tauri sidecar: console on (Rust reads stdout for server_started JSON),
    # name includes the triple suffix, onefile mode.
    _EXE_NAME = f"python-sidecar-{_TRIPLE}"
    _CONSOLE = True
    _TARGET_ARCH = None  # let PyInstaller pick the host arch
else:
    # Legacy Electron path: windowed, no console, no triple suffix.
    _EXE_NAME = "VoiceTyper"
    _CONSOLE = False
    _TARGET_ARCH = None

# The spec file lives at <project>/scripts/build/voice-typer.spec
_spec_script = next(a for a in sys.argv if a.endswith(".spec"))
_PROJECT_ROOT = Path(_spec_script).resolve().parent.parent.parent

# BUILD-006: data files loaded at runtime via Path(__file__).parent from
# voice_typer/server/*. They must be bundled NEXT TO the importing module,
# i.e. under "voice_typer/server" in the bundle, not "voice_typer".
_corrections_json = str(_PROJECT_ROOT / "voice_typer" / "server" / "corrections.json")
_hotkey_reserved_json = str(_PROJECT_ROOT / "voice_typer" / "server" / "hotkey_reserved.json")
_model_hashes_json = str(_PROJECT_ROOT / "voice_typer" / "server" / "model_hashes.json")
# MEM-03: bundled Silero VAD JIT model (loaded by vad.py via torch.jit.load)
_silero_vad_jit = str(_PROJECT_ROOT / "voice_typer" / "server" / "silero_vad.jit")
_icon_path = _PROJECT_ROOT / "scripts" / "build" / "voice-typer.ico"

# NATIVE-001: native key-listener binaries.
# These are compiled by scripts/build/compile_native.sh (or .ps1 on Windows)
# and live at voice_typer/server/native/<binary-name>. The Python backend
# (voice_typer.server.native_hotkeys.get_native_binary_path) looks for them
# in three places:
#   1. VOICE_TYPER_NATIVE_BINARY env var (explicit override)
#   2. voice_typer/server/native/<binary-name> (dev mode + PyInstaller onedir)
#   3. Next to sys.executable (PyInstaller onedir)
#   4. Inside _MEIPASS (PyInstaller onefile)
#
# We add them as `binaries` so PyInstaller copies them into the bundle.
_native_dir = _PROJECT_ROOT / "voice_typer" / "server" / "native"
_native_binaries = []
for _binary_name in ("macos-key-listener", "windows-key-listener.exe", "linux-key-listener"):
    _candidate = _native_dir / _binary_name
    if _candidate.exists():
        # PyInstaller's binaries list is (source, dest_dir) tuples.
        # We put them under voice_typer/server/native/ in the bundle so
        # the Python code can find them via Path(__file__).parent / "native".
        _native_binaries.append((str(_candidate), "voice_typer/server/native"))
        print(f"[voice-typer.spec] Including native binary: {_candidate}")
    else:
        print(f"[voice-typer.spec] Skipping native binary (not built): {_candidate}")

# GAP-3: Linux permission-setup scripts. These are bundled so the AppImage
# first-run helper can invoke them via pkexec. The postinst/prerm scripts
# for .deb/.rpm packages also reference them (installed to
# /usr/share/voice-typer/scripts/ by electron-builder's `extraFiles`).
_linux_scripts_dir = _PROJECT_ROOT / "scripts" / "linux"
_linux_scripts = []
if _linux_scripts_dir.is_dir():
    for _script_name in (
        "install_permissions.py",
        "uninstall_permissions.py",
        "99-voice-typer.rules",
        "00-voice-typer-capslock.conf",
        "voice-typer.polkit",
    ):
        _candidate = _linux_scripts_dir / _script_name
        if _candidate.exists():
            _linux_scripts.append((str(_candidate), "scripts/linux"))
            print(f"[voice-typer.spec] Including Linux script: {_candidate}")

# PLAT-037: Windows application manifest to set requestedExecutionLevel
# to asInvoker. This prevents UAC elevation prompts on launch and
# ensures the app runs with the user's normal privileges.
# DRY: the manifest XML is the single source of truth. The
# standalone voice-typer.manifest file (which duplicated this
# XML verbatim) has been removed.
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

# XPLAT-03: build the hiddenimports list with platform-specific modules
# gated by sys.platform so PyInstaller doesn't emit warnings on platforms
# where these modules don't exist. The platform-specific modules are
# lazy-imported inside their respective backend classes and won't be
# auto-detected by PyInstaller without being listed here.
_hiddenimports = [
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
    # NEW-BUILD-001: ASR engine hiddenimports (lazy-imported).
    "voice_typer.server.parakeet_engine",
    "voice_typer.server.qwen_engine",
    "voice_typer.server.cloud_engines",
    "voice_typer.server.asr_registry",
    # NATIVE-001: native hotkey backend (lazy-imported by hotkeys.py).
    "voice_typer.server.native_hotkeys",
    # GAP-2: permission detection + onboarding (lazy-imported by hotkeys.py).
    "voice_typer.server.permissions",
    "transformers",
    "transformers.models",
    "accelerate",
    # BUILD-3: faster_whisper + faster_whisper.transcribe (ADR-0020 §4.5 Phase 0
    # verify-load target). The Nuitka sibling scripts use
    # --include-package=faster_whisper explicitly; the PyInstaller spec should
    # do the same defensively (even though static analysis often discovers
    # faster_whisper via ipc_server.py imports, listing it explicitly is the
    # safe choice — see test_pyinstaller_fallback.py GAP-1).
    "faster_whisper",
    "faster_whisper.transcribe",
    "ctranslate2",
    "tokenizers",
    "huggingface_hub",
]
# XPLAT-03: add platform-specific hiddenimports
if sys.platform == "win32":
    _hiddenimports += [
        # Windows volume ducking — lazy-imported inside
        # WinVolumeBackend.initialize().
        "pycaw",
        "comtypes",
        "comtypes.gen",
        # BUILD-N24: pywin32 (win32com.client) is used by platform.py
        # to create Desktop/Start Menu shortcuts via the native COM
        # approach (WScript.Shell). Without this hiddenimport, the
        # bundled EXE falls back to the slower PowerShell path.
        "win32com",
        "win32com.client",
    ]
elif sys.platform == "darwin":
    # macOS volume ducking — lazy-imported inside
    # MacVolumeBackend.initialize().
    _hiddenimports += ["CoreAudio"]

a = Analysis(
    [str(_PROJECT_ROOT / "voice_typer" / "server" / "ipc_server.py")],
    pathex=[str(_PROJECT_ROOT)],
    binaries=_native_binaries,
    datas=[
        (_corrections_json, "voice_typer/server"),
        (_hotkey_reserved_json, "voice_typer/server"),
        (_model_hashes_json, "voice_typer/server"),
        (_silero_vad_jit, "voice_typer/server"),
    ]
    + _linux_scripts,
    hiddenimports=_hiddenimports,
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
        # BUILD-003: additional safe exclusions (verified app.py
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
    # ADR-0020 §4.5: when VOICE_TYPER_TAURI_SIDECAR=1, emit `python-sidecar-<triple>`
    # (console on, so Rust can read server_started JSON from stdout). Otherwise
    # emit the legacy `VoiceTyper` windowed exe for the Electron fallback path.
    name=_EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # TEST-034: upx=False  # TEST-034: disabled to prevent AV false positives triggers AV false positives. UPX compression
                # causes many antivirus engines (Windows Defender, Kaspersky, etc.)
                # to flag the binary as potentially malicious. The ~15% size savings
                # is not worth the support burden of AV false positives.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=_CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
    # ADR-0020 §4.5: the Tauri sidecar path uses no icon (it's a background
    # process; Tauri's host exe carries the app icon). The legacy Electron
    # path keeps the icon for the standalone exe.
    icon=str(_icon_path) if _icon_path.exists() and not _TAURI_SIDECAR else None,
    # PLAT-037: embed the Windows application manifest with
    # requestedExecutionLevel=asInvoker to prevent UAC prompts.
    # (Applied to both modes — the manifest is harmless on the sidecar.)
    manifest=_manifest_file.name if sys.platform == "win32" else None,
)
