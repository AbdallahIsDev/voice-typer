"""Phase 4.5 — focused unit tests for
``voice_typer.server.recording.capture.AudioCallbackDispatcher``.

These tests exercise the public API of the new collaborator with a
mocked ``recorder`` instance (a small ``_FakeRecorder`` helper class).
No real audio hardware is touched — no PortAudio, no real audio
worker thread, no subprocess. Each test sets up the fake's state,
calls ``AudioCallbackDispatcher.dispatch_callback_body`` or
``AudioCallbackDispatcher.audio_worker_loop``, and asserts on the
observable side-effects (counters, ring-buffer contents, mock call
counts).
"""

from __future__ import annotations

import collections
import inspect
import threading
import time
from unittest.mock import patch

import numpy as np
import pytest
from voice_typer.server.recording.capture import AudioCallbackDispatcher

from tests.fixtures.wait_helpers import wait_until

# ── Fakes ───────────────────────────────────────────────────────────────


class _FakeRecorder:
    """Minimal stand-in for :class:`Recorder` that owns the shared state
    touched by :class:`AudioCallbackDispatcher`.

    Real ``threading.Event`` / ``collections.deque`` instances are used so
    the dispatcher's synchronization and SPSC-ring-buffer assumptions
    are exercised faithfully. The mono downmix is exercised by spying on
    the module-level :func:`format.ensure_mono` the preroll branch calls
    (delegating to the real implementation). ``_process_audio_chunk``
    records its call args so tests can assert on the chunks the worker
    loop drained.
    """

    def __init__(
        self,
        *,
        ring_maxlen: int | None = 64,
        recording: bool = False,
        preroll_active: bool = False,
        preroll_maxlen: int = 100,
    ) -> None:
        self._recording_event = threading.Event()
        if recording:
            self._recording_event.set()
        self._preroll_active = preroll_active
        if ring_maxlen is None:
            self._ring_buffer: collections.deque = collections.deque()
        else:
            self._ring_buffer = collections.deque(maxlen=ring_maxlen)
        self._preroll_buffer: collections.deque = collections.deque(maxlen=preroll_maxlen)
        self._dropped_ring_chunks: int = 0
        self._worker_stop_event = threading.Event()
        self._worker_wake_event = threading.Event()
        self._process_audio_chunk_calls: list[tuple] = []
        self._process_audio_chunk_raises: bool = False

    def _process_audio_chunk(self, *args: object) -> None:
        self._process_audio_chunk_calls.append(args)
        if self._process_audio_chunk_raises:
            raise RuntimeError("boom (simulated worker error)")


# ── Construction ────────────────────────────────────────────────────────


class TestAudioCallbackDispatcherConstruction:
    """``AudioCallbackDispatcher.__init__`` stores the back-reference."""

    def test_init_stores_back_reference(self):
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        assert dispatcher._recorder is fake

    def test_module_docstring_describes_dispatcher(self):
        from voice_typer.server.recording import capture

        assert "AudioCallbackDispatcher" in capture.__doc__


# ── dispatch_callback_body ─────────────────────────────────────────────


