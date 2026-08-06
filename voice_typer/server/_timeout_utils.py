"""General-purpose thread-join timeout utilities ( extraction).

Extracted out of :mod:`voice_typer.server.shutdown_controller` so the
shutdown controller can focus on its core concern (orchestrating the
cleanup of every subsystem). These two helpers are pure thread-join
utilities with no shutdown-specific logic — they are useful anywhere a
caller needs to bound a blocking call with a hard timeout and detect
whether the worker actually finished.

* :data:`TIMEOUT` — sentinel returned by :func:`_run_with_timeout` when
  the worker thread did not finish within the timeout. Distinct from
  ``None`` so callers can reliably detect a timeout and apply per-
resource shutdown barriers () or hard-kill fallbacks (
  tray.stop -> ``os._exit(0)``).
* :func:`_run_with_timeout` — run *func* in a daemon worker thread
  with a hard timeout; return its result, or :data:`TIMEOUT` if it
  did not finish, or re-raise its exception.
* :func:`_run_parallel_with_timeout` — run several independent
  teardowns concurrently, capturing per-call failures into result
  tuples so one slow teardown does not mask failures from its peers.
* :func:`join_leaked_workers` — best-effort drain of the
  :data:`_LEAKED_WORKERS` registry; the shutdown watchdog calls this
  just before ``os._exit(0)`` so abandoned daemon workers get a
  bounded window to release resources.

The module also re-exports the watchdog constants
(``_DE11_GRACE_PERIOD_SECONDS`` / ``SHUTDOWN_WATCHDOG_TIMEOUT_S``) used
by :meth:`ShutdownController._arm_shutdown_watchdog` and tests that
patch them. ``shutdown_controller.py`` re-exports the same names for
backwards compatibility with existing test imports.

``_run_with_timeout`` tracks leaked worker threads
in the module-level :data:`_LEAKED_WORKERS` list (guarded by
:data:`_LEAKED_WORKERS_LOCK`). When a worker times out, it is appended
to the registry; :func:`join_leaked_workers` best-effort joins all
leaked workers (removing those that have exited) so the shutdown
watchdog can drain them before ``os._exit(0)``.

``_run_parallel_with_timeout`` raises ``ValueError`` if any
two items share the same ``desc`` — the previous dict-based reorder
silently dropped duplicate keys. Callers MUST pass unique descriptions.

``__all__`` exposes the canonical public names only.
``_TIMEOUT`` and ``_DE11_GRACE_PERIOD_SECONDS`` remain as module-level
aliases (back-compat for tests like ``test_shutdown_controller_de.py``
that import them directly) but are no longer in ``__all__``.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


# sentinel returned by ``_run_with_timeout`` when the worker
# thread did not finish within the timeout. Distinct from ``None`` so callers
# can reliably detect a timeout and apply per-resource shutdown barriers
# () or hard-kill fallbacks ( tray.stop -> os._exit(0)).
class _TimeoutSentinel:
    """Singleton sentinel signaling that ``_run_with_timeout`` abandoned
    its worker thread. Use ``is TIMEOUT`` to compare."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — cosmetic
        return "<TIMEOUT>"


TIMEOUT = _TimeoutSentinel()
# back-compat alias. Older tests (e.g.
# ``test_shutdown_controller_de.py``) import ``_TIMEOUT`` directly.
# Kept as a module-level name but removed from ``__all__`` so new
# callers use the canonical ``TIMEOUT``.
_TIMEOUT = TIMEOUT
# back-compat alias. Same rationale as ``_TIMEOUT``.
_DE11_GRACE_PERIOD_SECONDS: float = 2.0


# watchdog timeout for the non-main-thread ``quit()``
# ``restart_app()`` path. After ``_do_cleanup()`` completes, we arm a
# daemon-thread watchdog that calls ``os._exit(0)`` after this many
# seconds if the process is still alive (i.e. the main thread has not
# returned from ``tray.run()``). : the grace period is 2s (matches
# ``_DE11_GRACE_PERIOD_SECONDS`` and the SIGKILL escalation window in the
# legacy Electron termination path). Tests patch this to a shorter value
# to keep the suite fast.
SHUTDOWN_WATCHDOG_TIMEOUT_S: float = _DE11_GRACE_PERIOD_SECONDS


