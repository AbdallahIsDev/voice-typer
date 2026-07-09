"""Canonical path helpers for voice-typer data files.

RW-7: this module is the single source of truth for hardcoded path
literals that previously lived as ``Path.home() / ".voice-typer"``
across ~7 server modules. Centralizing them here ensures every
auxiliary artifact (PID files, sentinel files, log files, venv
interpreters) routes through the platform-aware logic in
:func:`voice_typer.server.config._config_dir`:

- Windows: ``%APPDATA%/voice-typer``
- macOS: ``~/Library/Application Support/voice-typer``
- Linux: ``$XDG_DATA_HOME/voice-typer`` (default ``~/.local/share/voice-typer``)
- Override: ``$VOICE_TYPER_CONFIG_DIR``
- Migration: ``~/.voice-typer`` is still checked first so existing
  installs keep their data in place.

The ``config.py`` module retains its own ``legacy = Path.home() /
".voice-typer"`` migration probe (it IS the canonical legacy-path
check) and is the only other module allowed to reference that literal
directly — see ``tests/test_paths.py`` for the regression guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

from voice_typer.server.config import _config_dir


def config_dir() -> Path:
    """The canonical voice-typer data directory.

    Thin wrapper around :func:`voice_typer.server.config._config_dir`
    so callers don't need to reach into ``config.py`` directly.
    """
    return _config_dir()


def pid_file() -> Path:
    """Path to the autostart launcher's PID file (``autostart.pid``).

    Written by :mod:`voice_typer.server.autostart_launcher` so the
    Electron main process's ``killStalePython()`` reaper can discover
    and clean up the autostarted session.
    """
    return _config_dir() / "autostart.pid"


def prewarm_sentinel() -> Path:
    """Path to the prewarm sentinel file (``.prewarm-sentinel``).

    Written by :mod:`voice_typer.server.prewarm` after a successful
    prewarm run; read by ``_already_warmed()`` to dedup prewarm
    invocations within the same boot session.

    Note: :func:`voice_typer.server.prewarm._sentinel_path` resolves
    this lazily via its own BootTrigger-aware fallback chain (which
    includes Windows registry and POSIX ``getpwuid`` lookups when env
    vars are missing). This helper is the canonical path under normal
    (post-logon) conditions.
    """
    return _config_dir() / ".prewarm-sentinel"


def prewarm_log() -> Path:
    """Path to the dedicated prewarm log file (``prewarm.log``).

    Contains only ``[PREWARM]`` messages (via a logger-name filter),
    written by :func:`voice_typer.server.prewarm._setup_logging`. The
    About page's "Open prewarm log" button opens this file.
    """
    return _config_dir() / "prewarm.log"


def prewarm_launchagent_log() -> Path:
    """Path to the macOS LaunchAgent's prewarm log file.

    Used by :mod:`voice_typer.server.prewarm_scheduler_posix` as the
    ``StandardOutPath`` / ``StandardErrorPath`` of the LaunchAgent
    plist, so launchd's prewarm output is captured to a known file
    rather than the system log.
    """
    return _config_dir() / "prewarm-launchagent.log"


def autostart_log() -> Path:
    """Path to the macOS LaunchAgent's autostart log file.

    Used by :func:`voice_typer.server.server_platform._enable_autostart_macos`
    as the ``StandardOutPath`` / ``StandardErrorPath`` of the
    ``com.voicetyper.plist`` LaunchAgent, so launchd's autostart output
    is captured to a known file rather than the system log.
    """
    return _config_dir() / "autostart.log"


def venv_pythonw() -> Path:
    """Path to the venv's pythonw.exe (Windows) or python (Unix).

    Used by the Windows Task Scheduler and HKCU Run-key fallback
    (:mod:`voice_typer.server.task_scheduler`) to launch the prewarm
    script in the same Python environment the app uses at runtime.

    The path may not exist on a fresh install (no venv yet) — callers
    must check ``.exists()`` before relying on it. On non-Windows the
    path uses ``bin/python`` (POSIX venv layout); the existing Windows
    callers gate on ``is_windows()`` first so they never actually
    consume the POSIX path, but it's still returned for symmetry and
    so tests that pin ``sys.platform = "win32"`` continue to work.
    """
    if sys.platform == "win32":
        return _config_dir() / "venv" / "Scripts" / "pythonw.exe"
    return _config_dir() / "venv" / "bin" / "python"


def legacy_hf_cache_dir() -> Path:
    """Path to the legacy ``~/.voice-typer/huggingface`` directory.

    Used as a defensive last-resort fallback in
    :func:`voice_typer.server.prewarm._resolve_hf_cache_dir` when
    ``_config_dir()`` itself raises (e.g. the BootTrigger scenario
    where ``$HOME`` / ``%USERPROFILE%`` are unset and the platform
    detection chain can't resolve a config dir). The literal
    ``Path.home() / ".voice-typer"`` lives here (rather than inline in
    ``prewarm.py``) so the RW-7 regression test can allow it in a
    single, well-documented location.
    """
    return Path.home() / ".voice-typer" / "huggingface"
