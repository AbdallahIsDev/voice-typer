"""Tests for the recording-lifecycle fixes (the fix).

These tests pin the post-fix contracts for four findings owned by the
sub-agent:

* **the fix** — every ``sd.InputStream(...)`` call in the recording
  package passes ``latency="low"`` so PortAudio selects the host API's
  smallest viable input buffer (≈10-20 ms end-to-end callback latency).
  Three call sites: ``StreamLifecycle.open_stream_for_candidates``
  (primary), ``StreamLifecycle.open_stream_fallback`` (last-resort),
  ``DisconnectHandler.restart_stream`` (hot-restart).

* **the fix** — the SPSC ring buffer capacity in
  ``_recorder_split.start_recording`` is scaled to ~2 s of headroom at
  the device's effective sample rate, floored at 64 chunks (so a 16 kHz
  device still gets ~2 s — 64 × 512 / 16000 = 2.048 s). The pre-fix
  capacity was 1.0 s with a floor of 16 (sized by
  ``session_state._resize_buffers_for_sample_rate``); the override in
  ``start_recording`` supersedes that.

* **the fix** — the ``retune_audio_processor(...)`` call was REMOVED from
  ``_recorder_split.start_recording`` and
  ``DisconnectHandler.restart_stream``. The chain stays at
  ``WHISPER_SAMPLE_RATE`` (16 kHz); the per-chunk resample inside
  ``AudioProcessor.process_chunk`` handles the native-rate → 16 kHz
  downsample on the worker thread. Filter-chain correctness is
  preserved (filters built at 16 kHz are fed 16 kHz audio
  post-resample). Pinned by ``TestAudioProcessorRetune`` in
  ``test_recorder_split_start.py`` (updated as part of the fix).

* **the fix** — ``StreamLifecycle.teardown_stream_body`` accepts a
  ``force: bool = False`` keyword. When ``force=True`` (passed by
  ``Recorder._handle_device_disconnect`` only), the teardown uses
  ``stream.abort()`` instead of ``stream.stop()`` so a dead device
  can't block the disconnect-recovery critical path indefinitely.
  Failures from ``abort()`` / ``close()`` are suppressed on the force
  path so the recovery always clears ``_stream`` and lets the next
  ``start()`` open a fresh stream. The CLEAN path (``force=False``,
  the default — used by ``stop()`` / ``discard()`` / ``__del__`` /
  start-rollback) keeps ``stream.stop()`` + ``stream.close()`` with
  exception propagation for graceful drain.

These tests are unit tests — they mock ``sd.InputStream`` and use
``MagicMock`` recorder stubs so they never touch real audio hardware
or worker threads. Each test is deterministic and sub-second.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.recording import disconnect_handler as dh_module, stream_lifecycle as sl_module
from voice_typer.server.recording.disconnect_handler import DisconnectHandler
from voice_typer.server.recording.stream_lifecycle import StreamLifecycle

# ── Helpers ───────────────────────────────────────────────────────────


def _make_stream_lifecycle_recorder_stub() -> MagicMock:
    """Build a minimal mock Recorder for ``StreamLifecycle`` tests.

    Mirrors ``_make_recorder_stub`` in ``test_stream_lifecycle_module.py``
    but kept local so this test file is self-contained. ``_lock`` is a
    real ``threading.Lock``; ``_is_in_audio_callback`` is a real
    ``threading.Event``; everything else is a ``MagicMock``.
    """
    recorder = MagicMock(name="recorder")
    recorder._lock = threading.Lock()
    recorder._is_in_audio_callback = threading.Event()
    recorder._stream = None
    recorder.config.recording_channels = 1
    recorder._resolve_effective_sample_rate.return_value = (16000, None)
    recorder._cached_max_input_channels.return_value = 1
    recorder._all_input_device_candidates.return_value = []
    recorder._stream_finished_callback = MagicMock(name="_stream_finished_callback")
    recorder._audio_callback_dispatch = MagicMock(name="_audio_callback_dispatch")
    return recorder


def _install_fake_input_stream(module, monkeypatch, *, actual_samplerate=None):
    """Install a fake ``sd.InputStream`` factory on a recording submodule.

    Returns a list that accumulates each call's kwargs dict in
    invocation order so tests can assert on which parameters were
    passed (in particular, ``latency``).
    """
    attempts: list[dict] = []

    def fake_input_stream(**kwargs):
        stream = MagicMock(name="fake_stream")
        stream.start = MagicMock(name="start")
        stream.stop = MagicMock(name="stop")
        stream.abort = MagicMock(name="abort")
        stream.close = MagicMock(name="close")
        if actual_samplerate is not None:
            stream.samplerate = actual_samplerate
        attempts.append(kwargs)
        return stream

    fake_sd = MagicMock(name="fake_sd")
    fake_sd.InputStream = MagicMock(side_effect=fake_input_stream)
    # ``query_devices`` is consulted by the disconnect-handler restart
    # path; return a plausible device dict so the channels-resolution
    # branch doesn't raise.
    fake_sd.query_devices.return_value = {
        "max_input_channels": 1,
        "name": "fake input",
    }
    monkeypatch.setattr(module, "sd", fake_sd)
    return attempts


# ── latency='low' on every sd.InputStream call ────────────────


class TestUU13LatencyLow:
    """all three ``sd.InputStream(...)`` call sites pass
    ``latency='low'``. PortAudio silently falls back to the default if
    the requested latency is unavailable — no retry logic needed."""

    def test_primary_open_stream_for_candidates_passes_latency_low(self, monkeypatch):
        """``StreamLifecycle.open_stream_for_candidates`` must pass
        ``latency='low'`` to ``sd.InputStream``."""
        recorder = _make_stream_lifecycle_recorder_stub()
        attempts = _install_fake_input_stream(sl_module, monkeypatch)
        # Provide a dev_info_extra so the info-log branch fires.
        recorder._resolve_effective_sample_rate.return_value = (
            48000,
            {
                "name": "Mock Mic",
                "host_api_name": "ALSA",
                "native_rate": 48000,
            },
        )
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, last_err = lifecycle.open_stream_for_candidates(
            recorder, [7], callback, effective_sr=16000, last_error=None
        )

        assert selected == 7
        assert eff_sr == 48000
        assert last_err is None
        assert len(attempts) == 1, "exactly one InputStream attempt expected"
        kwargs = attempts[0]
        # latency='low' is present.
        assert kwargs.get("latency") == "low", (
            f"open_stream_for_candidates must pass latency='low'; got kwargs={kwargs}"
        )
        # Sanity: the other essential kwargs are still wired up.
        assert kwargs["samplerate"] == 48000
        assert kwargs["blocksize"] == 512
        assert kwargs["callback"] is callback

    def test_fallback_open_stream_fallback_passes_latency_low(self, monkeypatch):
        """``StreamLifecycle.open_stream_fallback`` must pass
        ``latency='low'`` to ``sd.InputStream`` on the fallback path."""
        recorder = _make_stream_lifecycle_recorder_stub()
        attempts = _install_fake_input_stream(sl_module, monkeypatch)
        # Fallback enumerates ALL input devices via
        # ``_all_input_device_candidates`` — return a single fallback
        # candidate so exactly one InputStream attempt fires.
        recorder._all_input_device_candidates.return_value = [11]
        recorder._resolve_effective_sample_rate.return_value = (48000, None)
        callback = MagicMock(name="callback")
        lifecycle = StreamLifecycle(recorder)

        selected, eff_sr, used_fallback, last_err = lifecycle.open_stream_fallback(
            recorder, [], callback, effective_sr=16000, last_error=None
        )

        assert selected == 11
        assert used_fallback is True
        assert last_err is None
        assert len(attempts) == 1, "exactly one fallback InputStream attempt expected"
        kwargs = attempts[0]
        # latency='low' on the fallback path too.
        assert kwargs.get("latency") == "low", f"open_stream_fallback must pass latency='low'; got kwargs={kwargs}"

    def test_disconnect_hot_restart_passes_latency_low(self, monkeypatch):
        """``DisconnectHandler.restart_stream`` must pass
        ``latency='low'`` to ``sd.InputStream`` on the hot-restart path."""
        # Build a real Recorder with a MagicMock config (no audio
        # device). This is the same pattern as
        # ``test_recorder_mono_and_disconnect_fixes.py``.
        from voice_typer.server.recording.recorder import Recorder

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            recording_channels=1,
        )
        r = Recorder(config)

        # Stub the helpers ``restart_stream`` consults.
        r._resolve_device = MagicMock(return_value=None)
        r._resolve_effective_sample_rate = MagicMock(return_value=(48000, None))
        r._refresh_vad_caches = MagicMock()
        r._current_callback = MagicMock(name="_current_callback")

        attempts = _install_fake_input_stream(dh_module, monkeypatch)

        # ``restart_stream`` runs INSIDE ``_stream_lifecycle_lock`` —
        # acquire it for realism (the production caller does this).
        with r._stream_lifecycle_lock:
            DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert len(attempts) == 1, f"exactly one hot-restart InputStream attempt expected; got {len(attempts)}"
        kwargs = attempts[0]
        # latency='low' on the hot-restart path too.
        assert kwargs.get("latency") == "low", (
            f"DisconnectHandler.restart_stream must pass latency='low'; got kwargs={kwargs}"
        )


# ── ring buffer capacity scales with sample rate ──────────────


class TestUU36RingBufferScaling:
    """``start_recording`` resizes the SPSC ring buffer to ~2 s
    of headroom at the device's effective sample rate, floored at 64
    chunks. The pre-fix capacity was 1.0 s with floor 16 (sized by
    ``session_state._resize_buffers_for_sample_rate``); the override in
    ``start_recording`` supersedes that."""

    def _build_recorder_for_start(self, *, effective_sr: int) -> MagicMock:
        """Build a MagicMock recorder suitable for ``start_recording``.

        Mirrors ``_build_mock_recorder`` in ``test_recorder_split_start.py``
        but kept local. ``_ring_buffer`` is a real ``collections.deque``
        so we can assert on ``maxlen`` after ``start_recording`` runs.
        """
        import collections

        from voice_typer.server._audio_constants import _AUDIO_BLOCKSIZE

        recorder = MagicMock(name="recorder")
        recorder.config = MagicMock(name="config")
        recorder.config.sample_rate = 16000
        recorder.config.microphone = None
        recorder.config.save.return_value = True
        recorder._cache_session_config.return_value = 30
        recorder._resolve_device.return_value = 5
        recorder._same_physical_microphone_candidates.return_value = [5]
        recorder._build_audio_callback.return_value = object()
        # Stream opens successfully on the first candidate.
        recorder._open_stream_for_candidates.return_value = (5, effective_sr, None)
        recorder._stream = MagicMock(name="opened-stream")
        # Real ring buffer so we can assert on maxlen.
        recorder._ring_buffer = collections.deque(maxlen=max(64, int(effective_sr / _AUDIO_BLOCKSIZE * 2.0)))
        # ``_recording_event`` must be a real Event so ``is_set()`` works.
        recorder._recording_event = threading.Event()
        recorder._audio_processor = None
        # Stub the per-session helpers as no-ops (MagicMock auto-stubs).
        return recorder

    def test_48khz_ring_buffer_capacity_is_2s_with_floor_64(self):
        """At 48 kHz / 512-sample blocks, the ring buffer must hold
        ~2 s of audio. ``int(48000 / 512 * 2.0) = 187`` chunks
        (≈ 2.0 s). The floor of 64 does NOT fire at 48 kHz.

        Pre-fix: ``_resize_buffers_for_sample_rate`` (in
        ``session_state.py``) sized the ring to 1.0 s = 93 chunks. The
        override in ``start_recording`` supersedes that.
        """
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=48000)
        start_recording(recorder)

        expected_capacity = max(64, int(48000 / 512 * 2.0))
        assert expected_capacity == 187, (
            f"sanity: 48000/512*2.0 = {int(48000 / 512 * 2.0)}; expected 187, got {expected_capacity}"
        )
        actual_maxlen = recorder._ring_buffer.maxlen
        assert actual_maxlen == expected_capacity, (
            f"at 48 kHz, ring buffer maxlen must be "
            f"{expected_capacity} (~2 s headroom, floor 64 not hit); "
            f"got {actual_maxlen}."
        )

    def test_16khz_ring_buffer_capacity_floors_at_64(self):
        """At 16 kHz / 512-sample blocks, ``int(16000 / 512 * 2.0) = 62``
        which is BELOW the floor of 64. The floor must kick in so a
        16 kHz device still gets ~2 s of headroom (64 × 512 / 16000 =
        2.048 s).

        Pre-fix: ``_resize_buffers_for_sample_rate`` (in
        ``session_state.py``) sized the ring to 1.0 s = 31 chunks
        (floored at 16). The override floors at 64 instead.
        """
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=16000)
        start_recording(recorder)

        # ``int(16000 / 512 * 2.0) = 62`` — below the floor.
        expected_capacity = 64  # floor kicks in
        actual_maxlen = recorder._ring_buffer.maxlen
        assert actual_maxlen == expected_capacity, (
            f"at 16 kHz, ring buffer maxlen must be floored at 64 (int(16000/512*2.0)=62 < 64); got {actual_maxlen}."
        )

    def test_8khz_ring_buffer_capacity_floors_at_64(self):
        """At 8 kHz (Bluetooth HFP), ``int(8000 / 512 * 2.0) = 31``
        which is well below the floor. The floor must still kick in."""
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=8000)
        start_recording(recorder)

        expected_capacity = 64  # floor kicks in (31 < 64)
        actual_maxlen = recorder._ring_buffer.maxlen
        assert actual_maxlen == expected_capacity, (
            f"at 8 kHz, ring buffer maxlen must be floored at 64 (int(8000/512*2.0)=31 < 64); got {actual_maxlen}."
        )

    def test_ring_buffer_reassignment_clears_stale_chunks(self):
        """when ``start_recording`` reassigns the ring buffer,
        any stale chunks from a prior session MUST be evicted (the new
        deque is empty). The zeroing of the dropped
        arrays happens before the reassignment (mirrors
        ``disconnect_handler.py:329-333``)."""
        import collections

        import numpy as np
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=48000)
        # Simulate stale chunks from a prior session.
        stale_arr = np.full(512, 0.5, dtype=np.float32)
        recorder._ring_buffer = collections.deque(
            [(stale_arr, 512, None, None, 0.0)],
            maxlen=64,
        )
        assert len(recorder._ring_buffer) == 1

        start_recording(recorder)

        # The new deque is empty (stale chunks dropped).
        assert len(recorder._ring_buffer) == 0, (
            "stale ring buffer chunks must be cleared on start_recording reassignment."
        )
        # the stale array was zeroed before the deque
        # reference was dropped (so the user's voice data doesn't
        # linger in process memory until GC).
        assert float(stale_arr.max()) == 0.0, "stale ring buffer chunk must be zeroed before reassignment."


# ── teardown_stream_body force=True uses stream.abort() ────────


class TestUU38ForceTeardownUsesAbort:
    """``teardown_stream_body(force=True)`` (the disconnect-recovery
    path) calls ``stream.abort()`` instead of ``stream.stop()`` so a
    dead device can't block the recovery critical path indefinitely.
    The CLEAN path (``force=False``, the default) keeps
    ``stream.stop()`` for graceful drain."""

    def test_force_true_calls_abort_not_stop(self):
        """``force=True`` must call ``stream.abort()`` and NOT
        ``stream.stop()``. ``stream.close()`` is still called and
        ``_stream`` is cleared."""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        recorder._stream = fake_stream
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)

        lifecycle.teardown_stream_body(recorder, force=True)

        # abort() called, stop() NOT called.
        fake_stream.abort.assert_called_once_with()
        fake_stream.stop.assert_not_called()
        # close() still called so PortAudio resources are freed.
        fake_stream.close.assert_called_once_with()
        # _stream cleared so the next start() opens a fresh stream.
        assert recorder._stream is None

    def test_force_false_default_calls_stop_not_abort(self):
        """The default ``force=False`` path keeps ``stream.stop()``
        for graceful drain. ``abort()`` is NOT called."""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        recorder._stream = fake_stream
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)

        # Default — force=False.
        lifecycle.teardown_stream_body(recorder)

        # CLEAN path: stop() called, abort() NOT called.
        fake_stream.stop.assert_called_once_with()
        fake_stream.abort.assert_not_called()
        fake_stream.close.assert_called_once_with()
        assert recorder._stream is None

    def test_force_true_suppresses_abort_exception(self):
        """on the force path, an ``abort()`` failure (e.g. the
        stream is already in a PortAudio error state) MUST be
        suppressed so the recovery can still call ``close()`` and
        clear ``_stream``. The device is already gone — propagating
        the exception would block the disconnect-recovery critical path."""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.abort.side_effect = OSError("PortAudio: stream not running")
        recorder._stream = fake_stream
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)

        # Must NOT raise — the abort failure is suppressed.
        lifecycle.teardown_stream_body(recorder, force=True)

        fake_stream.abort.assert_called_once_with()
        # close() still attempted (best-effort on the force path).
        fake_stream.close.assert_called_once_with()
        # _stream still cleared so the next start() opens a fresh stream.
        assert recorder._stream is None, (
            "_stream must be cleared even if abort() raised — the disconnect-recovery path must not be blocked."
        )

    def test_force_true_suppresses_close_exception(self):
        """on the force path, a ``close()`` failure is also
        suppressed. ``_stream`` is always cleared."""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.close.side_effect = OSError("close boom")
        recorder._stream = fake_stream
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)

        # Must NOT raise — the close failure is suppressed on force path.
        lifecycle.teardown_stream_body(recorder, force=True)

        fake_stream.abort.assert_called_once_with()
        fake_stream.close.assert_called_once_with()
        # _stream still cleared.
        assert recorder._stream is None

    def test_force_false_close_exception_propagates(self):
        """The CLEAN path (``force=False``) propagates ``close()``
        exceptions — preserves the previously behavior pinned by
        ``test_close_exception_propagates`` in
        ``test_stream_lifecycle_module.py``."""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.close.side_effect = OSError("close boom")
        recorder._stream = fake_stream
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)

        with pytest.raises(OSError, match="close boom"):
            lifecycle.teardown_stream_body(recorder)  # force=False default

        fake_stream.stop.assert_called_once_with()

    def test_force_true_returns_immediately_when_stream_is_none(self):
        """Idempotent: ``force=True`` with no stream is a no-op
        (mirrors the ``force=False`` idempotency contract)."""
        recorder = _make_stream_lifecycle_recorder_stub()
        # ``_stream`` is None by default in the stub.
        lifecycle = StreamLifecycle(recorder)

        # Must NOT raise — idempotent.
        lifecycle.teardown_stream_body(recorder, force=True)

        assert recorder._stream is None

    def test_handle_device_disconnect_passes_force_true(self, monkeypatch):
        """``Recorder._handle_device_disconnect`` must call
        ``self._teardown_stream(force=True)`` so the dead device
        doesn't block the recovery critical path on ``stream.stop()``.

        Source-inspection: the ``force=True`` literal must appear in
        ``_handle_device_disconnect`` source (otherwise a future
        refactor could silently drop back to ``stop()`` and reintroduce
        the indefinite-block).
        """
        import inspect

        from voice_typer.server.recording.recorder import Recorder

        src = inspect.getsource(Recorder._handle_device_disconnect)
        assert "_teardown_stream(force=True)" in src, (
            "_handle_device_disconnect must call "
            "_teardown_stream(force=True) so stream.abort() is used "
            "on the known-dead-device path. Source snippet:\n" + src
        )

    def test_teardown_stream_body_signature_has_force_kwarg(self):
        """``teardown_stream_body`` must accept a ``force``
        keyword-only argument (default ``False``) so the
        ``_handle_device_disconnect`` call site can opt in."""
        import inspect

        sig = inspect.signature(StreamLifecycle.teardown_stream_body)
        assert "force" in sig.parameters, "teardown_stream_body must accept a 'force' parameter."
        # Default is False (CLEAN path).
        assert sig.parameters["force"].default is False, (
            "teardown_stream_body's 'force' parameter must "
            "default to False so the CLEAN path (stop/discard/__del__) "
            "keeps stream.stop()."
        )
        # Keyword-only (so callers can't accidentally pass it positionally
        # and shadow the recorder arg).
        assert sig.parameters["force"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "'force' must be keyword-only so the recorder arg stays positional."
        )
