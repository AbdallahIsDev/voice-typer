"""Teardown helper for the backend PID file (single-instance check).

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_pid_file`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_pid_file(controller) -> None:
    """clear the backend PID file so a subsequent launch isn't
    falsely blocked by the single-instance check.

    Looks up ``_clear_backend_pid_file`` dynamically from the app
    module so tests that monkeypatch
    ``voice_typer.server.app._clear_backend_pid_file`` still take
    effect (mirrors the SettingsController convention).
    """
    try:
        from voice_typer.server import app as _app_module

        _app_module._clear_backend_pid_file()
    except Exception:
        log.debug("[SHUTDOWN] could not clear backend PID file", exc_info=True)


__all__ = ["teardown_pid_file"]
