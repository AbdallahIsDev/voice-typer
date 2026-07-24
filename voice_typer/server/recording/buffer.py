"""Securely zero audio buffers on a background worker thread.

Phase 4.5 / ARCH-045 — extracted from the original ``recording.py``
god-module.  Owns the long-lived buffer-clear worker thread and its
bounded queue (CR-10).

The public names ``_secure_clear_array``,
``_ensure_buffer_clear_worker``, ``_buffer_clear_worker_loop``,
``_secure_clear_array_background``, ``_buffer_clear_queue``,
``_buffer_clear_worker``, ``_BUFFER_CLEAR_QUEUE_MAXSIZE`` are
re-exported from ``voice_typer.server.recording`` (the package
``__init__.py``) so existing imports keep working unchanged.

The mutable ``_buffer_clear_worker`` global lives here.  The package
``__init__.py`` routes reads/writes of ``recording._buffer_clear_worker``
through to this submodule via a custom module ``__getattr__`` /
``__setattr__`` (see ``recording/__init__.py``).
"""

from __future__ import annotations

import collections
import contextlib
import logging
import queue
import threading
import time
from typing import Any

import numpy as np

# All submodules use the package-level logger so log records propagate
# to ``caplog.at_level(..., logger="voice_typer.server.recording")`` in
# tests.
log = logging.getLogger("voice_typer.server.recording")


def _secure_clear_array(arr: np.ndarray) -> None:
    """SEC-audit-008: Securely clear a numpy array's contents before deallocation.

    Fills the array with zeros using ``np.fill()`` to prevent forensic
    recovery of audio data from process memory.  Call this before
    ``del`` or before the array goes out of scope when the array
    contains sensitive audio data (voice recordings that may contain
    PII).

    Parameters
    ----------
    arr : np.ndarray
        Numpy array to zero out in-place.
    """
    with contextlib.suppress(Exception):
        arr.fill(0)  # best-effort; some array types may not support fill


# CR-10: A single long-lived "buffer-clear" worker thread replaces the
# previous per-call ``threading.Thread(name="buffer-clear-bg", daemon=True)``
# spawn. Under rapid hotkey toggling (e.g. user mashing the record key),
# ``stop()`` / ``discard()`` could be invoked several times per second, and
# each invocation spawned a brand-new daemon thread. Over a long session
# this produced unbounded thread churn (thread creation/destruction cost,
# extra scheduler pressure, and a small but non-zero risk of hitting the
# process thread-table ceiling on constrained platforms).
#
# The fix is a classic producer/consumer: callers enqueue the deque to be
# zeroed onto ``_buffer_clear_queue``; a single daemon worker thread
# (``_buffer_clear_worker``) drains the queue and zeros each deque's
# chunks in turn. The worker is created lazily on first enqueue (under a
# lock) so simply importing the module has no thread-creation side
# effects — important for tests and for short-lived CLI invocations.
#
# The queue is bounded (``_BUFFER_CLEAR_QUEUE_MAXSIZE``). In practice it
# should never fill: the worker zeros ~30K chunks in ~30-100ms, so it
# drains far faster than even a sustained hotkey toggle rate could
# produce new buffers. But if it ever *does* fill (e.g. a runaway test
# loop, or a system so loaded that the worker is starved for seconds),
# we fall back to clearing the deque synchronously on the caller's
# thread with a single warning log. That preserves the secure-clear
# guarantee (data is still zeroed) at the cost of a one-off blocking
# call — a strictly better failure mode than dropping the clear.
_BUFFER_CLEAR_QUEUE_MAXSIZE = 64
_buffer_clear_queue: queue.Queue = queue.Queue(maxsize=_BUFFER_CLEAR_QUEUE_MAXSIZE)
_buffer_clear_worker_lock = threading.Lock()
_buffer_clear_worker: threading.Thread | None = None

# R4-F8: worker thread name (re-exported for tests / ThreadRegistry).
BUFFER_CLEAR_WORKER_NAME = "buffer-clear-bg"

# Test-only join timeout for the buffer-clear worker. Generous because
# the worker may be in the middle of zeroing a large deque when the
# sentinel arrives.
_BUFFER_CLEAR_WORKER_JOIN_TIMEOUT_S = 5.0