# module-level registry of leaked worker threads.
#
# When ``_run_with_timeout`` returns ``TIMEOUT``, the abandoned daemon
# worker is appended here. ``join_leaked_workers`` best-effort drains
# this list before the process calls ``os._exit(0)``. Guarded by
# ``_LEAKED_WORKERS_LOCK`` so concurrent callers (multiple
# ``_run_with_timeout`` invocations + the shutdown watchdog) can safely
# mutate the list without races.
#
# Workers are removed from this list by ``join_leaked_workers`` once
# they have exited (best-effort — if the worker never exits, it stays
# in the list until the process dies via ``os._exit(0)``).
_LEAKED_WORKERS: list[threading.Thread] = []
_LEAKED_WORKERS_LOCK = threading.Lock()


def join_leaked_workers(
    timeout: float = 1.0,
    *,
    total_budget: float | None = None,
) -> int:
    """Best-effort join of every leaked worker thread.

    when ``_run_with_timeout`` returns ``TIMEOUT``,
        the abandoned daemon worker is added to :data:`_LEAKED_WORKERS`.
        The shutdown watchdog calls this just before ``os._exit(0)`` so
        the leaked workers get a bounded window to release resources
        (PortAudio streams, file handles, locks) before the process is
        torn down.

        Two budget modes are supported:

        * **Per-worker** (default, ``timeout`` keyword): each leaked
          worker is joined with up to *timeout* seconds. The budget is
          per-worker, NOT shared — callers that need a global cap should
          pass a smaller value or use ``total_budget``. With N leaked
          workers, worst-case wall time = ``N * timeout``.
        * **Shared deadline** (``total_budget`` keyword): when
          provided, takes precedence over ``timeout``. A single global
          deadline (``time.monotonic() + total_budget``) is shared
          across all workers. Each worker is joined with
          ``min(0.2, remaining_budget)`` seconds, where
          ``remaining_budget = deadline - time.monotonic()``. The
          iteration is capped at the first 10 workers by registration
          order so the worst-case wall time is bounded by
          ``min(10 * 0.2, total_budget) = min(2.0, total_budget)``
          seconds. This is the mode the shutdown watchdog uses so its
          effective time stays bounded regardless of how many workers
          are in the registry.

        Threads that have already exited (or exit during the join) are
        removed from the registry. Threads still alive after the join
        remain in the registry — they are daemon threads, so
        ``os._exit(0)`` will reap them when the process dies.

        Thread-safe: takes :data:`_LEAKED_WORKERS_LOCK` to snapshot and
        prune the list. Safe to call concurrently with ``_run_with_timeout``
        (which may be appending new leaked workers).

        Parameters
        ----------
        timeout : float
            Per-worker join timeout in seconds (used when
            ``total_budget`` is None). ``0`` returns immediately (just
            prunes already-dead threads). Negative values are clamped
            to ``0.0``.
        total_budget : float | None
            Global shared deadline for ALL workers. When
            provided, takes precedence over ``timeout``: each worker
            is joined with ``min(0.2, remaining_budget)`` seconds, and
            the iteration is capped at the first 10 workers. Negative
            values are clamped to ``0.0``.

        Returns
        -------
        int
            Number of workers that were still alive after the join (i.e.
            the number of workers remaining in the registry). Useful for
            diagnostics / logging.
    """
    # Shared-deadline mode: cap worker count + use a single
    # global budget so the watchdog's effective time is bounded.
    if total_budget is not None:
        return _join_leaked_workers_with_budget(total_budget)

    if timeout < 0:
        timeout = 0.0
    # Snapshot under the lock so we do not mutate the list while
    # iterating (a concurrent ``_run_with_timeout`` could append).
    with _LEAKED_WORKERS_LOCK:
        snapshot = list(_LEAKED_WORKERS)
    if not snapshot:
        return 0
    for t in snapshot:
        if not t.is_alive():
            continue
        try:
            t.join(timeout=timeout)
        except Exception:  # noqa: BLE001 — best-effort; never propagate
            log.debug(
                "[TIMEOUT-UTILS] join_leaked_workers: join() raised for %r",
                t.name,
                exc_info=True,
            )
    # Prune dead threads from the registry (best-effort cleanup so
    # the list does not grow without bound if join_leaked_workers is
    # called repeatedly without os._exit(0) — e.g. in tests).
    with _LEAKED_WORKERS_LOCK:
        _LEAKED_WORKERS[:] = [t for t in _LEAKED_WORKERS if t.is_alive()]
        remaining = len(_LEAKED_WORKERS)
    if remaining:
        log.warning(
            "[TIMEOUT-UTILS] join_leaked_workers: %d worker(s) still alive "
            "after %.2fs per-worker join — they will be reaped by os._exit(0)",
            remaining,
            timeout,
        )
    return remaining


