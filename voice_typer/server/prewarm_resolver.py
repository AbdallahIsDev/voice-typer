"""Prewarm executable resolver — shared by all platform schedulers.

ADR-0020 §5: post-Tauri-migration, the prewarm helper is frozen the
same Nuitka way as the sidecar, into ``prewarm-<target-triple>[.exe]``,
and bundled as a ``bundle.resource`` (NOT ``externalBin`` — prewarm
is launched by the platform scheduler, not spawned by Tauri as a
managed child).

This module provides a single canonical resolver,
:func:`resolve_prewarm_exe`, used by:

- :mod:`voice_typer.server.task_scheduler` (Windows Task Scheduler +
  HKCU Run-key fallback)
- :mod:`voice_typer.server.prewarm_scheduler_posix` (macOS LaunchAgent
  + Linux systemd user timer)
- The ``run_prewarm`` / ``get_prewarm_status`` IPC handlers in
  :mod:`voice_typer.server.handlers.status_handlers`

Pre-migration, each scheduler built its own command line
(``pythonw.exe -m voice_typer.server.prewarm`` on Windows,
``python3 -m voice_typer.server.prewarm`` on POSIX). Post-migration,
they all delegate to :func:`resolve_prewarm_exe` so the path logic
lives in one place.

Cross-platform
--------------
The resolver returns a string suitable for the platform's scheduler:

- **Windows**: a single path to ``prewarm-x86_64-pc-windows-msvc.exe``
  (Task Scheduler ``<Command>`` element + HKCU Run-key value). The
  ``<Arguments>`` element / Run-key args stay empty.
- **macOS**: a single path to ``prewarm-<arch>-apple-darwin``
  (LaunchAgent ``ProgramArguments`` array, single element).
- **Linux**: a single path to ``prewarm-<arch>-unknown-linux-gnu``
  (systemd ``ExecStart=`` value, single token).

Dev mode (no frozen exe found) returns a Python command line
(``python -m voice_typer.server.prewarm``) so source-tree development
keeps working — same as today.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger(__name__)


def _target_triple() -> str:
    """Return the Rust target triple for the current platform+arch.

    Mirrors the naming Tauri uses for ``externalBin`` binaries (see
    ADR-0020 §4.1). The triple must match the suffix on the frozen
    prewarm binary name exactly, or the resolver won't find it.

    ADR-0020 §4.1 explicitly lists Windows ARM64
    (``aarch64-pc-windows-msvc``) as a supported target triple. The
    previous implementation used ``sys.maxsize > 2**32`` which only
    distinguishes x86_64 from x86 — it NEVER returns ``aarch64`` on
    Windows ARM64 hosts. This is fixed by using ``platform.machine()``
    for all three platforms (Windows now mirrors the macOS/Linux
    branches).
    """
    import platform

    if is_windows():
        # ADR-0020 §4.1: Windows ARM64 is explicitly supported.
        # platform.machine() returns 'ARM64' on Windows 11 ARM,
        # 'AMD64' on x86_64, 'x86' on 32-bit. Normalize to the Rust
        # arch names used in the target triple.
        machine = platform.machine().lower()
        if machine in ("arm64", "aarch64"):
            arch = "aarch64"
        elif machine in ("amd64", "x86_64"):
            arch = "x86_64"
        elif machine in ("x86", "i386", "i686"):
            # 32-bit Windows — rare but supported by the triple naming.
            arch = "i686"
        else:
            # Unknown — fall back to the maxsize check (64-bit → x86_64).
            arch = "x86_64" if sys.maxsize > 2**32 else "i686"
        return f"{arch}-pc-windows-msvc"
    elif is_macos():
        machine = platform.machine()
        arch = "aarch64" if machine == "arm64" else "x86_64"
        return f"{arch}-apple-darwin"
    else:
        machine = platform.machine() or "x86_64"
        # Linux ARM64: platform.machine() returns 'aarch64' (already
        # the Rust arch name); x86_64 returns 'x86_64'.
        return f"{machine}-unknown-linux-gnu"


def _exe_suffix() -> str:
    """Return the platform's executable suffix."""
    return ".exe" if is_windows() else ""


