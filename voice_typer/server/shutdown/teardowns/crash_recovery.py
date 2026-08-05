"""Teardown helper for the crash-recovery writer.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_crash_recovery`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

# ``_run_with_timeout`` / ``TIMEOUT`` are looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


TIMEOUT = _sc.TIMEOUT

log = logging.getLogger(__name__)


def teardown_crash_recovery(controller) -> None:
    """flush pending crash-recovery writes + shutdown the writer.

    RELIABILITY-005: flush before the process exits so the latest
    state is persisted. Short timeout — if the disk is genuinely
    slow we'd rather exit and lose the in-flight snapshot than hang
    the shutdown.

    ATOMICITY: flush() and shutdown() run in SEPARATE try/except
    blocks. Pre-fix, both calls shared one try block — if flush()
    raised, shutdown() was NEVER attempted and the writer thread +
    state file handle leaked. The split guarantees shutdown() runs
    even when flush() fails, so the writer thread is joined and the
    snapshot file is closed cleanly. The TIMEOUT sentinel returned
    by ``_run_with_timeout`` is checked explicitly (was silently
    discarded pre-fix) so operators get a WARNING when the inner
    timeout fires.
    """
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