# Per-worker cap for the shared-deadline join mode. The
# watchdog's effective time is bounded by ``min(_MAX_WORKERS_TO_JOIN *
# 0.2, total_budget)`` = ``min(2.0, total_budget)`` seconds.
_MAX_WORKERS_TO_JOIN = 10
# Per-worker timeout cap in shared-deadline mode. Each worker
# gets at most this many seconds; the remaining budget is shared with
# subsequent workers.
_PER_WORKER_TIMEOUT_CAP_S = 0.2


def _join_leaked_workers_with_budget(total_budget: float) -> int:
    """Shared-deadline join. Each worker gets at most
    ``min(_PER_WORKER_TIMEOUT_CAP_S, remaining_budget)`` seconds; the
    iteration is capped at the first ``_MAX_WORKERS_TO_JOIN`` workers
    by registration order. The worst-case wall time is
    ``min(_MAX_WORKERS_TO_JOIN * _PER_WORKER_TIMEOUT_CAP_S, total_budget)``
    = ``min(2.0, total_budget)`` seconds, regardless of how many
    workers are in the registry.
    """
    import time as _time

    if total_budget < 0:
        total_budget = 0.0
    deadline = _time.monotonic() + total_budget
    # Snapshot under the lock so we do not mutate the list while
    # iterating. Cap at the first _MAX_WORKERS_TO_JOIN workers by
    # registration order (the list is append-ordered, so the first N
    # entries are the oldest leaked workers).
    with _LEAKED_WORKERS_LOCK:
        snapshot = list(_LEAKED_WORKERS[:_MAX_WORKERS_TO_JOIN])
    if not snapshot:
        return 0
    per_worker_used: list[float] = []
    for t in snapshot:
        remaining = deadline - _time.monotonic()
        if remaining <= 0.0:
            # Budget exhausted — stop iterating. Remaining workers
            # (including any beyond the cap) stay in the registry and
            # are reaped by os._exit(0).
            break
        if not t.is_alive():
            continue
        per_worker_timeout = min(_PER_WORKER_TIMEOUT_CAP_S, remaining)
        try:
            t.join(timeout=per_worker_timeout)
        except Exception:  # noqa: BLE001 — best-effort; never propagate
            log.debug(
                "[TIMEOUT-UTILS] join_leaked_workers: join() raised for %r",
                t.name,
                exc_info=True,
            )
        per_worker_used.append(per_worker_timeout)
    # Prune dead threads from the registry (best-effort cleanup so
    # the list does not grow without bound if join_leaked_workers is
    # called repeatedly without os._exit(0) — e.g. in tests).
    with _LEAKED_WORKERS_LOCK:
        _LEAKED_WORKERS[:] = [t for t in _LEAKED_WORKERS if t.is_alive()]
        remaining_count = len(_LEAKED_WORKERS)
    if remaining_count:
        avg_per_worker = sum(per_worker_used) / len(per_worker_used) if per_worker_used else 0.0
        log.warning(
            "[TIMEOUT-UTILS] join_leaked_workers: %d worker(s) still alive "
            "after shared-deadline join (total_budget=%.2fs, avg_per_worker="
            "%.3fs, capped_at=%d) — they will be reaped by os._exit(0)",
            remaining_count,
            total_budget,
            avg_per_worker,
            _MAX_WORKERS_TO_JOIN,
        )
    return remaining_count