# R4-F8: optional central ThreadRegistry for shutdown coordination.
# When set via ``set_thread_registry``, the lazily-started buffer-clear
# worker registers itself so ``shutdown_all()`` can signal/join it during
# ``VoiceTyperApp.quit()``. Mirrors the scipy-preloader pattern in
# ``recorder.py``. ``None`` (the default) means no registry — behaviour
# is unchanged (the worker is still tracked via the module-global
# ``_buffer_clear_worker`` and can be joined by
# ``_stop_buffer_clear_worker``).
_thread_registry: Any | None = None


def set_thread_registry(registry: Any | None) -> None:
    """R4-F8: install a central ThreadRegistry for shutdown coordination.

    When called BEFORE the worker is started, the next
    ``_ensure_buffer_clear_worker`` call will register the new worker.

    When called AFTER the worker is already running, the running worker
    is registered immediately (mirrors the scipy-preloader pattern in
    ``recorder.py``). This is needed because the worker may have been
    lazily started by an earlier ``_secure_clear_array_background`` call
    that ran before the registry was set.

    Passing ``None`` clears the registry — subsequent worker starts will
    not register. The already-running worker (if any) is left alone.

    GT-47: the read of ``_buffer_clear_worker`` and the subsequent call
    to ``registry.register(...)`` are now performed under
    ``_buffer_clear_worker_lock``. Previously the read happened outside
    the lock — a concurrent ``_stop_buffer_clear_worker`` could clear
    the global to ``None`` and the underlying worker thread could exit
    between our read and the ``register`` call, leaving the central
    ThreadRegistry with a stale/dead thread reference that
    ``shutdown_all()`` would later try to join (no-op join on a dead
    thread, but the registry entry would never be cleaned up).
    """
    global _thread_registry
    _thread_registry = registry
    if registry is not None:
        # If the worker is already running, register it immediately so
        # ``shutdown_all()`` can join it. Mirrors the scipy-preloader
        # pattern in ``recorder.py`` __init__.
        #
        # GT-47: hold ``_buffer_clear_worker_lock`` across the read +
        # ``register`` call so a concurrent
        # ``_stop_buffer_clear_worker`` cannot clear the global between
        # the read and the register (which would register a stale/dead
        # thread reference).
        with _buffer_clear_worker_lock:
            worker = _buffer_clear_worker
            if worker is not None and worker.is_alive():
                with contextlib.suppress(Exception):
                    registry.register(
                        name=BUFFER_CLEAR_WORKER_NAME,
                        thread=worker,
                        stop_event=None,
                        join_timeout=_BUFFER_CLEAR_WORKER_JOIN_TIMEOUT_S,
                    )


def _stop_buffer_clear_worker(timeout: float = 2.0) -> bool:
    """R4-F8 (test-only helper): signal the buffer-clear worker to stop
    and join it.

    Sends the ``None`` sentinel through ``_buffer_clear_queue`` so the
    worker's loop exits on its next iteration, then joins the worker
    thread for up to ``timeout`` seconds.

    Returns ``True`` if the worker exited within the timeout (or no
    worker was running), ``False`` if it timed out. Idempotent: safe to
    call when the worker is not running (returns ``True`` immediately).

    After this returns, ``_buffer_clear_worker`` is set to ``None`` so
    the next ``_ensure_buffer_clear_worker`` call lazily starts a fresh
    worker. If the worker failed to exit within the timeout, the global
    reference is still cleared (the daemon will exit on its next
    iteration when it sees the sentinel).
    """
    global _buffer_clear_worker
    worker = _buffer_clear_worker
    if worker is None:
        return True
    # Send the None sentinel so the worker exits its loop. ``put_nowait``
    # because the queue is bounded — if it's full (worker starved for an
    # extended period), we still proceed with the join; the daemon will
    # exit on its next iteration when it eventually drains to the
    # sentinel.
    with contextlib.suppress(queue.Full):
        _buffer_clear_queue.put_nowait(None)
    worker.join(timeout=timeout)
    exited = not worker.is_alive()
    if not exited:
        log.warning(
            "[RECORDING] buffer-clear worker did not exit within %.1fs (it will exit as a daemon on next iteration)",
            timeout,
        )
    else:
        log.debug("[RECORDING] buffer-clear worker exited cleanly")
    # Clear the global reference whether or not the join succeeded. The
    # daemon will exit on its next iteration when it sees the sentinel;
    # the next ``_ensure_buffer_clear_worker`` call will start a fresh
    # worker if needed.
    with _buffer_clear_worker_lock:
        if _buffer_clear_worker is worker:
            _buffer_clear_worker = None
    return exited


