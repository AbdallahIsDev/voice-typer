"""Regression tests for the hot-swap secure-clear path.

Pre-fix bug:
  - ``DisconnectHandler.restart_stream`` called ``_secure_clear_caches()``
    (which correctly zeros cached concat arrays) and THEN called
    ``recorder._audio_pipeline._buffer.clear()`` and ``recorder._ring_buffer.clear()``.
    The ``.clear()`` calls drop all chunk references WITHOUT zeroing
    the underlying numpy arrays — leaving the user's voice data in
    process memory until GC reclaims the arrays. This was inconsistent
    with the ``discard()`` path in ``_recorder_split.py:467-475`` which
    correctly calls ``_secure_clear_array_background(_old_buffer)``
    after swapping in a fresh deque.
  - ``capture.start_audio_worker_body`` and ``stop_audio_worker_body``
    also called ``_ring_buffer.clear()`` directly, dropping chunk
    references without zeroing the underlying arrays.

Post-fix:
  - ``disconnect_handler.py`` replaces ``recorder._audio_pipeline._buffer.clear()`` with
    the swap-and-secure-clear-background pattern (mirrors ``discard()``).
  - ``disconnect_handler.py`` and ``capture.py`` iterate the ring
    buffer and call ``.fill(0)`` on each chunk's numpy array BEFORE
    ``.clear()`` (synchronous zeroing is acceptable — ring buffer
    chunks are small ~2KB).

These tests:
  1. Verify the ring buffer chunks are zeroed BEFORE the deque is
     cleared on the disconnect restart path (the new behavior).
  2. Verify ``_secure_clear_array_background`` is called with the
     OLD ``_buffer`` reference on the disconnect restart path (so
     the user's voice data is zeroed on the background worker).
  3. Verify the same ring-buffer zeroing behavior in the two
     ``capture.py`` call sites (``start_audio_worker_body`` and
     ``stop_audio_worker_body`` with ``drain=False``).
  4. Pin the source-string contract so a future refactor cannot
     silently revert to the bare ``.clear()`` form.
"""

from __future__ import annotations

import collections
import inspect
import threading
from unittest.mock import MagicMock, patch

import numpy as np
from voice_typer.server.recording.capture import AudioCallbackDispatcher
from voice_typer.server.recording.disconnect_handler import (
    DisconnectHandler,
)
from voice_typer.server.recording.recorder import Recorder

# ── Helpers ────────────────────────────────────────────────────────────


def _make_recorder() -> Recorder:
    """Build a real ``Recorder`` with a MagicMock config (no audio device).

    Delegates to the shared canonical factory (XS-42 helper dedup) —
    see ``tests/fixtures/recorder_test_helpers.make_recorder`` for the
    pre-populated config fields.
    """
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder()