def _run_with_timeout(description: str, func, timeout: float = 5.0):
    """run *func* in a worker thread with a hard timeout.

        Returns whatever ``func()`` returned, or :data:`TIMEOUT` (a sentinel
        distinct from ``None``) if it did not finish within *timeout* seconds.
        Re-raises any exception raised by ``func()`` so the caller's existing
        try/except still applies. The worker thread is daemon-marked so it
        does not block interpreter exit if it really is stuck.

    callers that share a resource across multiple
        ``_run_with_timeout`` calls (e.g. ``app.recorder`` for
        ``recorder.stop`` -> ``recorder.shutdown_mic_watcher``) MUST check the
        return value against :data:`TIMEOUT` and skip the downstream call
        when the upstream one timed out — otherwise the leaked worker thread
        races the next call on the same resource (PortAudio is not safe for
        concurrent stream operations from multiple threads).

    if the worker times out, it is added to the
        module-level :data:`_LEAKED_WORKERS` registry so the shutdown
        watchdog can best-effort join it before ``os._exit(0)`` (see
        :func:`join_leaked_workers`). Workers that eventually finish are
        pruned from the registry by ``join_leaked_workers``.
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
        # track the leaked worker so the shutdown
        # watchdog can drain it before os._exit(0). The worker is a
        # daemon, so it will not block process exit even if
        # join_leaked_workers is never called.
        with _LEAKED_WORKERS_LOCK:
            _LEAKED_WORKERS.append(t)
        log.warning(
            "[SHUTDOWN] %s did not finish in %.1fs — continuing "
            "(worker thread leaked as daemon, registered for "
            "best-effort join via join_leaked_workers)",
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
    """run several independent teardowns concurrently.

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

    *items* MUST have unique ``desc`` values. The result is
        re-ordered by ``desc`` to match input order, so duplicate
        descriptions would silently drop one of the results (the dict
        reorder would overwrite). This function raises ``ValueError`` if any
        two items share a description — callers MUST ensure uniqueness
        (e.g. by prefixing with a subsystem name).
    """
    import concurrent.futures

    if not items:
        return []
    # enforce uniqueness. The dict-based reorder below would
    # silently drop duplicate keys; raise here so the caller knows.
    # (We use a real raise rather than ``assert`` so the check survives
    # ``python -O`` — this is a public-API contract, not a debug aid.)
    descs = [desc for (desc, _func, _timeout) in items]
    if len(set(descs)) != len(items):
        seen: set[str] = set()
        duplicates: list[str] = []
        for d in descs:
            if d in seen and d not in duplicates:
                duplicates.append(d)
            seen.add(d)
        raise ValueError(
            f"_run_parallel_with_timeout: duplicate descriptions in "
            f"items (results would be silently dropped during "
            f"reorder). Duplicates: {duplicates}"
        )
    results: list[tuple[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(len(items), 8),
        thread_name_prefix="cleanup-parallel",
    ) as pool:
        future_map: dict = {}
        for (desc, func, timeout) in items:
            # RACE: ``pool.submit`` can raise ``RuntimeError('cannot
            # schedule new futures after interpreter shutdown')`` when
            # the main thread is tearing down concurrently (e.g. a
            # FATAL tray-crash path exits the interpreter while a
            # background startup thread is still in this helper -
            # observed in the ``voice-typer`` terminal run). The same
            # race was already guarded in ``transport_tcp.py``'
            # accept loop; here we record the failure as the item's
            # result tuple (``(desc, RuntimeError)``) instead of
            # letting it kill the calling thread with an unhandled
            # exception. Callers already treat ``BaseException``
            # results as per-item failures (log + continue), so no
            # caller change is needed.
            try:
                fut = pool.submit(_run_with_timeout, desc, func, timeout)
            except RuntimeError as exc:
                log.debug(
                    "[TIMEOUT-UTILS] %s: pool.submit rejected during interpreter shutdown (%s) - recording failure",
                    desc,
                    exc,
                )
                results.append((desc, exc))
                continue
            future_map[fut] = (desc, func, timeout)
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
    # Canonical sentinel + watchdog constant.
    "TIMEOUT",
    "SHUTDOWN_WATCHDOG_TIMEOUT_S",
    # Public helpers.
    "_run_with_timeout",
    "_run_parallel_with_timeout",
    "join_leaked_workers",
    # Exposed for ``isinstance`` / subclassing in tests.
    "_TimeoutSentinel",
    # NOTE: ``_TIMEOUT`` and ``_DE11_GRACE_PERIOD_SECONDS`` are
    # module-level aliases kept for back-compat with tests that import
    # them directly, but they are intentionally NOT in ``__all__``
    # () — new callers should use ``TIMEOUT``
    # ``SHUTDOWN_WATCHDOG_TIMEOUT_S``.
]
