"""Runtime-neutral backend PID-file and process-liveness helpers.

Single source of truth for the three helpers that outlived the
single-instance subsystem's Tauri-mode gating:

- :func:`_is_pid_alive`: cross-platform PID liveness probe (POSIX
  ``os.kill(pid, 0)`` / Windows ``OpenProcess``).
- :func:`_backend_pid_file`: the ``<config_dir>/run/backend.pid`` path
  (belt-and-suspenders companion to the single-instance mutex on the
  Electron path; the authoritative "is a backend running?" source for
  the autostart launcher on every runtime).
- :func:`_clear_backend_pid_file`: best-effort removal on shutdown
  (teardowns, the shutdown watchdog, and atexit).

This module is deliberately dependency-light (stdlib +
``platform_utils``) so the autostart launcher can import it at login
time without pulling the app orchestrator, and the shutdown path can
import it at interpreter-exit time. The single-instance subsystem
re-exports all three for backwards compatibility; new consumers should
import them from HERE.

``_config_dir`` is resolved at call time through the owning
``voice_typer.server.config`` module object so tests that monkeypatch
``voice_typer.server.config._config_dir`` are honored by every helper
in this module.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def _backend_pid_file() -> Path:
    """Return the path to the backend PID file (``<config_dir>/run/backend.pid``).

    Written after the single-instance mutex is acquired (Electron path)
    or after the backend binds its IPC port, removed by
    :func:`_clear_backend_pid_file` during shutdown. Used as a
    belt-and-suspenders check: on Windows the named mutex is the
    authoritative single-instance guard, but if a previous instance
    crashed hard (BSOD, power loss) the OS may not have released the
    mutex yet when the next launch tries to acquire it. The PID file
    lets us detect a stale lock and proceed.

    ``_config_dir`` is resolved at call time through the owning
    ``voice_typer.server.config`` module object so the heavy app
    orchestrator is never imported on this path and tests that
    monkeypatch ``voice_typer.server.config._config_dir`` are honored.
    """
    from voice_typer.server import config as _config_module
    from voice_typer.server._paths import RUN_SUBDIR

    return _config_module._config_dir() / RUN_SUBDIR / "backend.pid"


def _clear_backend_pid_file() -> None:
    """Remove the backend PID file (best-effort)."""
    try:
        pid_file = _backend_pid_file()
        if pid_file.exists():
            pid_file.unlink()
    except OSError as exc:
        log.debug("[SHUTDOWN] could not remove backend PID file: %s", exc)
    except Exception:
        log.debug("[SHUTDOWN] could not remove backend PID file", exc_info=True)


def _is_pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is currently running.

    Cross-platform: uses ``os.kill(pid, 0)`` on POSIX and ``OpenProcess``
    on Windows.  Returns False if the PID is invalid or the process has
    exited.  On Windows, error_access_denied (5) is treated as "alive"
    (the process exists but is owned by another session, better to
    block a duplicate than to proceed when unsure).
    """
    if pid <= 0:
        return False
    if is_windows():
        try:
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            still_active = wintypes.DWORD()
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                # error_access_denied (5) means the process exists but is
                # owned by another user/session, treat as alive.
                return kernel32.GetLastError() == 5
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(still_active)):
                    return False
                # STILL_ACTIVE == 259 means the process is running.
                return still_active.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True


__all__ = [
    "_backend_pid_file",
    "_clear_backend_pid_file",
    "_is_pid_alive",
]
