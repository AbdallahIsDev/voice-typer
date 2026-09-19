"""regression tests: single long-lived buffer-clear worker thread."""

from __future__ import annotations

import collections
import queue
import sys
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _drain_queue(timeout: float = 5.0) -> None:
    """Block until the buffer-clear queue is fully drained."""
    from voice_typer.server.recording import _buffer_clear_queue

    if not _buffer_clear_queue.unfinished_tasks:
        # Already drained, nothing to wait for.
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _buffer_clear_queue.unfinished_tasks:
            return
        time.sleep(0.005)
    pytest.fail("buffer-clear queue did not drain within timeout")


def _count_buffer_clear_threads() -> int:
    """Number of live OS threads named ``buffer-clear-bg``."""
    return sum(1 for t in threading.enumerate() if t.name == "buffer-clear-bg" and t.is_alive())


def _make_buffer(n_chunks: int = 3) -> collections.deque:
    """A deque of non-zero float32 chunks, mimicking a live audio buffer."""
    return collections.deque(np.full((n_chunks, 16), 0.5, dtype=np.float32) for _ in range(n_chunks))


def test_single_worker_thread_spawned_across_many_enqueues():
    """50 enqueues must not create more than 1 new worker thread."""
    from voice_typer.server import recording

    initial = _count_buffer_clear_threads()
    for _ in range(50):
        recording._secure_clear_array_background(_make_buffer())

    _drain_queue(timeout=5.0)

    final = _count_buffer_clear_threads()
    growth = final - initial
    assert growth <= 1, (
        f"expected at most 1 new buffer-clear-bg thread across 50 enqueues, "
        f"got {growth} (initial={initial}, final={final})."
        "behaviour would have spawned ~50."
    )


def test_all_enqueued_buffers_eventually_zeroed():
    """every enqueued deque's chunks end up zero-filled."""
    from voice_typer.server import recording

    buffers = [_make_buffer(n_chunks=4) for _ in range(20)]
    for buf in buffers:
        recording._secure_clear_array_background(buf)

    _drain_queue(timeout=5.0)

    for i, buf in enumerate(buffers):
        for j, chunk in enumerate(buf):
            assert not np.any(chunk), f"buffer {i} chunk {j} was not zeroed: max={np.abs(chunk).max()}"


def test_worker_is_daemon():
    """the buffer-clear worker must be a daemon (no process-exit block)."""
    from voice_typer.server import recording

    # Trigger lazy start (or reuse existing singleton).
    recording._secure_clear_array_background(_make_buffer())
    _drain_queue(timeout=5.0)

    worker = recording.buffer._buffer_clear_worker
    assert worker is not None, "worker was not lazily started"
    assert worker.daemon is True, "buffer-clear worker must be a daemon"
    assert worker.name == "buffer-clear-bg", f"expected name 'buffer-clear-bg', got {worker.name!r}"


def test_rapid_enqueues_do_not_explode_thread_count():
    """1000 rapid enqueues must not spawn 1000 threads."""
    from voice_typer.server import recording

    initial_worker_count = _count_buffer_clear_threads()
    initial_total_threads = threading.active_count()

    for _ in range(1000):
        recording._secure_clear_array_background(_make_buffer(n_chunks=1))

    _drain_queue(timeout=10.0)

    final_worker_count = _count_buffer_clear_threads()
    final_total_threads = threading.active_count()

    worker_growth = final_worker_count - initial_worker_count
    total_growth = final_total_threads - initial_total_threads

    assert worker_growth <= 1, (
        f"expected at most 1 new buffer-clear-bg thread after 1000 enqueues, "
        f"got {worker_growth} (initial={initial_worker_count}, "
        f"final={final_worker_count}). Old behaviour would have "
        "spawned ~1000."
    )
    # Allow a small constant slack for pytest/fixture threads, but catch
    assert total_growth <= 5, (
        f"total thread count grew by {total_growth} "
        f"(initial={initial_total_threads}, final={final_total_threads}); "
        "expected bounded growth (≤5)."
    )