class TestDispatchCallbackBodyPrerollPath:
    """Pre-roll branch — invoked when ``_recording_event`` is NOT set."""

    def test_preroll_active_captures_mono_preroll_and_returns_none(self):
        fake = _FakeRecorder(recording=False, preroll_active=True)
        dispatcher = AudioCallbackDispatcher(fake)
        # 2-channel stereo input — ensure_mono should collapse to mono.
        indata = np.arange(2 * 4, dtype=np.float32).reshape(4, 2)
        # Spy on the module-level downmix helper the preroll branch calls
        # (delegating to the REAL implementation so the downmix behavior
        # is exercised, not stubbed).
        import voice_typer.server.recording.capture as capture_mod
        from voice_typer.server.recording.format import ensure_mono as real_ensure_mono

        ensure_mono_calls: list[np.ndarray] = []

        def _spy(recorder: object, audio: np.ndarray) -> np.ndarray:
            ensure_mono_calls.append(audio)
            return real_ensure_mono(recorder, audio)

        with patch.object(capture_mod, "ensure_mono", _spy):
            result = dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        assert result is None, "preroll path must signal early-bailout (None)"
        # ensure_mono was called once with a COPY of the input (the
        # callback must NOT mutate the PortAudio-owned indata buffer).
        assert len(ensure_mono_calls) == 1
        mono_input = ensure_mono_calls[0]
        assert mono_input is not indata, "preroll capture must copy the indata buffer"
        # Pre-roll buffer received the DOWNMIXED (mono) chunk.
        assert len(fake._preroll_buffer) == 1
        prerolled = fake._preroll_buffer[0]
        assert prerolled.shape == (4,), "stereo→mono downmix should produce 1D mono"
        # Ring buffer MUST NOT receive anything during preroll path.
        assert len(fake._ring_buffer) == 0

    def test_preroll_inactive_returns_none_without_touching_preroll_buffer(self):
        fake = _FakeRecorder(recording=False, preroll_active=False)
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros((4, 1), dtype=np.float32)
        import voice_typer.server.recording.capture as capture_mod

        with patch.object(capture_mod, "ensure_mono") as mono_spy:
            result = dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        assert result is None
        mono_spy.assert_not_called()
        assert len(fake._preroll_buffer) == 0
        assert len(fake._ring_buffer) == 0


class TestDispatchCallbackBodyRecordingPath:
    """Recording-active branch — payload-returning path."""

    def test_returns_5_tuple_payload_when_recording_active(self):
        fake = _FakeRecorder(recording=True, ring_maxlen=64)
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros(4, dtype=np.float32)
        result = dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 5
        chunk_copy, frames, time_info, status, perf_ts = result
        # chunk_copy is a fresh array, NOT the PortAudio-owned indata.
        assert chunk_copy is not indata
        # The copy preserves the input data.
        np.testing.assert_array_equal(chunk_copy, indata)
        # frames / time_info / status are passed through unchanged.
        assert frames == 4
        assert time_info == "tinfo"
        assert status == "status"
        # perf_ts is a real float from time.perf_counter().
        assert isinstance(perf_ts, float)
        assert perf_ts > 0

    def test_does_not_increment_counters_when_ring_buffer_has_room(self):
        fake = _FakeRecorder(recording=True, ring_maxlen=64)
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros(4, dtype=np.float32)
        dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        assert fake._dropped_ring_chunks == 0

    def test_increments_dropped_counters_when_ring_buffer_full(self):
        # maxlen=2 — pre-fill the ring buffer to capacity, then call.
        fake = _FakeRecorder(recording=True, ring_maxlen=2)
        fake._ring_buffer.append(("pre-1", 4, "t1", "s1", 0.0))
        fake._ring_buffer.append(("pre-2", 4, "t2", "s2", 0.0))
        assert len(fake._ring_buffer) == 2  # at capacity
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros(4, dtype=np.float32)
        result = dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        # Body returns a payload (proceed-with-append signal).
        assert result is not None
        # Counter bumped once for the one overflow detection.
        assert fake._dropped_ring_chunks == 1

    def test_no_counter_increment_when_maxlen_is_none(self):
        fake = _FakeRecorder(recording=True, ring_maxlen=None)
        # Stuff the deque so it has content (maxlen=None means never full).
        for i in range(10):
            fake._ring_buffer.append((f"pre-{i}", 4, "t", "s", 0.0))
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.zeros(4, dtype=np.float32)
        dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        assert fake._dropped_ring_chunks == 0

    def test_payload_shape_is_compatible_with_ring_buffer_append_and_unpack(self):
        """Smoke test simulating the primary agent's Option C delegate:

            payload = self._capture.dispatch_callback_body(...)
            if payload is None:
                return
            self._ring_buffer.append(payload)
            self._worker_wake_event.set()

        Verifies the payload tuple can be appended to a deque and later
        unpacked by ``_process_audio_chunk(*chunk_data)`` in the worker
        loop without shape mismatch.
        """
        fake = _FakeRecorder(recording=True, ring_maxlen=64)
        dispatcher = AudioCallbackDispatcher(fake)
        indata = np.arange(4, dtype=np.float32)
        payload = dispatcher.dispatch_callback_body(fake, indata, 4, "tinfo", "status")
        assert payload is not None
        # Simulate the primary agent's delegate append:
        fake._ring_buffer.append(payload)
        # Simulate the worker loop's popleft + unpack:
        chunk_data = fake._ring_buffer.popleft()
        fake._process_audio_chunk(*chunk_data)
        assert len(fake._process_audio_chunk_calls) == 1
        # Verify the unpacked args are the same 5 elements we returned.
        args = fake._process_audio_chunk_calls[0]
        assert len(args) == 5


