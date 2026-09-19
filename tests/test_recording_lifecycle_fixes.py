"""Tests for the recording-lifecycle fixes (the fix)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server.recording import disconnect_handler as dh_module, stream_lifecycle as sl_module
from voice_typer.server.recording.disconnect_handler import DisconnectHandler
from voice_typer.server.recording.stream_lifecycle import StreamLifecycle


def _make_stream_lifecycle_recorder_stub() -> MagicMock:
    """Build a minimal mock Recorder for ``StreamLifecycle`` tests."""
    recorder = MagicMock(name="recorder")
    recorder._audio_pipeline._lock = threading.Lock()
    recorder._is_in_audio_callback = threading.Event()
    recorder._stream_lifecycle._stream = None
    recorder.config.recording_channels = 1
    recorder._devices._resolve_effective_sample_rate.return_value = (16000, None)
    recorder._devices._cached_max_input_channels.return_value = 1
    recorder._devices._all_input_device_candidates.return_value = []
    recorder._stream_finished_callback = MagicMock(name="_stream_finished_callback")
    recorder._audio_callback_dispatch = MagicMock(name="_audio_callback_dispatch")
    return recorder


def _install_fake_input_stream(module, monkeypatch, *, actual_samplerate=None):
    """Install a fake ``sd.InputStream`` factory on a recording submodule."""
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
    fake_sd.query_devices.return_value = {
        "max_input_channels": 1,
        "name": "fake input",
    }
    monkeypatch.setattr(module, "sd", fake_sd)
    return attempts


class TestLatencyLow:
    """all three ``sd.InputStream(...)`` call sites pass"""

    def test_primary_open_stream_for_candidates_passes_latency_low(self, monkeypatch):
        """``StreamLifecycle.open_stream_for_candidates`` must pass"""
        recorder = _make_stream_lifecycle_recorder_stub()
        attempts = _install_fake_input_stream(sl_module, monkeypatch)
        # Provide a dev_info_extra so the info-log branch fires.
        recorder._devices._resolve_effective_sample_rate.return_value = (
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
        assert kwargs.get("latency") == "low", (
            f"open_stream_for_candidates must pass latency='low'; got kwargs={kwargs}"
        )
        # Sanity: the other essential kwargs are still wired up.
        assert kwargs["samplerate"] == 48000
        # VAD-001 (rate-scaled): ~32 ms blocks at the native rate —
        assert kwargs["blocksize"] == scaled_audio_blocksize(48000)
        assert kwargs["blocksize"] == 1536
        assert kwargs["callback"] is callback

    def test_fallback_open_stream_fallback_passes_latency_low(self, monkeypatch):
        """``StreamLifecycle.open_stream_fallback`` must pass"""
        recorder = _make_stream_lifecycle_recorder_stub()
        attempts = _install_fake_input_stream(sl_module, monkeypatch)
        # Fallback enumerates ALL input devices via
        recorder._devices._all_input_device_candidates.return_value = [11]
        recorder._devices._resolve_effective_sample_rate.return_value = (48000, None)
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
        assert kwargs.get("latency") == "low", f"open_stream_fallback must pass latency='low'; got kwargs={kwargs}"

    def test_disconnect_hot_restart_passes_latency_low(self, monkeypatch):
        """``DisconnectHandler.restart_stream`` must pass"""
        # Build a real Recorder with a MagicMock config (no audio
        from voice_typer.server.recording.recorder import Recorder

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            recording_channels=1,
        )
        r = Recorder(config)

        r._current_callback = MagicMock(name="_current_callback")

        attempts = _install_fake_input_stream(dh_module, monkeypatch)

        # ``restart_stream`` runs INSIDE ``_stream_lifecycle_lock`` —
        with r._stream_lifecycle_lock:
            DisconnectHandler(r).restart_stream(_captured_generation=0)

        assert len(attempts) == 1, f"exactly one hot-restart InputStream attempt expected; got {len(attempts)}"
        kwargs = attempts[0]
        assert kwargs.get("latency") == "low", (
            f"DisconnectHandler.restart_stream must pass latency='low'; got kwargs={kwargs}"
        )


class TestRingBufferScaling:
    """``start_recording`` resizes the SPSC ring buffer to ~2 s"""

    def _build_recorder_for_start(self, *, effective_sr: int) -> MagicMock:
        """Build a MagicMock recorder suitable for ``start_recording``."""
        import collections

        recorder = MagicMock(name="recorder")
        recorder.config = MagicMock(name="config")
        recorder.config.sample_rate = 16000
        recorder.config.microphone = None
        recorder.config.save.return_value = True
        recorder._session_state.cache_session_config.return_value = 30
        recorder._devices._resolve_device.return_value = 5
        recorder._devices._same_physical_microphone_candidates.return_value = [5]
        recorder._stream_lifecycle.build_audio_callback.return_value = object()
        # Stream opens successfully on the first candidate.
        recorder._stream_lifecycle.open_stream_for_candidates.return_value = (5, effective_sr, None)
        recorder._stream_lifecycle._stream = MagicMock(name="opened-stream")
        # Real ring buffer so we can assert on maxlen (pre-state sized
        from voice_typer.server._audio_constants import scaled_audio_blocksize

        recorder._ring_buffer = collections.deque(
            maxlen=max(64, int(effective_sr / scaled_audio_blocksize(effective_sr) * 2.0))
        )
        # Real scalars: ``start_recording`` calls the module-level
        recorder._audio_pipeline._buffer_sr = None
        recorder._effective_sr = effective_sr
        # ``_recording_event`` must be a real Event so ``is_set()`` works.
        recorder._recording_event = threading.Event()
        recorder._audio_processor = None
        # Stub the per-session helpers as no-ops (MagicMock auto-stubs).
        return recorder

    def test_48khz_ring_buffer_capacity_is_2s_with_floor_64(self):
        """At 48 kHz the stream delivers rate-scaled ~32 ms chunks"""
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=48000)
        start_recording(recorder)

        expected_capacity = max(64, int(48000 / scaled_audio_blocksize(48000) * 2.0))
        assert expected_capacity == 64, (
            f"sanity: 48000/{scaled_audio_blocksize(48000)}*2.0 = "
            f"{int(48000 / scaled_audio_blocksize(48000) * 2.0)}; expected the 64-chunk floor, got {expected_capacity}"
        )
        actual_maxlen = recorder._ring_buffer.maxlen
        assert actual_maxlen == expected_capacity, (
            f"at 48 kHz, ring buffer maxlen must be "
            f"{expected_capacity} (~2 s headroom via the 64-chunk floor); "
            f"got {actual_maxlen}."
        )
        # DURATION contract: the floored capacity still holds ~2 s of audio.
        assert actual_maxlen * scaled_audio_blocksize(48000) / 48000 >= 2.0

    def test_16khz_ring_buffer_capacity_floors_at_64(self):
        """At 16 kHz / 512-sample blocks, ``int(16000 / 512 * 2.0) = 62``"""
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=16000)
        start_recording(recorder)

        # ``int(16000 / 512 * 2.0) = 62``, below the floor.
        expected_capacity = 64  # floor kicks in
        actual_maxlen = recorder._ring_buffer.maxlen
        assert actual_maxlen == expected_capacity, (
            f"at 16 kHz, ring buffer maxlen must be floored at 64 (int(16000/512*2.0)=62 < 64); got {actual_maxlen}."
        )

    def test_8khz_ring_buffer_capacity_floors_at_64(self):
        """At 8 kHz (Bluetooth HFP), ``int(8000 / 512 * 2.0) = 31``"""
        from voice_typer.server.recording._recorder_split import start_recording

        recorder = self._build_recorder_for_start(effective_sr=8000)
        start_recording(recorder)

        expected_capacity = 64  # floor kicks in (31 < 64)
        actual_maxlen = recorder._ring_buffer.maxlen
        assert actual_maxlen == expected_capacity, (
            f"at 8 kHz, ring buffer maxlen must be floored at 64 (int(8000/512*2.0)=31 < 64); got {actual_maxlen}."
        )

    def test_ring_buffer_reassignment_clears_stale_chunks(self):
        """when ``start_recording`` reassigns the ring buffer,"""
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
        assert float(stale_arr.max()) == 0.0, "stale ring buffer chunk must be zeroed before reassignment."


class TestForceTeardownUsesAbort:
    """``teardown_stream_body(force=True)`` (the disconnect-recovery"""

    def test_force_true_calls_abort_not_stop(self):
        """``force=True`` must call ``stream.abort()`` and NOT"""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: set the stream on the OWNING lifecycle
        lifecycle._stream = fake_stream

        lifecycle.teardown_stream_body(recorder, force=True)

        fake_stream.abort.assert_called_once_with()
        fake_stream.stop.assert_not_called()
        fake_stream.close.assert_called_once_with()
        # _stream cleared so the next start() opens a fresh stream.
        assert lifecycle._stream is None

    def test_force_false_default_calls_stop_not_abort(self):
        """The default ``force=False`` path keeps ``stream.stop()``"""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: set the stream on the OWNING lifecycle
        lifecycle._stream = fake_stream

        # Default, force=False.
        lifecycle.teardown_stream_body(recorder)

        # CLEAN path: stop() called, abort() NOT called.
        fake_stream.stop.assert_called_once_with()
        fake_stream.abort.assert_not_called()
        fake_stream.close.assert_called_once_with()
        assert lifecycle._stream is None

    def test_force_true_suppresses_abort_exception(self):
        """stream is already in a PortAudio error state) MUST be"""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.abort.side_effect = OSError("PortAudio: stream not running")
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: set the stream on the OWNING lifecycle
        lifecycle._stream = fake_stream

        # Must NOT raise, the abort failure is suppressed.
        lifecycle.teardown_stream_body(recorder, force=True)

        fake_stream.abort.assert_called_once_with()
        fake_stream.close.assert_called_once_with()
        # _stream still cleared so the next start() opens a fresh stream.
        assert lifecycle._stream is None, (
            "_stream must be cleared even if abort() raised, the disconnect-recovery path must not be blocked."
        )

    def test_force_true_suppresses_close_exception(self):
        """on the force path, a ``close()`` failure is also"""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.close.side_effect = OSError("close boom")
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: set the stream on the OWNING lifecycle
        lifecycle._stream = fake_stream

        # Must NOT raise, the close failure is suppressed on force path.
        lifecycle.teardown_stream_body(recorder, force=True)

        fake_stream.abort.assert_called_once_with()
        fake_stream.close.assert_called_once_with()
        # _stream still cleared.
        assert lifecycle._stream is None

    def test_force_false_close_exception_propagates(self):
        """The CLEAN path (``force=False``) propagates ``close()``"""
        recorder = _make_stream_lifecycle_recorder_stub()
        fake_stream = MagicMock(name="fake_stream")
        fake_stream.close.side_effect = OSError("close boom")
        recorder._is_in_audio_callback.clear()
        lifecycle = StreamLifecycle(recorder)
        # STATE-OWNERSHIP: set the stream on the OWNING lifecycle
        lifecycle._stream = fake_stream

        with pytest.raises(OSError, match="close boom"):
            lifecycle.teardown_stream_body(recorder)  # force=False default

        fake_stream.stop.assert_called_once_with()

    def test_force_true_returns_immediately_when_stream_is_none(self):
        """Idempotent: ``force=True`` with no stream is a no-op"""
        recorder = _make_stream_lifecycle_recorder_stub()
        # ``_stream`` is None by default in the stub.
        lifecycle = StreamLifecycle(recorder)

        # Must NOT raise, idempotent.
        lifecycle.teardown_stream_body(recorder, force=True)

        assert lifecycle._stream is None

    def test_handle_device_disconnect_passes_force_true(self, monkeypatch):
        """``Recorder._handle_device_disconnect`` must call"""
        import inspect

        from voice_typer.server.recording.recorder import Recorder

        src = inspect.getsource(Recorder._handle_device_disconnect)
        assert "_teardown_stream(force=True)" in src, (
            "_handle_device_disconnect must call "
            "_teardown_stream(force=True) so stream.abort() is used "
            "on the known-dead-device path. Source snippet:\n" + src
        )

    def test_teardown_stream_body_signature_has_force_kwarg(self):
        """``teardown_stream_body`` must accept a ``force``"""
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
        assert sig.parameters["force"].kind == inspect.Parameter.KEYWORD_ONLY, (
            "'force' must be keyword-only so the recorder arg stays positional."
        )
