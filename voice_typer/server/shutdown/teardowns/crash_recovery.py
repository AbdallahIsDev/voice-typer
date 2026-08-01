"""Teardown helper for the crash-recovery writer.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_crash_recovery`. The body is unchanged;
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


def teardown_crash_recovery(controller) -> None:
    """flush pending crash-recovery writes + shutdown the writer.

    RELIABILITY-005: flush before the process exits so the latest
    state is persisted. Short timeout — if the disk is genuinely
    slow we'd rather exit and lose the in-flight snapshot than hang
    the shutdown.
    """
    app = controller._app
    try:
        if app._crash_recovery is not None:
            app._crash_recovery.flush(timeout=2.0)
            _run_with_timeout(
                "crash_recovery.shutdown",
                app._crash_recovery.shutdown,
                timeout=5.0,
            )
    except Exception as e:
        log.warning("[SHUTDOWN] crash recovery flush failed: %s", e)


__all__ = ["teardown_crash_recovery"]