def _ensure_buffer_clear_worker() -> threading.Thread:
    """Lazily start the single long-lived buffer-clear worker thread.

    CR-10: idempotent — repeated calls return the same running thread.
    The worker is a daemon so it never blocks process exit. Acquired
    under ``_buffer_clear_worker_lock`` to make the lazy-start race-free
    under concurrent ``stop()``/``discard()`` calls.

    R4-F8: when ``set_thread_registry`` was previously called with a
    non-None registry, the freshly-started worker is registered so
    ``shutdown_all()`` can signal/join it during ``VoiceTyperApp.quit()``.
    The registry entry is removed by ``_stop_buffer_clear_worker`` after
    the join completes (or times out) so a subsequent lazy-start
    re-registers cleanly without triggering the
    "Re-registering name" warning.
    """
    global _buffer_clear_worker
    # Fast path: worker already running. ``threading.Thread.is_alive``
    # is a cheap C-level check; we avoid the lock in the common case.
    worker = _buffer_clear_worker
    if worker is not None and worker.is_alive():
        return worker
    with _buffer_clear_worker_lock:
        worker = _buffer_clear_worker
        if worker is None or not worker.is_alive():
            worker = threading.Thread(
                target=_buffer_clear_worker_loop,
                name=BUFFER_CLEAR_WORKER_NAME,
                daemon=True,
            )
            _buffer_clear_worker = worker
            worker.start()
            # R4-F8: register the freshly-started worker with the
            # central ThreadRegistry (if one was set). Best-effort —
            # if register() raises, the worker is still running and
            # tracked via the module-global.
            if _thread_registry is not None:
                with contextlib.suppress(Exception):
                    _thread_registry.register(
                        name=BUFFER_CLEAR_WORKER_NAME,
                        thread=worker,
                        stop_event=None,
                        join_timeout=_BUFFER_CLEAR_WORKER_JOIN_TIMEOUT_S,
                    )
    return worker


def _buffer_clear_worker_loop() -> None:
    """CR-10: drain ``_buffer_clear_queue`` and zero each deque's chunks.

    Loops until a ``None`` sentinel is popped from the queue (sent by
    ``_stop_buffer_clear_worker``). Each non-None item popped is a
    ``collections.deque`` of audio chunks; we iterate it and
    ``ndarray.fill(0)`` each chunk. Best effort — any exception is
    swallowed (the deque will be GC'd anyway, and we don't want one bad
    buffer to poison the worker for the rest).
    """
    while True:
        try:
            buffer = _buffer_clear_queue.get()
        except Exception:
            # Should never happen with a stdlib Queue, but be defensive:
            # if get() ever raises, yield the CPU and retry rather than
            # spinning a tight loop.
            time.sleep(0.01)
            continue
        try:
            if buffer is None:
                # R4-F8: sentinel from ``_stop_buffer_clear_worker`` —
                # exit the loop cleanly so the worker thread can be
                # joined by tests / shutdown_all().
                return
            for chunk in buffer:
                if isinstance(chunk, np.ndarray):
                    chunk.fill(0)
        except Exception:
            pass  # best-effort; the buffer will be GC'd anyway
        finally:
            _buffer_clear_queue.task_done()


def _secure_clear_array_background(buffer: collections.deque) -> None:
    """SEC-audit-008 / MEM-04: Zero all chunks in a buffer on a background worker.

    CR-10: previously this function spawned a fresh daemon thread per
    call. Under rapid hotkey toggling that produced unbounded thread
    churn. It now enqueues the deque onto ``_buffer_clear_queue`` and a
    single long-lived daemon worker (``_buffer_clear_worker``) drains
    the queue and zeros each chunk.

    The old buffer reference is passed in; the caller has already
    replaced it with a fresh deque, so the worker can zero the chunks
    at its leisure without blocking the hot path.

    If the queue is full (worker starved for an extended period — should
    not happen in practice), we fall back to a synchronous clear on the
    caller's thread with a single warning, preserving the secure-clear
    guarantee rather than dropping it silently.
    """
    _ensure_buffer_clear_worker()
    try:
        _buffer_clear_queue.put_nowait(buffer)
    except queue.Full:
        log.warning(
            "[RECORDING] buffer-clear queue full (size=%d); clearing "
            "synchronously on caller thread. If this recurs, the "
            "buffer-clear worker may be starved.",
            _BUFFER_CLEAR_QUEUE_MAXSIZE,
        )
        try:
            for chunk in buffer:
                if isinstance(chunk, np.ndarray):
                    chunk.fill(0)
        except Exception:
            pass  # best-effort; the buffer will be GC'd anyway
