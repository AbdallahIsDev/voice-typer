"""Teardown helper for the history DB writer.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_history_db`. The body is unchanged;
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


def teardown_history_db(controller) -> None:
    """flush pending fire-and-forget history DB writes + close
    the DB (joins the writer thread).

    CRASH-SAFE-GAP-A: ``add_transcription()`` is fire-and-forget
    (enqueues the INSERT and returns immediately). If quit() exits
    without draining the queue, the writer thread (a daemon) is
    killed by the OS and any unprocessed INSERTs are silently lost.
    Flushing here ensures the writer drains its queue and commits
    all pending writes before the process terminates.
    """
    app = controller._app
    try:
        if app.history_db is not None:
            _run_with_timeout(
                "history_db.flush",
                app.history_db.flush,
                timeout=10.0,
            )
            _run_with_timeout(
                "history_db.close",
                app.history_db.close,
                timeout=5.0,
            )
    except Exception as e:
        log.warning("[SHUTDOWN] history DB flush/close failed: %s", e)


__all__ = ["teardown_history_db"]