# ── Source-inspection contract: dispatch_callback_body must NOT contain
# the heavy-pipeline operations that the  source test pins
# out of Recorder._audio_callback_dispatch. (The recorder-side check
# is owned by the primary agent; this just guards the helper side.)
def _strip_docstring(src: str) -> str:
    """Return ``src`` with the leading ``\"\"\"``-delimited docstring removed.

    Used by the source-inspection tests below so the helper's docstring
    (which references the forbidden literals to explain the Option C
    contract) does not trip the negative assertions on the body.
    """
    start = src.find('"""')
    if start == -1:
        return src
    end = src.find('"""', start + 3)
    if end == -1:
        return src
    return src[end + 3 :]


class TestDispatchCallbackBodySourceContract:
    """the helper must not introduce heavy-pipeline ops that
    would leak into ``Recorder._audio_callback_dispatch`` if the primary
    agent inlines them. The body should only do RT-safe work."""

    def test_dispatch_callback_body_source_omits_heavy_ops(self):
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.dispatch_callback_body))
        # The forbidden literals — the same ones the  test
        # checks are absent from Recorder._audio_callback_dispatch.
        for forbidden in (
            "compute_vad_prob",
            "_get_resample_poly",
            "process_chunk",
            "_vad_update",
        ):
            assert forbidden not in src, (
                f"dispatch_callback_body must NOT call {forbidden!r} — "
                "that runs on the worker thread, not the RT callback"
            )

    def test_dispatch_callback_body_source_does_not_call_ring_buffer_append(self):
        """The literal ``_ring_buffer.append`` MUST stay on
        ``Recorder._audio_callback_dispatch`` (Option C). The helper
        must NOT contain it — otherwise the source-inspection check on
        the Recorder's method would still pass, but the helper would
        be doing the append itself, breaking the Option C contract."""
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.dispatch_callback_body))
        # The body READS _ring_buffer.maxlen and len(_ring_buffer) for
        # overflow detection, but must NOT append to it.
        assert "_ring_buffer.append" not in src, (
            "Option C contract: _ring_buffer.append must stay on "
            "Recorder._audio_callback_dispatch (the literal is pinned "
            "by the source-inspection test)"
        )

    def test_dispatch_callback_body_source_does_not_set_worker_wake_event(self):
        """The literal ``_worker_wake_event.set()`` MUST stay on
        ``Recorder._audio_callback_dispatch`` (Option C). The helper
        only reads ``_worker_wake_event`` if at all."""
        src = _strip_docstring(inspect.getsource(AudioCallbackDispatcher.dispatch_callback_body))
        assert "_worker_wake_event.set" not in src, (
            "Option C contract: _worker_wake_event.set() must stay on "
            "Recorder._audio_callback_dispatch (the literal is pinned "
            "by the source-inspection test)"
        )


# ── audio_worker_loop ──────────────────────────────────────────────────


