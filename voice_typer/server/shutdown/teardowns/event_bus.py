"""Teardown helper for the event_bus deferred-publish executor.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_event_bus`. The body is unchanged;
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


def teardown_event_bus(controller) -> None:
    """shut down the event_bus deferred-publish executor.

    M-22: this is the LAST module-level cleanup because earlier
    steps (bubble worker stop, recorder stop, hotkey stop) can each
    publish events via ``event_bus.publish``, and an RT-thread
    publish defers to this executor. Shutting it down here ensures
    no deferred ``_deliver`` tasks outlive the subsystems they
    deliver TO.

    ``event_bus.shutdown`` now calls
    ``executor.shutdown(wait=True, cancel_futures=True)`` so the
    5s ``_run_with_timeout`` wrapper ACTUALLY bounds the wait
    (previously ``wait=False`` returned immediately and the
    non-daemon worker thread lingered past the 5s "timeout").
    Idempotent — safe under the ``_do_cleanup`` double-call guard.

    The ``controller`` argument is unused but kept for API symmetry
    with the other teardown helpers (all take ``controller`` as the
    first positional arg so the :class:`ShutdownController` delegate
    methods can call ``<helper>(self)`` uniformly).
    """
    try:
        from voice_typer.server import event_bus as _event_bus

        _run_with_timeout(
            "event_bus.shutdown",
            _event_bus.shutdown,
            timeout=5.0,
        )
    except Exception:
        log.debug("[CLEANUP] event_bus.shutdown failed", exc_info=True)


__all__ = ["teardown_event_bus"]
