"""Teardown helper for the level_monitor module's PortAudio stream.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_level_monitor`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

# ``_run_with_timeout`` is looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


log = logging.getLogger(__name__)


def teardown_level_monitor(controller) -> None:
    """stop the level_monitor module's PortAudio InputStream +
    worker thread.

    MED-NNN / XCUT-2: the level_monitor module owns its own
    PortAudio InputStream + worker thread as module-level globals
    that are NOT registered with ``app._thread_registry``. Without
    this call the stream + worker leak across restart_app().
    Best-effort — stop_monitoring() is itself idempotent.
    """
    try:
        from voice_typer.server import level_monitor

        _run_with_timeout(
            "level_monitor.stop_monitoring",
            level_monitor.stop_monitoring,
            timeout=5.0,
        )
    except Exception:
        log.warning(
            "[SHUTDOWN] level_monitor.stop_monitoring failed",
            exc_info=True,
        )


__all__ = ["teardown_level_monitor"]