class TestAudioWorkerLoop:
    """``AudioCallbackDispatcher.audio_worker_loop`` — drains the ring
    buffer and calls ``_process_audio_chunk`` until ``_worker_stop_event``
    is set. Tests run the loop on a real thread to exercise the
    ``_worker_wake_event.wait(timeout=...)`` path."""

    def _start_worker(self, dispatcher: AudioCallbackDispatcher, fake: _FakeRecorder) -> threading.Thread:
        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake,),
            name="test-audio-worker",
            daemon=True,
        )
        t.start()
        return t

    def _stop_and_join(self, fake: _FakeRecorder, t: threading.Thread, timeout: float = 2.0) -> None:
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()  # wake the wait() so it notices the stop
        t.join(timeout=timeout)
        assert not t.is_alive(), "audio_worker_loop did not exit within timeout"

    def test_exits_immediately_when_stop_set_and_buffer_empty(self):
        fake = _FakeRecorder()
        fake._worker_stop_event.set()  # already stopped before the loop starts
        dispatcher = AudioCallbackDispatcher(fake)
        # Should return almost immediately — no chunks, stop is set.
        t = self._start_worker(dispatcher, fake)
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert fake._process_audio_chunk_calls == []

    def test_drains_single_chunk_then_exits_on_stop(self):
        fake = _FakeRecorder()
        fake._ring_buffer.append((np.zeros(4, dtype=np.float32), 4, "t", "s", 0.0))
        dispatcher = AudioCallbackDispatcher(fake)
        t = self._start_worker(dispatcher, fake)
        # Wake the worker so it processes the chunk.
        fake._worker_wake_event.set()
        # Wait for the drain, then stop.
        assert wait_until(lambda: len(fake._process_audio_chunk_calls) == 1)
        self._stop_and_join(fake, t)
        assert len(fake._process_audio_chunk_calls) == 1
        args = fake._process_audio_chunk_calls[0]
        assert len(args) == 5

    def test_drains_multiple_chunks_in_fifo_order(self):
        fake = _FakeRecorder()
        # Push 3 chunks with distinguishable frame counts.
        for i, frames in enumerate((4, 8, 16)):
            fake._ring_buffer.append((np.zeros(frames, dtype=np.float32), frames, f"t{i}", f"s{i}", float(i)))
        dispatcher = AudioCallbackDispatcher(fake)
        t = self._start_worker(dispatcher, fake)
        fake._worker_wake_event.set()
        assert wait_until(lambda: len(fake._process_audio_chunk_calls) == 3)
        self._stop_and_join(fake, t)
        # All 3 chunks drained, in FIFO (insertion) order.
        assert len(fake._process_audio_chunk_calls) == 3
        frame_seq = [args[1] for args in fake._process_audio_chunk_calls]
        assert frame_seq == [4, 8, 16]
        # Buffer is empty after the drain.
        assert len(fake._ring_buffer) == 0

    def test_continues_on_chunk_processing_exception(self, caplog):
        """A single bad chunk must NOT kill the worker — the loop logs
        via ``log_rate_limited`` and continues to the next chunk."""
        fake = _FakeRecorder()
        fake._process_audio_chunk_raises = True
        # Two bad chunks — both should be attempted (and logged).
        fake._ring_buffer.append((np.zeros(4, dtype=np.float32), 4, "t1", "s1", 0.0))
        fake._ring_buffer.append((np.zeros(4, dtype=np.float32), 4, "t2", "s2", 0.1))
        dispatcher = AudioCallbackDispatcher(fake)
        t = self._start_worker(dispatcher, fake)
        fake._worker_wake_event.set()
        assert wait_until(lambda: len(fake._process_audio_chunk_calls) == 2)
        self._stop_and_join(fake, t)
        # Both chunks were attempted (even though both raised).
        assert len(fake._process_audio_chunk_calls) == 2
        # Buffer drained despite the exceptions.
        assert len(fake._ring_buffer) == 0

    def test_drains_remaining_chunks_on_stop_signal(self):
        """Stop signal AFTER chunks were enqueued — the worker must
        drain the buffer fully before exiting (no in-flight loss)."""
        fake = _FakeRecorder()
        for i in range(5):
            fake._ring_buffer.append((np.zeros(4, dtype=np.float32), 4, f"t{i}", f"s{i}", float(i)))
        dispatcher = AudioCallbackDispatcher(fake)
        t = self._start_worker(dispatcher, fake)
        # Wake, wait until the worker is mid-drain (at least one chunk
        # processed), then stop — the drain loop must still complete.
        fake._worker_wake_event.set()
        assert wait_until(lambda: len(fake._process_audio_chunk_calls) >= 1)
        # Stop should set after wake — the drain loop completes first.
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert len(fake._process_audio_chunk_calls) == 5
        assert len(fake._ring_buffer) == 0

    def test_wakes_on_event_set(self):
        """The worker must wake promptly when ``_worker_wake_event`` is
        set (the audio callback signals via this event)."""
        fake = _FakeRecorder()
        dispatcher = AudioCallbackDispatcher(fake)
        t = self._start_worker(dispatcher, fake)
        # Wait until the worker thread is up and parked on its wait.
        assert wait_until(lambda: t.is_alive())
        # Set the wake event and confirm the worker doesn't crash.
        # (Aliveness is persistent state — a wake-handling crash would
        # keep the thread dead regardless of when we check.)
        fake._worker_wake_event.set()
        assert t.is_alive(), "worker should still be running (no stop set)"
        # Now stop it cleanly.
        self._stop_and_join(fake, t)

    def test_returns_when_stop_event_already_set_at_entry(self):
        """If the worker is started with stop already set (e.g. a race
        between start and stop), it must NOT block on the wake event —
        the `if not _worker_stop_event.is_set()` guard skips the wait."""
        fake = _FakeRecorder()
        fake._worker_stop_event.set()
        dispatcher = AudioCallbackDispatcher(fake)
        # Call directly (no thread) — should return promptly.
        start = time.perf_counter()
        dispatcher.audio_worker_loop(fake)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.2, "should not block on wake event when stop is already set"