def _candidate_paths() -> list[Path]:
    """Build the ordered list of filesystem paths to probe.

    ADR-0020 §5 resolution order:
      1. ``VOICE_TYPER_PREWARM_EXE`` env var (preferred — set by the
         Tauri host at startup to ``resourceDir/prewarm-<triple>``).
      2. Tauri resource dir, heuristically:
         - macOS: ``<app>.app/Contents/Resources/prewarm-<triple>``
         - Linux: ``$APPDIR/usr/resources/prewarm-<triple>`` (AppImage)
                  or ``/usr/lib/voice-typer/resources/prewarm-<triple>``
                  (.deb/.rpm install)
         - Windows: ``%LOCALAPPDATA%\\Programs\\VoiceTyper\\resources\\prewarm-<triple>.exe``
      3. Next to ``sys.executable`` (PyInstaller onedir fallback).
      4. Inside ``_MEIPASS`` (PyInstaller onefile fallback).

    Returns the candidate list in priority order; the caller picks the
    first that exists.
    """
    triple = _target_triple()
    suffix = _exe_suffix()
    name = f"prewarm-{triple}{suffix}"
    candidates: list[Path] = []

    # 1. Explicit env override (Tauri host sets this).
    env_path = os.environ.get("VOICE_TYPER_PREWARM_EXE")
    if env_path:
        candidates.append(Path(env_path))

    # 2. Tauri resource dir, per platform.
    if is_macos():
        # Voice Typer.app/Contents/Resources/prewarm-<triple>
        # sys.argv[0] in a .app bundle is typically
        # ".../Voice Typer.app/Contents/MacOS/VoiceTyper" — go up two
        # to get Contents, then into Resources.
        try:
            exe = Path(sys.argv[0]).resolve()
            app_contents = exe.parent.parent
            candidates.append(app_contents / "Resources" / name)
        except Exception:
            pass
    elif is_linux():
        # AppImage: $APPDIR is set by the AppImage runtime to the
        # squashfs mount point (e.g. /tmp/.mount_VoiceTyXXXX/).
        # .deb/.rpm: install to /usr/lib/voice-typer/resources/.
        appdir = os.environ.get("APPDIR")
        if appdir:
            candidates.append(Path(appdir) / "usr" / "resources" / name)
        candidates.append(Path("/usr/lib/voice-typer/resources") / name)
        candidates.append(Path("/usr/share/voice-typer/resources") / name)
    elif is_windows():
        # %LOCALAPPDATA%\Programs\VoiceTyper\resources\prewarm-<triple>.exe
        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            candidates.append(Path(localappdata) / "Programs" / "VoiceTyper" / "resources" / name)

    # 3. PyInstaller onedir: binary sits next to the Python executable.
    exe_dir = Path(sys.executable).resolve().parent
    candidates.append(exe_dir / name)

    # 4. PyInstaller onefile: binary extracted to _MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / name)

    return candidates


def resolve_prewarm_exe() -> str | None:
    """Resolve the prewarm executable path, post-Tauri-migration.

    Returns
    -------
    str | None
        - A single path string to the frozen prewarm executable if one
          is found (suitable for direct use as a Task Scheduler
          ``<Command>``, LaunchAgent ``ProgramArguments[0]``, or
          systemd ``ExecStart`` token).
        - A dev-fallback command line (``"<python> -m
          voice_typer.server.prewarm"``) if no frozen exe is found —
          keeps source-tree development working.
        - ``None`` only if even the dev fallback can't be built (which
          would require ``sys.executable`` to be empty, an exotic
          failure mode).

    Notes
    -----
    The Tauri host should set ``VOICE_TYPER_PREWARM_EXE`` to
    ``resourceDir/prewarm-<triple>[.exe]`` at startup. That env var is
    the highest-priority source — the heuristic paths below it are
    belt-and-suspenders for the case where the env var is missing
    (e.g. running from source, or a broken install).
    """
    for candidate in _candidate_paths():
        if candidate.is_file():
            log.debug("[PREWARM-RESOLVE] found: %s", candidate)
            return str(candidate)

    # Dev fallback: plain python module invocation. Works without a
    # frozen exe (source-tree development, CI without Nuitka build).
    # Quote sys.executable in case it contains spaces (Windows path).
    exe = sys.executable
    if not exe:
        log.error("[PREWARM-RESOLVE] sys.executable is empty — cannot build dev fallback")
        return None
    log.info("[PREWARM-RESOLVE] no frozen exe found — using dev fallback: %s -m voice_typer.server.prewarm", exe)
    # The caller (task_scheduler / prewarm_scheduler_posix) is
    # responsible for splitting this into command + args. We return
    # the full command line so the existing Python-module path still
    # works unchanged.
    return f'"{exe}" -m voice_typer.server.prewarm'
