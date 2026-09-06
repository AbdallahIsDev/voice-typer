"""regression tests: single long-lived buffer-clear worker thread.

Before the fix, ``_secure_clear_array_background`` spawned a fresh
daemon ``threading.Thread`` on every call. Under rapid hotkey toggling
(several ``stop()``/``discard()`` invocations per second) this produced
unbounded thread churn. The fix replaces the per-call spawn with a
single module-level daemon worker + bounded ``queue.Queue``.

These tests assert the four guarantees spelled out in the fix
task:

1. Only ONE worker thread is spawned regardless of how many buffers
   are enqueued.
2. All enqueued buffers are eventually zeroed.
3. The worker is a daemon thread.
4. Rapid enqueues (1000 in a loop) don't spawn 1000 threads.

The tests are written to be order-independent: because the singleton
worker thread cannot be cleanly killed from Python (it's a daemon, and
CPython offers no thread.kill()), we assert *growth* (≤1 new worker
thread per test) rather than absolute thread counts. This keeps the
suite resilient to shared module state across tests.
"""

from __future__ import annotations

import collections
import queue
import sys
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


# ── shared fixture: mock sounddevice so importing recording.py is cheap ──
@pytest.fixture(autouse=True)
def _mock_sounddevice(monkeypatch):
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


def _drain_queue(timeout: float = 5.0) -> None:
    """Block until the buffer-clear queue is fully drained.

    The worker calls ``task_done()`` after each deque, so ``Queue.join``
    returns once every enqueued buffer has been zeroed. We poll with a
    short timeout rather than relying on ``join()``'s indefinite block
    so a buggy worker can't hang the test suite forever.
    """
    from voice_typer.server.recording import _buffer_clear_queue

    if not _buffer_clear_queue.unfinished_tasks:
        # Already drained — nothing to wait for.
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


# ────────────────────────────────────────────────────────────────────────────
# only ONE worker thread is spawned across many enqueues.
# ────────────────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────────────────
# all enqueued buffers are eventually zeroed.
# ────────────────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────────────────
# the worker is a daemon thread.
# ────────────────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────────────────
# rapid enqueues (1000 in a loop) don't spawn 1000 threads.
# ──────────────────────────────────────────────────────────────────────────
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
    # any implementation that spawns one thread per enqueue.
    assert total_growth <= 5, (
        f"total thread count grew by {total_growth} "
        f"(initial={initial_total_threads}, final={final_total_threads}); "
        "expected bounded growth (≤5)."
    )


# ────────────────────────────────────────────────────────────────────────────
# the cached worker singleton is reused across calls (not recreated).
# ──────────────────────────────────────────────────────────────────────────
def test_worker_singleton_reused_across_calls():
    """``_buffer_clear_worker`` is created once and then reused.

    This is the lazy-start invariant: the cached module-level worker
    reference must NOT be replaced by a fresh thread on subsequent
    calls (otherwise the fix would have reintroduced the per-call
    spawn pattern in disguise).
    """
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


# ────────────────────────────────────────────────────────────────────────────
# queue-full fallback still zeros the buffer synchronously.
# ────────────────────────────────────────────────────────────────────────────
def test_queue_full_fallback_clears_synchronously(monkeypatch):
    """if the bounded queue is full, the caller thread zeros the buffer.

    The fallback path must preserve the secure-clear guarantee (data is
    still zeroed) rather than dropping the clear silently. We force the
    fallback by making ``put_nowait`` always raise ``queue.Full``.
    """
    from voice_typer.server import recording
    from voice_typer.server.recording import buffer as buffer_mod

    # Replace the queue's put_nowait with one that always raises Full,
    # so the synchronous fallback path is exercised deterministically.
    real_queue = buffer_mod._buffer_clear_queue

    def always_full(_item):
        raise queue.Full

    monkeypatch.setattr(real_queue, "put_nowait", always_full)
    # Bypass lazy-start so we don't depend on worker state. The consumer
    # reads its OWN module global (C-ARCH-2 owning-module shape), so the
    # patch must target recording.buffer — patching the package re-export
    # would be a silent no-op.
    monkeypatch.setattr(buffer_mod, "_ensure_buffer_clear_worker", lambda: None)

    overflow = _make_buffer(n_chunks=2)
    assert np.any(overflow[0]), "test setup: overflow buffer should start non-zero"

    # Should NOT raise; should fall back to synchronous fill(0).
    recording._secure_clear_array_background(overflow)

    # The fallback cleared the overflow buffer in-place.
    for i, chunk in enumerate(overflow):
        assert not np.any(chunk), f"overflow chunk {i} not zeroed by queue-full fallback"


# ────────────────────────────────────────────────────────────────────────────
# public API shape preserved — function accepts a deque and returns None.
# ────────────────────────────────────────────────────────────────────────────
def test_public_api_signature_preserved():
    """the public callable ``_secure_clear_array_background(buf)``
    must still accept a deque and return None (callers in ``stop()`` /
    ``discard()`` rely on this)."""
    from voice_typer.server import recording

    buf = _make_buffer()
    result = recording._secure_clear_array_background(buf)
    assert result is None, "public API must return None"
    _drain_queue(timeout=5.0)


# ────────────────────────────────────────────────────────────────────────────
# non-ndarray items in the deque are skipped without raising.
# ──────────────────────────────────────────────────────────────────────────
def test_non_ndarray_items_skipped_gracefully():
    """the worker iterates a heterogeneous deque without crashing.

    The original ``_zero_worker`` and the new worker loop both guard
    each chunk with ``isinstance(chunk, np.ndarray)``. A deque that
    contains stray non-array items (e.g. a metadata dict accidentally
    appended) must not poison the worker for subsequent buffers.
    """
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


# ── ER-91: pop-drain frees chunks as they are zeroed ───────────────


def test_er91_deque_is_drained_chunk_by_chunk():
    """The worker must POP chunks off the deque (not iterate in place):
    after processing, the deque itself is empty and every handed-over
    chunk is zeroed. In-place iteration would leave the full deque
    alive for the whole pass (the ER-91 complaint)."""
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
    """Non-deque buffers fall back to in-place iteration semantics:
    all ndarray chunks end up zeroed."""
    from voice_typer.server.recording import _secure_clear_array_background

    chunks = [np.full(4, 7.0, dtype=np.float32), "not-an-array", np.ones(3, dtype=np.float32)]
    buf = list(chunks)
    _secure_clear_array_background(buf)
    _drain_queue()
    assert not np.any(chunks[0]), "first ndarray chunk not zeroed"
    assert not np.any(chunks[2]), "last ndarray chunk not zeroed"
    assert chunks[1] == "not-an-array", "non-ndarray item must be untouched"