def test_worker_singleton_reused_across_calls():
    """``_buffer_clear_worker`` is created once and then reused."""
    from voice_typer.server import recording

    # Force lazy start (or pick up the existing singleton from prior tests).
    recording._secure_clear_array_background(_make_buffer())
    worker_after_first = recording.buffer._buffer_clear_worker
    assert worker_after_first is not None, "worker should be lazily started"
    _drain_queue(timeout=5.0)

    # 20 subsequent calls must NOT replace the cached worker reference.
    for _ in range(20):
        recording._secure_clear_array_background(_make_buffer())
        assert recording.buffer._buffer_clear_worker is worker_after_first, (
            "buffer-clear worker should be reused across calls, not recreated"
        )

    _drain_queue(timeout=5.0)


def test_queue_full_fallback_clears_synchronously(monkeypatch):
    """if the bounded queue is full, the caller thread zeros the buffer."""
    from voice_typer.server import recording
    from voice_typer.server.recording import buffer as buffer_mod

    # Replace the queue's put_nowait with one that always raises Full,
    real_queue = buffer_mod._buffer_clear_queue

    def always_full(_item):
        raise queue.Full

    monkeypatch.setattr(real_queue, "put_nowait", always_full)
    # reads its OWN module global (C-ARCH-2 owning-module shape), so the
    monkeypatch.setattr(buffer_mod, "_ensure_buffer_clear_worker", lambda: None)

    overflow = _make_buffer(n_chunks=2)
    assert np.any(overflow[0]), "test setup: overflow buffer should start non-zero"

    # Should NOT raise; should fall back to synchronous fill(0).
    recording._secure_clear_array_background(overflow)

    # The fallback cleared the overflow buffer in-place.
    for i, chunk in enumerate(overflow):
        assert not np.any(chunk), f"overflow chunk {i} not zeroed by queue-full fallback"


def test_public_api_signature_preserved():
    """the public callable ``_secure_clear_array_background(buf)``"""
    from voice_typer.server import recording

    buf = _make_buffer()
    result = recording._secure_clear_array_background(buf)
    assert result is None, "public API must return None"
    _drain_queue(timeout=5.0)


def test_non_ndarray_items_skipped_gracefully():
    """the worker iterates a heterogeneous deque without crashing."""
    from voice_typer.server import recording

    mixed = collections.deque(
        [
            np.full(8, 0.7, dtype=np.float32),
            "not-an-array",
            None,
            np.full(8, 0.9, dtype=np.float32),
        ]
    )
    clean = _make_buffer(n_chunks=2)

    recording._secure_clear_array_background(mixed)
    recording._secure_clear_array_background(clean)

    _drain_queue(timeout=5.0)

    # All ndarray chunks in both deques should be zeroed.
    for i, chunk in enumerate(mixed):
        if isinstance(chunk, np.ndarray):
            assert not np.any(chunk), f"mixed[{i}] not zeroed"
    for i, chunk in enumerate(clean):
        assert not np.any(chunk), f"clean[{i}] not zeroed"


def test_er91_deque_is_drained_chunk_by_chunk():
    """The worker must POP chunks off the deque (not iterate in place):"""
    import collections

    from voice_typer.server.recording import _secure_clear_array_background

    chunks = [np.arange(8, dtype=np.float32) + float(i) for i in range(5)]
    buf = collections.deque(chunks)
    _secure_clear_array_background(buf)
    _drain_queue()
    assert len(buf) == 0, f"deque must be emptied by the pop-drain, has {len(buf)} items"
    for i, c in enumerate(chunks):
        assert not np.any(c), f"chunk {i} was not zeroed"


def test_er91_non_deque_iterable_still_zeroed_defensively():
    """Non-deque buffers fall back to in-place iteration semantics:"""
    from voice_typer.server.recording import _secure_clear_array_background

    chunks = [np.full(4, 7.0, dtype=np.float32), "not-an-array", np.ones(3, dtype=np.float32)]
    buf = list(chunks)
    _secure_clear_array_background(buf)
    _drain_queue()
    assert not np.any(chunks[0]), "first ndarray chunk not zeroed"
    assert not np.any(chunks[2]), "last ndarray chunk not zeroed"
    assert chunks[1] == "not-an-array", "non-ndarray item must be untouched"
