"""Teardown helper for the history DB writer.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_history_db`. The body is unchanged;
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


def teardown_history_db(controller) -> None:
    """flush pending fire-and-forget history DB writes + close
    the DB (joins the writer thread).

    CRASH-SAFE-GAP-A: ``add_transcription()`` is fire-and-forget
    (enqueues the INSERT and returns immediately). If quit() exits
    without draining the queue, the writer thread (a daemon) is
    killed by the OS and any unprocessed INSERTs are silently lost.
    Flushing here ensures the writer drains its queue and commits
    all pending writes before the process terminates.

    Inner timeouts (flush + close) MUST be strictly less than
    the outer wrapper budget. Previously the inner timeouts were
    ``flush=10.0`` + ``close=5.0`` = 15s, exactly equal to the outer
    ``_run_with_timeout`` budget (15s) that ``_do_cleanup`` allocates
    for the ``teardown_history_db`` sequenced phase item. Zero slack
    meant a slow-but-not-stuck flush that took 9.9s left only 0.1s for
    close. The fix tightens the inner timeouts to ``flush=8.0`` +
    ``close=4.0`` = 12s, leaving 3s of slack under the 15s outer
    budget. The inner timeouts are enforced via the existing
    ``_run_with_timeout`` thread-join wrapper (HistoryDB.flush/close
    do not accept a ``timeout`` parameter themselves).

    ATOMICITY: flush() and close() run in SEPARATE try/except blocks.
    Pre-fix, both calls shared one try block — if flush() raised,
    close() was NEVER attempted and the SQLite connection + writer
    thread leaked until ``os._exit(0)`` killed them mid-WAL-write.
    The split guarantees close() runs even when flush() fails, so the
    writer thread is joined and the connection is released. The
    TIMEOUT sentinel returned by ``_run_with_timeout`` is also checked
    explicitly (was silently discarded pre-fix) so operators get a
    WARNING when the inner timeout fires and the leaked worker may
    still be racing ``close()`` for the same ``_write_lock``.
    """
    app = controller._app
    if app.history_db is None:
        return

    flush_err: Exception | None = None
    try:
        # 8.0s inner budget for flush — strictly less than the 15.0s
        # outer wrapper budget. See docstring.
        _run_with_timeout(
            "history_db.flush",
            app.history_db.flush,
            timeout=8.0,
        )
    except Exception as e:
        flush_err = e
        log.warning("[SHUTDOWN] history DB flush failed: %s", e)

    try:
        _result = _run_with_timeout(
            "history_db.close",
            app.history_db.close,
            timeout=4.0,
        )
        if _result is TIMEOUT:
            log.warning(
                "[SHUTDOWN] history DB close timed out (flush_err=%r)",
                flush_err,
            )
    except Exception as e:
        log.warning(
            "[SHUTDOWN] history DB close failed (flush_err=%r): %s",
            flush_err,
            e,
        )


__all__ = ["teardown_history_db"]
