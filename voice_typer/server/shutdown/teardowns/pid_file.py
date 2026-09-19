"""Teardown helper for the backend PID file (single-instance check)."""

from __future__ import annotations

import atexit
import logging

log = logging.getLogger(__name__)


def _clear_pid_file_safely() -> None:
    """Best-effort removal of the backend PID file."""
    try:
        # Resolve through the owning module object at call time so tests
        from voice_typer.server import backend_pid as _backend_pid_module

        _backend_pid_module._clear_backend_pid_file()
    except Exception:
        log.warning(
            "[SHUTDOWN] could not clear backend PID file",
            exc_info=True,
        )


def teardown_pid_file(controller) -> None:
    """clear the backend PID file so a subsequent launch isn't"""
    _clear_pid_file_safely()


# Secondary safety net for the normal (non-watchdog) shutdown path:
atexit.register(_clear_pid_file_safely)


__all__ = ["teardown_pid_file"]
