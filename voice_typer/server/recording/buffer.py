"""Securely zero audio buffers on a background worker thread.

SEC-audit-008: audio chunks must be zeroed before deallocation so voice
"""

from __future__ import annotations

import collections
import contextlib
import logging
import queue
import threading
import time
from typing import Any

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

# Package-level logger so records hit caplog under "voice_typer.server.recording".
log = logging.getLogger("voice_typer.server.recording")


def _secure_clear_array(arr: np.ndarray) -> None:
    """SEC-audit-008: zero a numpy array in-place before deallocation."""
    with contextlib.suppress(Exception):
        arr.fill(0)  # best-effort; some array types may not support fill


def _secure_clear_handed_off_buffer(buffer: Any) -> None:
    """SEC-audit-008: zero one handed-off buffer in place."""
    storage = getattr(buffer, "storage", None)
    if isinstance(storage, np.ndarray):
        storage.fill(0)
        return
    if isinstance(buffer, collections.deque):
        while True:
            try:
                chunk = buffer.popleft()
            except IndexError:
                break
            if isinstance(chunk, np.ndarray):
                chunk.fill(0)
        return
    for chunk in list(buffer):
        if isinstance(chunk, np.ndarray):
            chunk.fill(0)


# Single long-lived clear worker replaces per-call thread spawn (hotkey-mash
_BUFFER_CLEAR_QUEUE_MAXSIZE = 64
_buffer_clear_queue: queue.Queue = queue.Queue(maxsize=_BUFFER_CLEAR_QUEUE_MAXSIZE)
_buffer_clear_worker_lock = threading.Lock()
_buffer_clear_worker: threading.Thread | None = None

BUFFER_CLEAR_WORKER_NAME = "buffer-clear-bg"

# Test-only join timeout; generous because a large deque may be mid-zero.
_BUFFER_CLEAR_WORKER_JOIN_TIMEOUT_S = 5.0

# NOTE: see docs/code-notes/recording.md#buffer-clear-worker
_thread_registry: Any | None = None


def set_thread_registry(registry: Any | None) -> None:
    """Install a central ThreadRegistry; register an already-running worker."""
    global _thread_registry
    _thread_registry = registry
    if registry is not None:
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
    """Signal the buffer-clear worker to stop and join it (test helper)."""
    global _buffer_clear_worker
    worker = _buffer_clear_worker
    if worker is None:
        return True
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
    with _buffer_clear_worker_lock:
        if _buffer_clear_worker is worker:
            _buffer_clear_worker = None
    return exited


def _ensure_buffer_clear_worker() -> threading.Thread:
    """Lazily start the single long-lived buffer-clear worker (idempotent)."""
    global _buffer_clear_worker
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
    """Drain the clear queue until a None sentinel (SEC-audit-008)."""
    while True:
        try:
            buffer = _buffer_clear_queue.get()
        except Exception:
            time.sleep(0.01)
            continue
        try:
            if buffer is None:
                return
            _secure_clear_handed_off_buffer(buffer)
        except Exception:
            log.debug("[RECORDING] secure-clear worker failed on handed-off buffer", exc_info=True)
        finally:
            _buffer_clear_queue.task_done()


def _secure_clear_array_background(buffer: Any) -> None:
    """SEC-audit-008: enqueue buffer zeroing on the background worker."""
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
        with contextlib.suppress(Exception):
            _secure_clear_handed_off_buffer(buffer)
