"""Native binary discovery.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).

This module owns:

- :data:`_BINARY_NAMES` — per-platform binary filename map.
- :func:`get_native_binary_path` — find the native key-listener
  binary for the current platform (env var → dev mode → PyInstaller
  bundle).
"""

import os
import sys
from pathlib import Path

# Binary names per platform
_BINARY_NAMES = {
    "darwin": "macos-key-listener",
    "win32": "windows-key-listener.exe",
    "linux": "linux-key-listener",
}


# ─── Binary discovery ──────────────────────────────────────────────────────


def get_native_binary_path() -> Path | None:
    """Find the native key-listener binary for the current platform.

    Search order:
    1. ``VOICE_TYPER_NATIVE_BINARY`` env var (explicit override — single binary)
    2. ``VOICE_TYPER_NATIVE_DIR`` env var (ADR-0020 §7 — Tauri resource dir containing all native binaries)
    3. ``voice_typer/server/native/<binary-name>`` (dev mode — source tree)
    4. ``voice_typer/server/native/<binary-name>.exe`` (Windows dev mode)
    5. Next to the Python executable (PyInstaller onedir mode)
    6. Inside ``_MEIPASS`` (PyInstaller onefile mode)

    Returns ``None`` if no binary is found.
    """
    binary_name = _BINARY_NAMES.get(sys.platform)
    if binary_name is None:
        return None

    # 1. Explicit override (single binary path)
    env_path = os.environ.get("VOICE_TYPER_NATIVE_BINARY")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    # 2. ADR-0020 §7: Tauri resource dir. The Tauri host sets this env
    # var to point at the bundle's `resources/native/` directory, which
    # contains all three platform binaries (only the matching one is
    # used). This is the path the Nuitka-frozen sidecar uses in
    # production. Falls through silently if the env var is unset (dev
    # mode) or points at a dir without the binary (broken install —
    # the dev/source-tree path below will pick up the slack).
    env_dir = os.environ.get("VOICE_TYPER_NATIVE_DIR")
    if env_dir:
        candidate = Path(env_dir) / binary_name
        if candidate.is_file():
            return candidate

    # 3/4. Dev mode — alongside this package's source tree.  Use
    # ``__file__`` of *this* module (``native_hotkeys/binary_path.py``)
    # resolved up two parents (``native_hotkeys/`` → ``server/``) and
    # then into ``server/native/``.  This mirrors the original layout
    # where ``native_hotkeys.py`` lived directly in ``server/``.
    module_dir = Path(__file__).resolve().parent.parent / "native"
    candidates = [
        module_dir / binary_name,
        # Some platforms may have a .exe suffix even in dev (cross-compile)
        module_dir / f"{binary_name}.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c

    # 5. PyInstaller onedir: binary sits next to python executable
    exe_dir = Path(sys.executable).resolve().parent
    onedir_candidate = exe_dir / binary_name
    if onedir_candidate.is_file():
        return onedir_candidate

    # 6. PyInstaller onefile: binary extracted to _MEIPASS
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        meipass_candidate = Path(meipass) / "voice_typer" / "server" / "native" / binary_name
        if meipass_candidate.is_file():
            return meipass_candidate

    return None
