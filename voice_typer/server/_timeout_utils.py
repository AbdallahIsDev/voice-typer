"""Timeout helpers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence

log = logging.getLogger(__name__)


# sentinel returned by ``_run_with_timeout`` when the worker
class _TimeoutSentinel:
    """Singleton sentinel signaling that ``_run_with_timeout`` abandoned
    its worker thread. Use ``is TIMEOUT`` to compare."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover, cosmetic
        return "<TIMEOUT>"


TIMEOUT = _TimeoutSentinel()
# back-compat alias. Older tests (e.g.
_TIMEOUT = TIMEOUT
# back-compat alias. Same rationale as ``_TIMEOUT``.
_DE11_GRACE_PERIOD_SECONDS: float = 1.0


# watchdog timeout for the non-main-thread ``quit()``
SHUTDOWN_WATCHDOG_TIMEOUT_S: float = _DE11_GRACE_PERIOD_SECONDS


# module-level registry of leaked worker threads.
_MAX_LEAKED_WORKERS = 64
_LEAKED_WORKERS: list[threading.Thread] = []
_LEAKED_WORKERS_LOCK = threading.Lock()


def join_leaked_workers(
    timeout: float = 1.0,
    *,
    total_budget: float | None = None,
) -> int:
    """Best-effort join of every leaked worker thread."""
    # Shared-deadline mode: cap worker count + use a single
    if total_budget is not None:
        return _join_leaked_workers_with_budget(total_budget)

    if timeout < 0:
        timeout = 0.0
    # Snapshot under the lock so we do not mutate the list while
    with _LEAKED_WORKERS_LOCK:
        snapshot = list(_LEAKED_WORKERS)
    if not snapshot:
        return 0
    for t in snapshot:
        if not t.is_alive():
            continue
        try:
            t.join(timeout=timeout)
        except Exception:  # noqa: BLE001, best-effort; never propagate
            log.debug(
                "[TIMEOUT-UTILS] join_leaked_workers: join() raised for %r",
                t.name,
                exc_info=True,
            )
    # Prune dead threads from the registry (best-effort cleanup so
    with _LEAKED_WORKERS_LOCK:
        _LEAKED_WORKERS[:] = [t for t in _LEAKED_WORKERS if t.is_alive()]
        remaining = len(_LEAKED_WORKERS)
    if remaining:
        log.warning(
            "[TIMEOUT-UTILS] join_leaked_workers: %d workers still alive "
            "after %.2fs per-worker join, they will be reaped by os._exit(0)",
            remaining,
            timeout,
        )
    return remaining


# Per-worker cap for the shared-deadline join mode. The
_MAX_WORKERS_TO_JOIN = 10
# Per-worker timeout cap in shared-deadline mode. Each worker
_PER_WORKER_TIMEOUT_CAP_S = 0.2


def _join_leaked_workers_with_budget(total_budget: float) -> int:
    """Shared-deadline join. Each worker gets at most"""
    import time as _time

    if total_budget < 0:
        total_budget = 0.0
    deadline = _time.monotonic() + total_budget
    # Snapshot under the lock so we do not mutate the list while
    with _LEAKED_WORKERS_LOCK:
        snapshot = list(_LEAKED_WORKERS[:_MAX_WORKERS_TO_JOIN])
    if not snapshot:
        return 0
    per_worker_used: list[float] = []
    for t in snapshot:
        remaining = deadline - _time.monotonic()
        if remaining <= 0.0:
            # Budget exhausted. Stop iterating. Remaining workers
            break
        if not t.is_alive():
            continue
        per_worker_timeout = min(_PER_WORKER_TIMEOUT_CAP_S, remaining)
        try:
            t.join(timeout=per_worker_timeout)
        except Exception:  # noqa: BLE001, best-effort; never propagate
            log.debug(
                "[TIMEOUT-UTILS] join_leaked_workers: join() raised for %r",
                t.name,
                exc_info=True,
            )
        per_worker_used.append(per_worker_timeout)
    # Prune dead threads from the registry (best-effort cleanup so
    with _LEAKED_WORKERS_LOCK:
        _LEAKED_WORKERS[:] = [t for t in _LEAKED_WORKERS if t.is_alive()]
        remaining_count = len(_LEAKED_WORKERS)
    if remaining_count:
        avg_per_worker = sum(per_worker_used) / len(per_worker_used) if per_worker_used else 0.0
        log.warning(
            "[TIMEOUT-UTILS] join_leaked_workers: %d workers still alive "
            "after shared-deadline join (total_budget=%.2fs, avg_per_worker="
            "%.3fs, capped_at=%d), they will be reaped by os._exit(0)",
            remaining_count,
            total_budget,
            avg_per_worker,
            _MAX_WORKERS_TO_JOIN,
        )
    return remaining_count


def _run_with_timeout(description: str, func, timeout: float = 5.0):
    """run *func* in a worker thread with a hard timeout.

    Returns whatever ``func()`` returned, or :data:`TIMEOUT` (a sentinel
    """
    result_holder: dict = {}

    def _worker() -> None:
        try:
            result_holder["value"] = func()
        except BaseException as exc:  # noqa: BLE001, re-raised below
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
        with _LEAKED_WORKERS_LOCK:
            # Opportunistic prune: drop workers that already exited so
            if len(_LEAKED_WORKERS) >= _MAX_LEAKED_WORKERS:
                _LEAKED_WORKERS[:] = [w for w in _LEAKED_WORKERS if w.is_alive()]
            # Hard cap: evict the OLDEST entry if the registry is still
            while len(_LEAKED_WORKERS) >= _MAX_LEAKED_WORKERS:
                evicted = _LEAKED_WORKERS.pop(0)
                log.warning(
                    "[TIMEOUT-UTILS] leaked-worker registry at cap "
                    "(%d), evicted oldest entry %r (daemon thread "
                    "remains reaped by process exit)",
                    _MAX_LEAKED_WORKERS,
                    evicted.name,
                )
            _LEAKED_WORKERS.append(t)
        log.warning(
            "[SHUTDOWN] %s did not finish in %.1fs, continuing "
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
    items: Sequence[tuple[str, Callable[[], object], float]],
) -> list[tuple[str, object]]:
    """run several independent teardowns concurrently."""
    import concurrent.futures

    if not items:
        return []
    # enforce uniqueness. The dict-based reorder below would
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
        for desc, func, timeout in items:
            # RACE: ``pool.submit`` can raise ``RuntimeError('cannot
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
            except BaseException as exc:  # noqa: BLE001, captured per-call
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
]
