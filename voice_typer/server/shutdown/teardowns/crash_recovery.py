"""Teardown helper for the crash-recovery writer."""

from __future__ import annotations

import logging

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_crash_recovery(controller) -> None:
    """flush pending crash-recovery writes + shutdown the writer."""
    app = controller._app
    if app._crash_recovery is None:
        return

    flush_err: Exception | None = None
    try:
        app._crash_recovery.flush(timeout=2.0)
    except Exception as e:
        flush_err = e
        log.warning("[SHUTDOWN] crash recovery flush failed: %s", e)

    try:
        _result = _run_with_timeout(
            "crash_recovery.shutdown",
            app._crash_recovery.shutdown,
            timeout=5.0,
        )
        if _result is TIMEOUT:
            log.warning(
                "[SHUTDOWN] crash recovery shutdown timed out (flush_err=%r)",
                flush_err,
            )
    except Exception as e:
        log.warning(
            "[SHUTDOWN] crash recovery shutdown failed (flush_err=%r): %s",
            flush_err,
            e,
        )


__all__ = ["teardown_crash_recovery"]
