"""WS dispatch-pool drain (extracted from ``shutdown_controller``).

Houses the body of :meth:`ShutdownController._drain_ws_dispatch_pool` —
the early bookend of ``_do_cleanup`` that stops the IPC server and
drains / cancels in-flight WS dispatch requests BEFORE any subsystem
teardown, concurrently, in a single ``_run_parallel_with_timeout`` batch.

The controller keeps a thin delegate on :class:`CleanupMixin`
(``shutdown_controller/_cleanup.py``) so the instance-method API used by
``do_cleanup`` (``controller._drain_ws_dispatch_pool(app)``) and by tests
continues to work — same convention as
:mod:`voice_typer.server.shutdown.teardowns`.

Patch-path note: ``_run_parallel_with_timeout`` is imported at module
level from :mod:`voice_typer.server._timeout_utils` — the same binding
strategy the body had in ``shutdown_controller/_cleanup.py``. Tests that
spy on the early-bookend batch patch THIS module's binding
(``voice_typer.server.shutdown.ws_drain._run_parallel_with_timeout``).

The logger keeps the pre-extraction name
(``voice_typer.server.shutdown_controller``) so log records emitted by the
moved body land on the same logger as before.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from voice_typer.server._timeout_utils import _run_parallel_with_timeout

log = logging.getLogger("voice_typer.server.shutdown_controller")


def drain_ws_dispatch_pool(controller, app) -> None:
    """Early bookend: stop the IPC server + drain the WS dispatch pool.

    Extracted from ``_do_cleanup``. Stops the IPC server
    EARLY so inbound requests can't resurrect torn-down subsystems,
    and drains / cancels in-flight WS dispatch requests BEFORE any
    subsystem teardown — concurrently, in a single
    ``_run_parallel_with_timeout`` batch. They touch disjoint pools
    (the TCP worker pool and the WS dispatch pool), so
    parallelisation is safe. ``_shutting_down`` is already True (set
    by ``quit()`` before calling ``_do_cleanup``), so the
    ``sidecar_ws._make_dispatch`` ``dispatch`` coroutine is already
    rejecting NEW requests. Best-effort — failures here don't
    prevent the rest of cleanup from running.

    Preserves the ``if join_thread.is_alive():`` drain-timeout
    branch (pinned by
    ``tests/test_shutdown_fast_path.py::TestOsExitOnStuckWsDrain::
    test_ws_drain_timeout_branch_exists``).

    ``controller`` is unused in the body (the drain only touches
    ``app``) but kept for API symmetry with the other extracted
    shutdown functions, mirroring ``run_plan``'s ``controller``
    parameter.
    """
    try:
        ipc_server = getattr(app, "_ipc_server", None)
        ws_pool = getattr(ipc_server, "_ws_dispatch_pool", None) if ipc_server is not None else None

        early_items: list[tuple[str, Callable[[], object], float]] = []
        if ipc_server is not None:
            # PERF-SHUTDOWN-002: the ipc_server.stop budget was 5.0s
            # pre-quit-latency-fix. ``stop()`` gates its pool drains
            # on ``app._shutting_down`` (always True on this path),
            # so it returns in milliseconds; 2.0s is now a generous
            # hard ceiling that still bounds teardown if a future
            # regression re-introduces a blocking path.
            early_items.append(("ipc_server.stop", ipc_server.stop, 2.0))

        if ws_pool is not None and hasattr(ws_pool, "shutdown"):

            def _drain_ws_pool() -> None:
                # ``shutdown(wait=False, cancel_futures=True)`` only
                # cancels QUEUED (not-yet-started) tasks; RUNNING handlers
                # continue. Without a bounded join, teardown races any
                # in-flight WS handler that touches the recorder /
                # history_db / crash_recovery subsystems. Spawn a
                # daemon-thread ``shutdown(wait=True)`` and join the
                # spawner with a 5s hard deadline (generous for any single
                # handler, short enough to bound teardown). If the drain
                # doesn't complete in 5s, log + proceed.
                ws_pool.shutdown(wait=False, cancel_futures=True)
                log.debug("[SHUTDOWN] WS dispatch pool shut down (cancel_futures=True)")
                join_thread = threading.Thread(
                    target=ws_pool.shutdown,
                    kwargs={"wait": True},
                    daemon=True,
                )
                join_thread.start()
                # 4.5s — deliberately UNDER this item's 5.0s parallel
                # budget: the inner join must expire BEFORE the outer
                # ``_run_parallel_with_timeout`` cutoff, otherwise the
                # two identical deadlines race and the diagnostic
                # WARNING can lose (observed on loaded CI runners:
                # outer timeout fired first, the item was abandoned,
                # and the WARNING never landed).
                join_thread.join(timeout=4.5)
                if join_thread.is_alive():
                    log.warning("[SHUTDOWN] ws_dispatch_pool did not drain in 5s — proceeding anyway")

            early_items.append(("ws_dispatch_pool.drain", _drain_ws_pool, 5.0))

        if early_items:
            _run_parallel_with_timeout(early_items)

        # explicit ``threading.Event`` coordination between the WS
        # dispatch path and ``_do_cleanup``. The pool's ``shutdown(wait=True)``
        # (run above) only guarantees that the ThreadPoolExecutor drained
        # its worker queue — it does NOT guarantee that the per-dispatch
        # coroutine body finished its DB write (the WS ``dispatch``
        # coroutine may still be in its ``await loop.run_in_executor``
        # unwind / result-serialisation tail when the pool reports drained).
        # ``sidecar_ws._make_dispatch`` clears ``_ws_drained_event`` on
        # entry to each dispatch and sets it when the in-flight count drops
        # to zero (after the dispatch body fully returns — including the
        # post-Future unwind). We wait on that Event here, bounded by 2s,
        # BEFORE allowing the parallel teardown batch to proceed. If the
        # wait times out, we log and proceed (the in-flight handler is on
        # its own).
        if ipc_server is not None:
            ws_drained_event = getattr(ipc_server, "_ws_drained_event", None)
            if ws_drained_event is not None:
                # Skip the 2s wait when the WS pool is already idle
                # (``_ws_inflight_count == 0``). The
                # ``sidecar_ws._make_dispatch`` lazily attaches
                # ``_ws_inflight_count`` (an int, initially 0) on
                # first dispatch; before any dispatch has ever
                # fired, the attribute is missing —
                # ``getattr(..., 0)`` falls back to 0 and the wait
                # is skipped (no in-flight handler can race DB
                # teardown when the pool has never been used).
                # When ``_ws_inflight_count > 0``, the original 2s
                # bounded wait is kept so an in-flight handler
                # gets its bounded window to finish its DB write
                # before ``_teardown_history_db`` starts.
                ws_inflight = getattr(ipc_server, "_ws_inflight_count", 0)
                if ws_inflight == 0:
                    log.debug(
                        "[SHUTDOWN] ws_drained_event.wait skipped "
                        "(_ws_inflight_count=0 — no in-flight WS handler "
                        "can race DB teardown)"
                    )
                else:
                    drained = ws_drained_event.wait(timeout=2.0)
                    if not drained:
                        in_flight = getattr(ipc_server, "_ws_inflight_count", 0)
                        # drain-timeout branch — log at WARNING and
                        # proceed (never block) so an in-flight write can't
                        # stall shutdown.
                        log.warning(
                            "[SHUTDOWN] WS dispatch drain Event did not "
                            "fire in 2s — %s in-flight handlers may race DB "
                            "teardown; proceeding with cleanup (the in-flight "
                            "write may silently fail)",
                            in_flight,
                        )
    except Exception:
        log.debug(
            "[SHUTDOWN] early bookend (ipc_server.stop + WS drain) failed",
            exc_info=True,
        )


__all__ = ["drain_ws_dispatch_pool"]
