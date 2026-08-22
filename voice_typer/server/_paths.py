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

import sys  # noqa: F401  # kept for test patches (tests monkeypatch `_paths.sys.platform`)
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

# shared network + LLM constants ─────────────────────────────
# These constants are the single source of truth for values that were
# previously duplicated across `_http_safety.py`, `_secrets.py`,
# `sidecar_ws.py`, `llm_polish.py`, and `config.py`.
#
# Historical note: this block previously HAD to precede an eager
# ``from voice_typer.server.config import _config_dir`` line, because
# ``config.py`` imports ``DEFAULT_LLM_API_URL`` / ``DEFAULT_LLM_MODEL``
# from this module at class-definition time and the eager import would
# otherwise have created a circular load
# (config → llm_polish → _http_safety → _paths → config). The eager
# import has been replaced by the lazy :func:`_resolve_config_dir`
# resolver below, so the constants block no longer has a positional
# constraint — but it stays at the top of the module because that is
# the conventional layout and makes the public surface easy to scan.
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

# WN-12: canonical name of the env var that carries the per-launch
# session token from the host (Electron / Tauri) to the Python sidecar.
# Previously the bare literal ``"VOICE_TYPER_IPC_TOKEN"`` was duplicated
# across 7+ files (electron_launcher, env_validation, ipc/entrypoint,
# ipc/transport_tcp, sidecar_ws, …). A typo in any of those would
# silently break IPC auth (the host sets X, the sidecar reads Y, every
# connection is refused). Centralising the literal here means the
# canonical name is defined in exactly one place; the parity test
# ``tests/test_ipc_token_env_var_sync.py`` asserts every reference
# matches this constant.  Internal identifier (not a user-facing
# brand) — renaming requires a coordinated host (Electron / Tauri)
# update + this constant change.
IPC_TOKEN_ENV_VAR: str = "VOICE_TYPER_IPC_TOKEN"

# Lazy resolver for :func:`voice_typer.server.config._config_dir`.
#
# Previously this module eagerly did ``from voice_typer.server.config
# import _config_dir`` at module load. That eager import pulled in the
# heavy ``config`` package (validators, secure_file_io, volume_ducker,
# duck_crash_recovery, etc.) — measured cold-start cost: ~54ms. The
# import is now deferred to first use via :func:`_resolve_config_dir`
# so this module imports in <5ms (only ``sys`` + ``pathlib`` at the
# top). The first call to any helper that needs the config dir pays
# the one-time import cost (~54ms), then the resolved function is
# cached on this module's ``_config_dir`` attribute and subsequent
# calls are a single dict lookup + function call (~0.5µs).
#
# Tests override the resolver by setting ``_paths._config_dir``
# directly (the existing ``monkeypatch.setattr(_paths, "_config_dir",
# lambda: tmp_path)`` pattern in ``tests/test_paths.py`` and
# ``tests/test_app_cleanup.py``). When the override is in place the
# lazy import is skipped (the resolver sees a non-None value and
# returns it immediately), so tests don't pay the heavy-import cost
# and don't touch the real filesystem.
_config_dir = None  # type: ignore[assignment]


def _resolve_config_dir():
    """Return the cached ``_config_dir`` callable, importing on first use.

    Resolves to :func:`voice_typer.server.config._config_dir` on the
    first call (paying the one-time ~54ms ``config`` package import
    cost) and caches the function reference on this module's
    ``_config_dir`` attribute. Subsequent calls are a single attribute
    read + callable invocation.

    Test fixtures that monkeypatch ``_paths._config_dir`` to a custom
    callable short-circuit this resolver (the patched value is not
    ``None``), so the heavy ``config`` import never fires under test.
    """
    global _config_dir
    if _config_dir is None:
        # Imported lazily to keep ``_paths`` cold-import cheap. The
        # ``config`` package re-exports ``_config_dir`` from
        # ``config_internals.paths`` (it is the canonical public
        # surface; the internal module is an implementation detail).
        from voice_typer.server.config import _config_dir as _impl

        _config_dir = _impl
    return _config_dir


def config_dir() -> Path:
    """The canonical voice-typer data directory.

    Thin wrapper around :func:`voice_typer.server.config._config_dir`
    so callers don't need to reach into ``config.py`` directly.
    """
    return _resolve_config_dir()()


