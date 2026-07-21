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
# NOTE (2026-07-20): ``_thread_registry`` and the registration block in
# ``_ensure_buffer_clear_worker`` were removed — they were added as part of
# a merge-damage repair, but HEAD's ``recorder.py`` never calls
# ``recording.set_thread_registry()``, so the registry was always ``None``
# and the registration was dead code. Keeping it risked a future agent
# re-adding the orphan call site in ``recorder.py`` (which was the original
# crash). If ThreadRegistry propagation is genuinely needed, implement it
# end-to-end properly — see the note in ``recording/__init__.py``.


def _ensure_buffer_clear_worker() -> threading.Thread:
    """Lazily start the single long-lived buffer-clear worker thread.

    CR-10: idempotent — repeated calls return the same running thread.
    The worker is a daemon so it never blocks process exit. Acquired
    under ``_buffer_clear_worker_lock`` to make the lazy-start race-free
    under concurrent ``stop()``/``discard()`` calls.
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
                name="buffer-clear-bg",
                daemon=True,
            )
            _buffer_clear_worker = worker
            worker.start()
    return worker


def _buffer_clear_worker_loop() -> None:
    """CR-10: drain ``_buffer_clear_queue`` and zero each deque's chunks.

    Loops until the worker thread is killed (it's a daemon, so process
    exit handles that). Each item popped is a ``collections.deque`` of
    audio chunks; we iterate it and ``ndarray.fill(0)`` each chunk. Best
    effort — any exception is swallowed (the deque will be GC'd anyway,
    and we don't want one bad buffer to poison the worker for the rest).
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
