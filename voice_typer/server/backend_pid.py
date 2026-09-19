"""Backend PID helpers."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def _backend_pid_file() -> Path:
    """Return the path to the backend PID file (``<config_dir>/run/backend.pid``)."""
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
    """Return True if a process with the given PID is currently running."""
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