# O3: transient runtime state (pid files, lockfiles, session markers)
# lives under a dedicated ``run/`` subdir of the config dir, keeping the
# config-dir root clean (alongside ``logs/``, ``db/``, ``crashes/``…).
# Legacy root-located pid/lock files are swept / migrated at startup.
RUN_SUBDIR = "run"


def run_dir() -> Path:
    """Path to the ``run/`` subdir holding transient runtime state (O3).

    Created on demand by the pid/lock writers; callers should not assume
    it exists before first use.
    """
    return config_dir() / RUN_SUBDIR


def prewarm_launchagent_log() -> Path:
    """Path to the legacy macOS LaunchAgent's prewarm log file.

    Prewarm became a worker startup phase (master plan §6.2 P-1): the
    macOS LaunchAgent + the ``prewarm_scheduler_posix`` module that
    wrote here were deleted. The path helper is retained so an
    uninstaller sweep can still clean up the legacy log file on
    upgraded installs (the file may exist on installs that predate
    the prewarm retirement).
    """
    return _resolve_config_dir()() / "prewarm-launchagent.log"


def autostart_log() -> Path:
    """Path to the macOS LaunchAgent's autostart log file.

    Used by :func:`voice_typer.server.server_platform._enable_autostart_macos`
    as the ``StandardOutPath`` / ``StandardErrorPath`` of the
    ``com.voicetyper.plist`` LaunchAgent, so launchd's autostart output
    is captured to a known file rather than the system log.
    """
    return _resolve_config_dir()() / "autostart.log"


def venv_pythonw() -> Path:
    """Path to the venv's pythonw.exe (Windows) or python (Unix).

    Used by :func:`voice_typer.server.server_platform.autostart_windows`
    to launch the autostart task in the same Python environment the
    app uses at runtime.

    The path may not exist on a fresh install (no venv yet) — callers
    must check ``.exists()`` before relying on it. On non-Windows the
    path uses ``bin/python`` (POSIX venv layout); the existing Windows
    callers gate on ``is_windows()`` first so they never actually
    consume the POSIX path, but it's still returned for symmetry and
    so tests that pin ``sys.platform = "win32"`` continue to work.
    """
    if is_windows():
        # ``_resolve_config_dir()()`` returns ``Any`` (lazy resolver);
        # wrap in ``Path`` to honor the declared return type.
        return Path(_resolve_config_dir()()) / "venv" / "Scripts" / "pythonw.exe"
    return Path(_resolve_config_dir()()) / "venv" / "bin" / "python"


def legacy_hf_cache_dir() -> Path:
    """Path to the legacy ``~/.voice-typer/huggingface`` directory.

    Used as a defensive last-resort fallback in
    :func:`voice_typer.server.prewarm._resolve_hf_cache_dir` when
    ``_config_dir()`` itself raises (e.g. the BootTrigger scenario
    where ``$HOME`` / ``%USERPROFILE%`` are unset and the platform
    detection chain can't resolve a config dir). The literal
    ``Path.home() / ".voice-typer"`` lives here (rather than inline
    in :mod:`voice_typer.server.prewarm`) so the regression test
    can allow it in a single, well-documented location.
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
    return _resolve_config_dir()() / "huggingface"


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
    return _resolve_config_dir()()


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
        base / "db",  # history DB + sidecars + backups (O2)
        base / "run",  # transient runtime state: pids, locks, session markers (O3)
        base / "electron-profile",  # Electron/Chromium profile (caches, Local Storage)
        base / "history.db",  # legacy SQLite history DB (pre-O2; may survive migration)
        base / "history.db-wal",  # legacy SQLite WAL (may not exist)
        base / "history.db-shm",  # legacy SQLite SHM (may not exist)
        base / "crash_recovery.json",  # crash-recovery snapshot
        base / "backend.lock",  # legacy single-instance POSIX lockfile (pre-O3)
        base / "backend.pid",  # legacy backend PID file (pre-O3)
        base / "autostart.log",  # macOS LaunchAgent autostart log
        base / "prewarm-launchagent.log",  # macOS LaunchAgent prewarm log
        base / "onboarding.marker",  # onboarding completion sentinel
    ]
