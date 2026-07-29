"""General-purpose thread-join timeout utilities (DR-28 extraction).

Extracted out of :mod:`voice_typer.server.shutdown_controller` so the
shutdown controller can focus on its core concern (orchestrating the
cleanup of every subsystem). These two helpers are pure thread-join
utilities with no shutdown-specific logic — they're useful anywhere a
caller needs to bound a blocking call with a hard timeout and detect
whether the worker actually finished.

* :data:`TIMEOUT` — sentinel returned by :func:`_run_with_timeout` when
  the worker thread did not finish within the timeout. Distinct from
  ``None`` so callers can reliably detect a timeout and apply per-
  resource shutdown barriers (GT-70) or hard-kill fallbacks (GT-43
  tray.stop → ``os._exit(0)``).
* :func:`_run_with_timeout` — run *func* in a daemon worker thread
  with a hard timeout; return its result, or :data:`TIMEOUT` if it
  didn't finish, or re-raise its exception.
* :func:`_run_parallel_with_timeout` — run several independent
  teardowns concurrently, capturing per-call failures into result
  tuples so one slow teardown does not mask failures from its peers.

The module also re-exports the watchdog constants
(``_DE11_GRACE_PERIOD_SECONDS`` / ``SHUTDOWN_WATCHDOG_TIMEOUT_S``) used
by :meth:`ShutdownController._arm_shutdown_watchdog` and tests that
patch them. ``shutdown_controller.py`` re-exports the same names for
backwards compatibility with existing test imports.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


# GT-43 / GT-70: sentinel returned by ``_run_with_timeout`` when the worker
# thread did not finish within the timeout. Distinct from ``None`` so callers
# can reliably detect a timeout and apply per-resource shutdown barriers
# (GT-70) or hard-kill fallbacks (GT-43 tray.stop → os._exit(0)).
class _TimeoutSentinel:
    """Singleton sentinel signaling that ``_run_with_timeout`` abandoned
    its worker thread. Use ``is TIMEOUT`` to compare."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — cosmetic
        return "<TIMEOUT>"


TIMEOUT = _TimeoutSentinel()
_TIMEOUT = TIMEOUT
_DE11_GRACE_PERIOD_SECONDS: float = 2.0


# GT-43: watchdog timeout for the non-main-thread ``quit()`` /
# ``restart_app()`` path. After ``_do_cleanup()`` completes, we arm a
# daemon-thread watchdog that calls ``os._exit(0)`` after this many
# seconds if the process is still alive (i.e. the main thread hasn't
# returned from ``tray.run()``). DE-11: the grace period is 2s (matches
# ``_DE11_GRACE_PERIOD_SECONDS`` and the SIGKILL escalation window in the
# legacy Electron termination path). Tests patch this to a shorter value
# to keep the suite fast.
SHUTDOWN_WATCHDOG_TIMEOUT_S: float = _DE11_GRACE_PERIOD_SECONDS


def _run_with_timeout(description: str, func, timeout: float = 5.0):
    """PVT-G5-057: run *func* in a worker thread with a hard timeout.

    Returns whatever ``func()`` returned, or :data:`TIMEOUT` (a sentinel
    distinct from ``None``) if it did not finish within *timeout* seconds.
    Re-raises any exception raised by ``func()`` so the caller's existing
    try/except still applies. The worker thread is daemon-marked so it
    doesn't block interpreter exit if it really is stuck.

    GT-70: callers that share a resource across multiple
    ``_run_with_timeout`` calls (e.g. ``app.recorder`` for
    ``recorder.stop`` → ``recorder.shutdown_mic_watcher``) MUST check the
    return value against :data:`TIMEOUT` and skip the downstream call
    when the upstream one timed out — otherwise the leaked worker thread
    races the next call on the same resource (PortAudio is not safe for
    concurrent stream operations from multiple threads).
    """
    result_holder: dict = {}

    def _worker() -> None:
        try:
            result_holder["value"] = func()
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            result_holder["error"] = exc

    t = threading.Thread(
        target=_worker,
        daemon=True,
        name=f"cleanup-{description}",
    )
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        log.warning(
            "[SHUTDOWN] %s did not finish in %.1fs — continuing (worker thread leaked as daemon)",
            description,
            timeout,
        )
        return TIMEOUT
    if "error" in result_holder:
        raise result_holder["error"]
    return result_holder.get("value")


def _run_parallel_with_timeout(
    items: list[tuple[str, object, float]],
) -> list[tuple[str, object]]:
    """XV-7: run several independent teardowns concurrently.

    Each entry in *items* is ``(description, func, timeout)``. Returns a
    list aligned with *items* of ``(description, result)`` where *result*
    is either the function's return value, :data:`TIMEOUT`, or the
    exception instance the function raised (caller decides whether to
    re-raise / log / ignore). Exceptions are NEVER raised out of this
    helper — every per-call failure is captured into the result tuple so
    one slow teardown does not mask failures from its peers.

    Used by ``_do_cleanup`` to parallelize teardowns that touch disjoint
    resources (e.g. the three hotkey backends). The teardowns MUST be
    genuinely independent — concurrent access to a shared resource
    (PortAudio, SQLite connection, pystray loop) is unsafe.
    """
    import concurrent.futures

    if not items:
        return []
    results: list[tuple[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(items), 8),
        thread_name_prefix="cleanup-parallel",
    ) as pool:
        future_map: dict = {
            pool.submit(_run_with_timeout, desc, func, timeout): (desc, func, timeout)
            for (desc, func, timeout) in items
        }
        for fut in concurrent.futures.as_completed(future_map):
            desc, _func, _timeout = future_map[fut]
            try:
                value = fut.result()
            except BaseException as exc:  # noqa: BLE001 — captured per-call
                value = exc
            results.append((desc, value))
    # Re-order to match input order so callers can index by position.
    by_desc = {desc: value for (desc, value) in results}
    return [(desc, by_desc[desc]) for (desc, _func, _timeout) in items]


__all__ = [
    "TIMEOUT",
    "_TIMEOUT",
    "_DE11_GRACE_PERIOD_SECONDS",
    "SHUTDOWN_WATCHDOG_TIMEOUT_S",
    "_TimeoutSentinel",
    "_run_with_timeout",
    "_run_parallel_with_timeout",
]