def _setup_recorder_for_restart(monkeypatch, r: Recorder) -> None:
    """Stub external dependencies so ``restart_stream`` runs without a
    real audio device or a started stream (mirrors
    ``test_recorder_mono_and_disconnect_fixes``'s helper)."""
    monkeypatch.setattr(r, "_current_callback", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(r._devices, "_resolve_device", lambda: None)
    monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", lambda _d: (48000, None))

    import voice_typer.server.recording.disconnect_handler as dh_mod

    monkeypatch.setattr(dh_mod, "refresh_vad_caches", lambda rec: None)

    monkeypatch.setattr(
        dh_mod.sd,
        "query_devices",
        lambda *a, **k: {"max_input_channels": 1, "name": "fake"},
    )
    fake_stream = MagicMock()
    monkeypatch.setattr(dh_mod.sd, "InputStream", lambda **kw: fake_stream)


def _make_ring_buffer_chunk(value: float = 0.5) -> tuple:
    """Build a ring-buffer 5-tuple payload with a NON-zero numpy array.

    The ring buffer holds 5-tuples ``(chunk_copy, frames, time_info,
    status, perf_ts)`` — the numpy array is the first element. We use a
    non-zero fill so we can distinguish "array was zeroed" from "array
    was always zero" (regression guard against a no-op test).
    """
    arr = np.full(512, value, dtype=np.float32)
    return (arr, 512, None, None, 0.0)


# ── DisconnectHandler.restart_stream: ring buffer secure clear ─────────


class TestRestartStreamRingBufferSecureClear:
    """The disconnect-restart path must ZERO each ring-buffer chunk's
    numpy array BEFORE clearing the deque (mirrors the preroll-buffer
    pattern in stop()/discard())."""

    def test_ring_buffer_chunks_are_zeroed_before_clear(self, monkeypatch):
        """Each ring-buffer chunk's numpy array must be ``.fill(0)``-ed
        BEFORE the deque is cleared — otherwise the user's voice data
        lingers in process memory until GC reclaims the arrays."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        # Populate the ring buffer with NON-zero numpy chunks (5-tuples).
        chunk_arrays = []
        for i in range(3):
            payload = _make_ring_buffer_chunk(value=float(i + 1))
            chunk_arrays.append(payload[0])
            r._ring_buffer.append(payload)
        # Sanity: chunks start non-zero.
        assert all(arr.any() for arr in chunk_arrays), "test setup: chunk arrays must be non-zero before restart"
        assert len(r._ring_buffer) == 3

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        # Deque must be empty after restart.
        assert len(r._ring_buffer) == 0, "ring buffer must be cleared on hot-swap restart"
        # Every previously-held array must now be all-zeros — proving
        # the restart path zeroed each chunk BEFORE dropping the deque
        # reference. The external references (``chunk_arrays``) simulate
        # another holder of the same array (e.g. a downstream consumer
        # that took a snapshot) so we can verify the in-place zeroing
        # independent of the deque's lifetime.
        for i, arr in enumerate(chunk_arrays):
            assert not arr.any(), (
                f"ring-buffer chunk {i} was NOT zeroed before .clear() — "
                f"max abs value after restart: {np.abs(arr).max()}. "
                f"The disconnect-restart path must call .fill(0) on each "
                f"chunk's numpy array BEFORE .clear() so the user's voice "
                f"data doesn't linger in process memory."
            )

    def test_ring_buffer_clear_handles_direct_array_items(self, monkeypatch):
        """The zeroing loop is defensive against direct-numpy-array items
        (not wrapped in a 5-tuple) — legacy/fallback robustness."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        # Mix of 5-tuples and direct numpy arrays.
        direct_arr = np.full(256, 0.7, dtype=np.float32)
        tuple_arr = np.full(256, 0.9, dtype=np.float32)
        r._ring_buffer.append(direct_arr)
        r._ring_buffer.append((tuple_arr, 256, None, None, 0.0))

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        assert len(r._ring_buffer) == 0
        assert not direct_arr.any(), "direct numpy array item must be zeroed"
        assert not tuple_arr.any(), "tuple-wrapped numpy array item must be zeroed"

    def test_ring_buffer_clear_is_noop_on_empty_deque(self, monkeypatch):
        """An empty ring buffer must not raise on the zeroing loop."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        assert len(r._ring_buffer) == 0

        handler = DisconnectHandler(r)
        # Must not raise.
        handler.restart_stream(_captured_generation=0)
        assert len(r._ring_buffer) == 0


# ── DisconnectHandler.restart_stream: _buffer secure clear ─────────────


class TestRestartStreamBufferSecureClearBackground:
    """The disconnect-restart path must swap ``_buffer`` for a fresh
    deque and defer zeroing to the background buffer-clear worker
    (mirrors ``discard()``'s pattern in ``_recorder_split.py:467-475``)."""

    def test_buffer_is_swapped_not_cleared(self, monkeypatch):
        """``_buffer`` must be replaced with a fresh deque (not
        ``.clear()``-ed in place) so the old deque can be handed off to
        ``_secure_clear_array_background`` for zeroing on the background
        worker."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        # Capture the original deque identity.
        original_buffer = r._audio_pipeline._buffer
        # Populate with a non-zero chunk.
        original_buffer.append(np.full(100, 0.5, dtype=np.float32))

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        # The new deque must NOT be the same object as the original —
        # proving a swap (not an in-place .clear()).
        assert r._audio_pipeline._buffer is not original_buffer, (
            "_buffer must be swapped for a fresh deque on hot-swap "
            "restart (mirrors discard()). An in-place .clear() would "
            "drop chunk references WITHOUT zeroing the underlying "
            "numpy arrays, leaving the user's voice data in process "
            "memory until GC."
        )
        # New deque must be empty.
        assert len(r._audio_pipeline._buffer) == 0
        # New deque must preserve maxlen.
        assert r._audio_pipeline._buffer.maxlen == original_buffer.maxlen

    def test_old_buffer_passed_to_secure_clear_array_background(self, monkeypatch):
        """``_secure_clear_array_background`` must be called with the OLD
        ``_buffer`` reference so the background worker zeros the chunks."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        original_buffer = r._audio_pipeline._buffer
        original_buffer.append(np.full(100, 0.5, dtype=np.float32))

        # Spy on the package-level helper (production calls it via
        # ``_recording_pkg._secure_clear_array_background`` after a lazy
        # import — patching the package binding is the correct injection
        # point per the module docstring).
        import voice_typer.server.recording as recording_pkg

        with patch.object(recording_pkg, "_secure_clear_array_background") as spy:
            handler = DisconnectHandler(r)
            handler.restart_stream(_captured_generation=0)

        assert spy.called, (
            "_secure_clear_array_background must be called on the "
            "hot-swap restart path so the old _buffer chunks are "
            "zeroed on the background worker (mirrors discard())."
        )
        args, _ = spy.call_args
        assert args[0] is original_buffer, (
            "_secure_clear_array_background must be called with the OLD "
            "_buffer reference (not the fresh deque). Got a different "
            "object — the swap-and-secure-clear pattern is broken."
        )


# ── capture.py: ring buffer secure clear in worker-lifecycle bodies ────


def _build_dispatcher_recorder(
    *, worker_alive: bool = False, ring_chunks: list | None = None
) -> tuple[MagicMock, list[np.ndarray]]:
    """Build a MagicMock recorder with stubs sufficient for
    ``start_audio_worker_body`` and ``stop_audio_worker_body``.

    Returns the recorder and the list of numpy arrays held in the ring
    buffer (so the test can verify they were zeroed in-place).
    """
    recorder = MagicMock(name="recorder")
    # ``_worker_thread`` is checked for is_alive() — None / MagicMock
    # depending on the caller's intent.
    if worker_alive:
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True
        fake_thread.join = lambda timeout=None: None
        recorder._worker_thread = fake_thread
    else:
        recorder._worker_thread = None
    # Real threading.Event for stop / wake so .set() / .clear() work.
    recorder._worker_stop_event = threading.Event()
    recorder._worker_wake_event = threading.Event()
    # Real deque so the zeroing loop can iterate.
    chunks = []
    arrays = []
    for _ in range(3):
        arr = np.full(512, 0.5, dtype=np.float32)
        arrays.append(arr)
        chunks.append((arr, 512, None, None, 0.0))
    if ring_chunks is not None:
        chunks = ring_chunks
        arrays = [c[0] if isinstance(c, tuple) else c for c in chunks]
    recorder._ring_buffer = collections.deque(chunks)
    # ``_thread_registry`` is optional — None short-circuits the register
    # / unregister calls.
    recorder._thread_registry = None
    return recorder, arrays


class TestStartAudioWorkerBodyRingBufferSecureClear:
    """``start_audio_worker_body`` must zero ring-buffer chunks before
    clearing the deque (mirrors the preroll-buffer pattern)."""

    def test_ring_buffer_chunks_zeroed_before_clear(self):
        """When a fresh worker starts, any stale ring-buffer chunks from
        a previous session must be ZEROED before ``.clear()`` drops the
        references."""
        dispatcher = AudioCallbackDispatcher(MagicMock())
        # Simulate: worker not running → start path runs the clear.
        recorder, arrays = _build_dispatcher_recorder(worker_alive=False)

        # Use the dispatcher's method directly (the Recorder delegator
        # is a thin 1-liner that calls this body).
        dispatcher.start_audio_worker_body(recorder)

        assert len(recorder._ring_buffer) == 0, "ring buffer must be cleared when starting a fresh worker"
        # Each held array must now be zero.
        for i, arr in enumerate(arrays):
            assert not arr.any(), (
                f"ring-buffer chunk {i} was NOT zeroed before .clear() — "
                f"max abs value: {np.abs(arr).max()}. The start-audio-"
                f"worker path must call .fill(0) on each chunk's numpy "
                f"array BEFORE .clear()."
            )

    def test_start_is_noop_when_worker_already_alive(self):
        """When the worker is already alive, the body returns early and
        does NOT touch the ring buffer (no zeroing, no clear)."""
        dispatcher = AudioCallbackDispatcher(MagicMock())
        recorder, arrays = _build_dispatcher_recorder(worker_alive=True)
        # Sanity: ring buffer is populated.
        assert len(recorder._ring_buffer) == 3

        dispatcher.start_audio_worker_body(recorder)

        # Early return — ring buffer untouched.
        assert len(recorder._ring_buffer) == 3, (
            "start_audio_worker_body must be a no-op when the worker is already alive (early-return guard)."
        )
        # Chunks must NOT have been zeroed (no clear was performed).
        assert all(arr.any() for arr in arrays), (
            "start_audio_worker_body must NOT zero ring-buffer chunks on the early-return path (no clear happened)."
        )


class TestStopAudioWorkerBodyRingBufferSecureClear:
    """``stop_audio_worker_body`` (drain=False, the discard path) must
    zero ring-buffer chunks before clearing the deque."""

    def test_discard_path_zeroes_chunks_before_clear(self):
        """When called with ``drain=False`` (the discard path), the ring
        buffer must be ZEROED before ``.clear()`` so the cancelled
        session's audio data doesn't linger in process memory."""
        dispatcher = AudioCallbackDispatcher(MagicMock())
        recorder, arrays = _build_dispatcher_recorder(worker_alive=True)

        dispatcher.stop_audio_worker_body(recorder, timeout=0.1, drain=False)

        assert len(recorder._ring_buffer) == 0, "ring buffer must be cleared on the discard path (drain=False)"
        for i, arr in enumerate(arrays):
            assert not arr.any(), (
                f"ring-buffer chunk {i} was NOT zeroed before .clear() "
                f"on the discard path — max abs value: {np.abs(arr).max()}. "
                f"stop_audio_worker_body(drain=False) must call .fill(0) "
                f"on each chunk's numpy array BEFORE .clear()."
            )

    def test_drain_true_does_not_clear_ring_buffer(self):
        """When ``drain=True`` (the stop path), the ring buffer is NOT
        cleared — the worker drains it fully before exiting so no
        in-flight audio is lost. The zeroing loop must NOT run in this
        case (it would zero chunks the worker is still draining)."""
        dispatcher = AudioCallbackDispatcher(MagicMock())
        recorder, arrays = _build_dispatcher_recorder(worker_alive=True)
        original_len = len(recorder._ring_buffer)

        dispatcher.stop_audio_worker_body(recorder, timeout=0.1, drain=True)

        # drain=True → ring buffer NOT cleared (worker drains it).
        assert len(recorder._ring_buffer) == original_len, (
            "stop_audio_worker_body(drain=True) must NOT clear the ring "
            "buffer — the worker drains it fully so no in-flight audio "
            "is lost."
        )
        # Chunks must NOT have been zeroed (worker may still process them).
        assert all(arr.any() for arr in arrays), (
            "stop_audio_worker_body(drain=True) must NOT zero ring-buffer "
            "chunks — the worker may still drain and process them."
        )

    def test_stop_is_noop_when_worker_is_none(self):
        """When there is no worker (``_worker_thread is None``), the body
        returns early without touching the ring buffer."""
        dispatcher = AudioCallbackDispatcher(MagicMock())
        recorder, arrays = _build_dispatcher_recorder(worker_alive=False)

        dispatcher.stop_audio_worker_body(recorder, timeout=0.1, drain=False)

        # Early return — ring buffer untouched.
        assert len(recorder._ring_buffer) == 3
        assert all(arr.any() for arr in arrays)


# ── Source-string contracts (regression guards) ───────────────────────


class TestSourceStringContracts:
    """Pin the source-level pattern so a future refactor cannot silently
    revert to the bare ``.clear()`` form (which drops references without
    zeroing the underlying numpy arrays)."""

    def test_disconnect_handler_does_not_bare_clear_ring_buffer(self):
        """``disconnect_handler.py`` must NOT call
        ``recorder._ring_buffer.clear()`` without first iterating and
        zeroing the chunks. We check for the bare ``.clear()`` call
        preceded by no zeroing loop (a weak but useful guard)."""
        from voice_typer.server.recording import disconnect_handler as dh_mod

        src = inspect.getsource(dh_mod.DisconnectHandler.restart_stream)
        # The fix must contain the zeroing loop.
        assert "_arr.fill(0)" in src, (
            "disconnect_handler.restart_stream must zero each ring-buffer "
            "chunk's numpy array via _arr.fill(0) before .clear()."
        )
        # The fix must call _secure_clear_array_background for _buffer.
        assert "_secure_clear_array_background(_old_buffer)" in src, (
            "disconnect_handler.restart_stream must call "
            "_secure_clear_array_background(_old_buffer) on the old "
            "_buffer reference (swap-and-secure-clear pattern)."
        )

    def test_capture_does_not_bare_clear_ring_buffer(self):
        """``capture.py`` must NOT call ``recorder._ring_buffer.clear()``
        without first iterating and zeroing the chunks."""
        from voice_typer.server.recording import capture as capture_mod

        src_start = inspect.getsource(capture_mod.AudioCallbackDispatcher.start_audio_worker_body)
        src_stop = inspect.getsource(capture_mod.AudioCallbackDispatcher.stop_audio_worker_body)
        # Both methods must contain the zeroing loop.
        assert "_arr.fill(0)" in src_start, (
            "start_audio_worker_body must zero each ring-buffer chunk's numpy array via _arr.fill(0) before .clear()."
        )
        assert "_arr.fill(0)" in src_stop, (
            "stop_audio_worker_body must zero each ring-buffer chunk's numpy array via _arr.fill(0) before .clear()."
        )
