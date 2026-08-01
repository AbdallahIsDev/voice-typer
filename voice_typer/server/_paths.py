"""Canonical path helpers for voice-typer data files.

this module is the single source of truth for hardcoded path
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

this module also owns shared network + LLM default constants
(``LOOPBACK_HOSTS``, ``LOOPBACK_HOST``, ``DEFAULT_LLM_API_URL``,
``DEFAULT_LLM_MODEL``) that were previously duplicated across
``_http_safety.py``, ``_secrets.py``, ``sidecar_ws.py``,
``llm_polish.py``, and ``config.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# shared network + LLM constants ─────────────────────────────
# These constants are the single source of truth for values that were
# previously duplicated across `_http_safety.py`, `_secrets.py`,
# `sidecar_ws.py`, `llm_polish.py`, and `config.py`.
#
# IMPORTANT: these assignments MUST precede the
# ``from voice_typer.server.config import _config_dir`` line below.
# ``config.py`` imports ``DEFAULT_LLM_API_URL`` / ``DEFAULT_LLM_MODEL``
# from this module at class-definition time, and ``llm_polish.py``
# transitively pulls this module in via ``_http_safety.py`` — so when
# ``config.py`` is being loaded (and reaches its
# ``from voice_typer.server._paths import DEFAULT_LLM_API_URL, ...``
# line), this module is only *partially* loaded (up to the
# ``_config_dir`` import). Defining the constants BEFORE that import
# guarantees they exist in the partial module dict and breaks what
# would otherwise be a circular import:
#   config → llm_polish → _http_safety → _paths → config.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
LOOPBACK_HOST: str = "127.0.0.1"
DEFAULT_LLM_API_URL: str = "https://api.openai.com/v1/chat/completions"
DEFAULT_LLM_MODEL: str = "gpt-4o-mini"

# Machine-readable application slug used for config-dir names, keyring
# service names, desktop files. Distinct from APP_NAME (display name,
# which lives in ``voice_typer.server.branding`` and is the human-
# readable ``"Voice Typer"`` string). Keeping the slug centralized here
# means a future rename only changes ONE literal in ONE file.
APP_SLUG: str = "voice-typer"

# canonical default IPC port. Previously the literal ``9876`` was
# duplicated in ``autostart_launcher.py`` (``IPC_PORT = 9876``),
# ``ipc/transport.py`` (``_pick_available_port(start: int = 9876, ...)``),
# ``ipc_server.py`` (``_pick_available_port(9876)``), and the TS main
# process (``constants.ts: IPC_PORT = ... : 9876``). Centralising it here
# means the parity test ``tests/test_ipc_port_sync.py`` can assert the TS
# side uses the same value by extracting it via regex.
IPC_PORT: int = 9876

from voice_typer.server.config import _config_dir  # noqa: E402


def config_dir() -> Path:
    """The canonical voice-typer data directory.

    Thin wrapper around :func:`voice_typer.server.config._config_dir`
    so callers don't need to reach into ``config.py`` directly.
    """
    return _config_dir()


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
    ``prewarm.py``) so the  regression test can allow it in a
        single, well-documented location.
    """
    return Path.home() / ".voice-typer" / "huggingface"


def hf_cache_dir() -> Path:
    """Path to the canonical HuggingFace model cache directory.

    (): the canonical HF cache lives at
        ``<config_dir>/huggingface`` (NOT ``~/.cache/huggingface`` — Voice
        Typer isolates its model cache inside its own data dir so an
        uninstall can purge all of it via a single ``rm -rf`` of the
        config dir). The uninstaller's ``--purge`` flag
        (``scripts/linux/uninstall_permissions.py``) and the Windows
        NSIS installer's ``deleteAppDataOnUninstall`` option both rely on
        the config dir being the single root for all user data.

        Used as a documentation anchor — the actual cache is populated by
        the ASR engines (``qwen_engine.py``, ``parakeet_engine.py``) which
        set ``HF_HOME=<config_dir>/huggingface`` via
        :mod:`voice_typer.server.asr_setup`. This helper exists so
        uninstallers and "where did my disk go?" diagnostics have a
        canonical path to inspect / delete without re-deriving the
        platform-aware ``_config_dir()`` chain.

        See :func:`legacy_hf_cache_dir` for the defensive fallback used
        when ``_config_dir()`` itself raises.
    """
    return _config_dir() / "huggingface"


def user_data_dir() -> Path:
    """Path to the canonical user data directory (root of all user data).

    (): Voice Typer stores ALL user data inside
        ``_config_dir()`` — logs, the venv, the HuggingFace model cache,
        the SQLite history DB, crash-recovery snapshots, the
        ``backend.lock`` single-instance lockfile, the autostart /
        prewarm logs, etc. This helper is the single root an uninstaller
        or "factory reset" feature can ``rm -rf`` to reclaim disk.

        The Linux uninstaller's ``--purge`` flag
        (``scripts/linux/uninstall_permissions.py --purge``) and the
        Windows NSIS installer's ``deleteAppDataOnUninstall: true`` option
        (``voice_typer/client/electron-builder.yml``) both delete this
        directory on uninstall. The deletion is OPT-IN on Linux (the
        ``--purge`` flag is off by default so users who reinstall keep
        their models); on Windows it's opt-OUT (NSIS's
        ``deleteAppDataOnUninstall`` is on by default per the
        electron-builder docs, but the user is prompted to confirm during
        uninstall).

        Semantically equivalent to :func:`config_dir` (both return
        ``_config_dir()``); the alias exists so uninstallers / factory-
        reset features can call ``user_data_dir()`` for self-documenting
        code without conflating "where the config dir is" with "where
        user data lives" (they happen to be the same path today, but the
        conceptual distinction matters for future migrations).
    """
    return _config_dir()


def user_data_subpaths_for_purge() -> list[Path]:
    """Return the list of subpaths inside ``user_data_dir()`` that an
        uninstaller should remove when purging user data.

    (): the purge is BOUNDED to these subpaths so an
        accidental ``rm -rf`` of the entire ``user_data_dir()`` can't
        delete unrelated user files if the user has manually placed
        non-Voice-Typer files inside the config dir (rare but possible on
        Linux when ``$XDG_DATA_HOME`` is set to a shared location).

        The list is intentionally exhaustive — it covers every file /
        subdirectory Voice Typer creates inside the config dir. If a new
        file is added in the future, it MUST be added to this list (the
        ``tests/test_app_cleanup.py::TestUserDataPurgePaths`` test
        enforces this by scanning the codebase for new path literals).

        The HuggingFace cache (``hf_cache_dir()``) is the big one —
        potentially gigabytes of model weights. The venv
        (``venv_pythonw().parent.parent``) is the second biggest —
        hundreds of MB of Python packages. The rest are kilobytes of
        logs / lockfiles / DBs.
    """
    base = user_data_dir()
    return [
        base / "huggingface",  # HF model cache (GBs)
        base / "venv",  # Python venv (hundreds of MB)
        base / "logs",  # rotating log files
        base / "history.db",  # SQLite history DB
        base / "history.db-wal",  # SQLite WAL (may not exist)
        base / "history.db-shm",  # SQLite SHM (may not exist)
        base / "crash_recovery.json",  # crash-recovery snapshot
        base / "backend.lock",  # single-instance POSIX lockfile
        base / "backend.pid",  # backend PID file (Windows + POSIX)
        base / "autostart.log",  # macOS LaunchAgent autostart log
        base / "prewarm-launchagent.log",  # macOS LaunchAgent prewarm log
        base / "onboarding.marker",  # onboarding completion sentinel
    ]
