"""Teardown helper for the backend PID file (single-instance check).

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_pid_file`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import atexit
import logging

log = logging.getLogger(__name__)


def _clear_pid_file_safely() -> None:
    """Best-effort removal of the backend PID file.

    Looks up ``_clear_backend_pid_file`` dynamically from the app
    module so tests that monkeypatch
    ``voice_typer.server.app._clear_backend_pid_file`` still take
    effect (mirrors the SettingsController convention). Wrapped in a
    top-level try/except so the atexit invocation path never raises
    into interpreter shutdown.

    The failure log level is WARNING (was DEBUG pre-fix) so operators
    see why the stale PID file survived — a stale file falsely blocks
    the next launch's single-instance check.
    """
    try:
        from voice_typer.server import app as _app_module

        _app_module._clear_backend_pid_file()
    except Exception:
        log.warning(
            "[SHUTDOWN] could not clear backend PID file",
            exc_info=True,
        )


def teardown_pid_file(controller) -> None:
    """clear the backend PID file so a subsequent launch isn't
    falsely blocked by the single-instance check.

    Looks up ``_clear_backend_pid_file`` dynamically from the app
    module so tests that monkeypatch
    ``voice_typer.server.app._clear_backend_pid_file`` still take
    effect (mirrors the SettingsController convention).

    NOTE: this helper is one of several teardowns sequenced by
    ``_do_cleanup()``. If a *prior* teardown hangs and the shutdown
    watchdog fires ``os._exit(0)``, this helper is never reached —
    ``os._exit(0)`` bypasses atexit. To cover that gap, the
    watchdog closure in ``shutdown.lifecycle._watchdog`` calls
    ``_clear_backend_pid_file()`` explicitly BEFORE ``os._exit(0)``,
    AND we register ``_clear_pid_file_safely`` below as an
    ``atexit`` callback for the normal (non-watchdog) shutdown path
    where ``_do_cleanup`` was skipped or never finished.
    """
    _clear_pid_file_safely()


# Secondary safety net for the normal (non-watchdog) shutdown path:
# register the pid-file removal as an atexit callback. atexit handlers
# are bypassed by ``os._exit(0)`` — the watchdog-killed path is covered
# separately by the explicit ``_clear_backend_pid_file()`` call inside
# ``lifecycle._watchdog`` BEFORE ``os._exit(0)``. This atexit hook only
# fires when Python's interpreter shutdown runs (e.g. main-thread
# ``SystemExit`` from ``quit()``, ``SIGTERM`` propagating to the main
# thread, or an uncaught exception bubbling past the crash handler).
# ``_clear_pid_file_safely`` is idempotent (the underlying helper
# checks ``pid_file.exists()`` before unlinking), so registering it
# alongside the explicit ``teardown_pid_file`` call is harmless.
atexit.register(_clear_pid_file_safely)


__all__ = ["teardown_pid_file"]