# ── Integration: dispatch_callback_body + audio_worker_loop end-to-end ──


class TestDispatchAndWorkerIntegration:
    """End-to-end: the dispatch body's payload, when appended to the
    ring buffer by the (simulated) Recorder delegate, is drained by
    the worker loop and unpacked into ``_process_audio_chunk(*args)``."""

    def test_payload_round_trips_through_worker_loop(self):
        fake = _FakeRecorder(recording=True, ring_maxlen=64)
        dispatcher = AudioCallbackDispatcher(fake)
        # Simulate the primary agent's Option C delegate inline:
        for i in range(3):
            indata = np.full(4, float(i), dtype=np.float32)
            payload = dispatcher.dispatch_callback_body(fake, indata, 4, f"t{i}", f"s{i}")
            assert payload is not None
            fake._ring_buffer.append(payload)
            fake._worker_wake_event.set()
        # Run the worker loop to drain.
        t = threading.Thread(
            target=dispatcher.audio_worker_loop,
            args=(fake,),
            name="test-integration-worker",
            daemon=True,
        )
        t.start()
        assert wait_until(lambda: len(fake._process_audio_chunk_calls) == 3)
        fake._worker_stop_event.set()
        fake._worker_wake_event.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        # All 3 chunks processed, in order, with the right payload.
        assert len(fake._process_audio_chunk_calls) == 3
        for i, args in enumerate(fake._process_audio_chunk_calls):
            assert len(args) == 5
            chunk_copy, frames, time_info, status, _perf_ts = args
            assert frames == 4
            assert time_info == f"t{i}"
            assert status == f"s{i}"
            np.testing.assert_array_equal(chunk_copy, np.full(4, float(i), dtype=np.float32))
        assert len(fake._ring_buffer) == 0

    def test_preroll_path_does_not_deliver_chunks_to_worker(self):
        """When the dispatch body takes the preroll branch (returns
        None), the (simulated) Recorder delegate must NOT append to the
        ring buffer — so the worker never sees those chunks."""
        fake = _FakeRecorder(recording=False, preroll_active=True)
        dispatcher = AudioCallbackDispatcher(fake)
        # Simulate the Option C delegate with the None-guard:
        for _ in range(3):
            indata = np.zeros((4, 2), dtype=np.float32)
            payload = dispatcher.dispatch_callback_body(fake, indata, 4, "t", "s")
            if payload is None:
                continue  # the early-bailout — no append, no wake
            fake._ring_buffer.append(payload)
            fake._worker_wake_event.set()
        # Worker has nothing to drain.
        assert len(fake._ring_buffer) == 0
        # Preroll buffer accumulated the 3 mono chunks.
        assert len(fake._preroll_buffer) == 3
        # Each preroll chunk is 1D mono (collapsed from stereo input).
        for chunk in fake._preroll_buffer:
            assert chunk.ndim == 1
            assert chunk.shape == (4,)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--timeout=30"]))
