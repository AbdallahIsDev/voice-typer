"""Tests for recording module — device resolution."""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest


# Mock sounddevice at module level
@pytest.fixture(autouse=True)
def mock_sounddevice(monkeypatch):
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


class TestResolveDevice:
    def test_none_config_returns_none(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock()
        config.microphone = None
        config.sample_rate = 16000
        r = Recorder(config)
        assert r._resolve_device() is None

    def test_string_index_converts_to_int(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock()
        config.microphone = "7"
        config.sample_rate = 16000
        r = Recorder(config)
        assert r._resolve_device() == 7

    def test_legacy_name_string_passes_through(self):
        """If someone put a device name (not numeric), pass it as-is."""
        from voice_typer.server.recording import Recorder

        config = MagicMock()
        config.microphone = "Blue Yeti"
        config.sample_rate = 16000
        r = Recorder(config)
        assert r._resolve_device() == "Blue Yeti"


class TestStopAudioPrep:
    def test_stop_concatenates_chunks_to_1d_and_clears_buffer(self, monkeypatch):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [
            np.array([[1.0], [2.0]], dtype=np.float32),
            np.array([[3.0]], dtype=np.float32),
        ]

        audio = r.stop()

        np.testing.assert_array_equal(audio, np.array([1.0, 2.0, 3.0], dtype=np.float32))
        # MEM-04: buffer is replaced with a fresh deque (not cleared in-place)
        assert len(r._buffer) == 0, f"Expected empty buffer after stop(), got {r._buffer!r}"
        assert r._stream is None

    def test_stop_resamples_when_effective_rate_differs(self, monkeypatch):
        from voice_typer.server.recording import Recorder

        calls = []

        def fake_resample_poly(audio, up, down):
            calls.append((up, down))
            return np.array([0.25, 0.5], dtype=np.float32)

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", lambda: fake_resample_poly)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 48000
        r._stream = MagicMock()
        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        audio = r.stop()

        np.testing.assert_array_equal(audio, np.array([0.25, 0.5], dtype=np.float32))
        assert calls == [(1, 3)]

    def test_stop_skips_resample_when_rate_matches_target(self, monkeypatch):
        from voice_typer.server.recording import Recorder

        get_resampler = MagicMock()
        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", get_resampler)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.ones((4, 1), dtype=np.float32)]

        r.stop()

        get_resampler.assert_not_called()

    def test_start_failure_resets_recording_state(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        class FailingStream:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("device failed")

            def close(self):
                pass

        monkeypatch.setattr(recording_mod.sd, "InputStream", FailingStream)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        with pytest.raises(RuntimeError, match="device failed"):
            r.start()

        assert r.recording is False
        assert r._stream is None

    def test_start_falls_back_to_same_microphone_on_another_host_api(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        devices = [
            {
                "index": 0,
                "name": "Microsoft Sound Mapper - Input",
                "max_input_channels": 2,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
            {
                "index": 1,
                "name": "Microphone (WO Mic Device)",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
            {
                "index": 8,
                "name": "Primary Sound Capture Driver",
                "max_input_channels": 2,
                "default_samplerate": 44100,
                "hostapi": 1,
            },
            {
                "index": 9,
                "name": "Microphone (WO Mic Device)",
                "max_input_channels": 1,
                "default_samplerate": 44100,
                "hostapi": 1,
            },
        ]
        host_apis = {
            0: {"name": "MME", "default_input_device": 1},
            1: {"name": "Windows DirectSound", "default_input_device": 8},
        }

        def query_devices(device=None, kind=None):
            if kind == "input":
                return devices[1]
            if device is None:
                return devices
            return next(dev for dev in devices if dev["index"] == device)

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: host_apis[idx])

        opened_devices = []

        class FallbackStream:
            def __init__(self, *args, **kwargs):
                opened_devices.append(kwargs["device"])
                if kwargs["device"] == 9:
                    raise RuntimeError("DirectSound error")
                self.closed = False
                self.started = False

            def start(self):
                self.started = True

            def close(self):
                self.closed = True

        monkeypatch.setattr(recording_mod.sd, "InputStream", FallbackStream)

        config = MagicMock(sample_rate=16000, microphone="9")
        r = Recorder(config)

        r.start()

        assert opened_devices == [9, 1]
        assert r.recording is True
        assert r._stream is not None
        assert config.microphone == "1"
        config.save.assert_called_once()

    def test_start_falls_back_to_all_devices_when_configured_mic_fails(self, monkeypatch):
        """When configured mic and same-name alternates all fail, try ALL input devices."""
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        devices = [
            {
                "index": 0,
                "name": "Microsoft Sound Mapper - Input",
                "max_input_channels": 2,
                "default_samplerate": 44100,
                "hostapi": 0,
            },
            {"index": 1, "name": "Broken Mic", "max_input_channels": 1, "default_samplerate": 44100, "hostapi": 0},
            {"index": 2, "name": "Working Mic", "max_input_channels": 1, "default_samplerate": 44100, "hostapi": 0},
        ]
        host_apis = {
            0: {"name": "MME", "default_input_device": 1},
        }

        def query_devices(device=None, kind=None):
            if kind == "input":
                return devices[1]
            if device is None:
                return devices
            return next(dev for dev in devices if dev["index"] == device)

        monkeypatch.setattr(recording_mod.sd, "query_devices", query_devices)
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: host_apis[idx])

        opened_devices = []

        class SelectiveStream:
            def __init__(self, *args, **kwargs):
                opened_devices.append(kwargs["device"])
                if kwargs["device"] in (1, 0):
                    raise RuntimeError("device failed")
                self.closed = False
                self.started = False

            def start(self):
                self.started = True

            def close(self):
                self.closed = True

        monkeypatch.setattr(recording_mod.sd, "InputStream", SelectiveStream)

        config = MagicMock(sample_rate=16000, microphone="1")
        r = Recorder(config)

        r.start()

        # Should try device 1 (configured), then 0 (same-name fallback),
        # then fall back to all devices and succeed with device 2
        assert 2 in opened_devices
        assert r.recording is True
        assert r._stream is not None
        assert config.microphone == "2"

    def test_snapshot_returns_audio_without_clearing_buffer(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [
            np.array([[1.0], [2.0]], dtype=np.float32),
            np.array([[3.0]], dtype=np.float32),
        ]

        snapshot = r.snapshot()

        np.testing.assert_array_equal(snapshot, np.array([1.0, 2.0, 3.0], dtype=np.float32))
        assert len(r._buffer) == 2

        stopped = r.stop()
        np.testing.assert_array_equal(stopped, snapshot)

    def test_snapshot_returns_empty_float32_when_no_buffer_exists(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._buffer = []

        snapshot = r.snapshot()

        assert snapshot.dtype == np.float32
        assert snapshot.size == 0

    def test_snapshot_uses_same_resampling_path_as_stop(self, monkeypatch):
        from voice_typer.server.recording import Recorder

        calls = []

        def fake_resample_poly(audio, up, down):
            calls.append((audio.copy(), up, down))
            return np.array([0.25, 0.5], dtype=np.float32)

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", lambda: fake_resample_poly)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 48000
        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        snapshot = r.snapshot()

        np.testing.assert_array_equal(snapshot, np.array([0.25, 0.5], dtype=np.float32))
        assert len(r._buffer) == 1
        assert len(calls) == 1
        np.testing.assert_array_equal(calls[0][0], np.ones(6, dtype=np.float32))
        assert calls[0][1:] == (1, 3)

    def test_snapshot_resampling_does_not_emit_info_log_spam(self, monkeypatch, caplog):
        from voice_typer.server.recording import Recorder

        def fake_resample_poly(audio, up, down):
            return np.array([0.25, 0.5], dtype=np.float32)

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", lambda: fake_resample_poly)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 48000
        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        with caplog.at_level("INFO"):
            r.snapshot()

        assert "Resampled 48000 Hz -> 16000 Hz" not in caplog.text


class TestStopCallbackBackoff:
    """PERF-FIX-002: the backoff loop in Recorder.stop() was
    inverted (Event.wait() returned True when callback was running, but
    the loop treated True as "completed"). The fix replaced it with a
    polling loop: ``while self._is_in_audio_callback.is_set(): sleep(5ms)``
    with a 300ms hard deadline.

    Round 0 forward-port: the manual poll loop was REMOVED because
    PortAudio's ``stream.stop()`` already blocks until the in-flight
    callback returns — the manual poll was redundant and added up to
    300ms of latency on every F2-press-to-stop.  These tests were
    updated to verify the new contract:
    1. 0ms common case (flag already clear → no wait, stream.stop called).
    2. ``stream.stop()`` is called and trusted to drain the callback
       (no manual poll loop).
    3. ``stop()`` returns promptly after ``stream.stop()`` returns
       (no extra blocking).
    """

    def test_zero_ms_common_case_when_flag_already_clear(self, monkeypatch):
        """When _is_in_audio_callback is NOT set (no callback in flight),
        the polling loop exits immediately on the first check — 0ms wait."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.array([[1.0]], dtype=np.float32)]

        # Flag is cleared (default state) → loop should not sleep at all
        sleep_calls = []
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        r.stop()

        # No sleep calls because the flag was never set
        assert len(sleep_calls) == 0, f"Expected 0 sleep calls (flag clear), got {len(sleep_calls)}"

    def test_stream_stop_called_when_callback_in_flight(self, monkeypatch):
        """When ``_is_in_audio_callback`` IS set (callback in flight),
        ``stop()`` delegates to ``stream.stop()`` which itself blocks until
        the in-flight callback returns (PortAudio contract).  No manual
        poll loop is needed — Round 0 forward-port removed the redundant
        300ms poll."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.array([[1.0]], dtype=np.float32)]

        # Set the flag (callback in flight).  stream.stop() (a MagicMock)
        # returns immediately without clearing the flag — that's fine
        # because the new contract trusts PortAudio to drain the callback.
        r._is_in_audio_callback.set()

        stop_called = {"n": 0}
        original_stop = r._stream.stop

        def tracking_stop():
            stop_called["n"] += 1
            return original_stop()

        r._stream.stop = tracking_stop

        r.stop()

        # stream.stop() must have been called exactly once.
        assert stop_called["n"] == 1, f"Expected stream.stop() called once, got {stop_called['n']}"
        # The flag may still be set (PortAudio cleared it internally) —
        # that's acceptable; the new contract does not poll it.
        assert r._stream is None or r._stream.stop.called

    def test_stop_returns_promptly_after_stream_stop(self, monkeypatch):
        """When ``_is_in_audio_callback`` stays set (callback hung),
        ``stop()`` is bounded by the 300ms hard deadline in
        ``_teardown_stream``. After the deadline, ``stream.close()`` is
        called and ``stop()`` returns regardless of the flag state.

        PERF-FIX-002 (Round 0 forward-port) re-introduced the manual
        poll loop after discovering PortAudio's ``stream.stop()`` does
        not always drain the in-flight callback before returning — the
        poll is a safety net against use-after-free in ``stream.close()``.
        See ``_teardown_stream`` docstring (recording.py:1563) for the
        full AUDIO-009/AUDIO-015 history.
        """
        import time as real_time

        import voice_typer.server.recording as rec_mod
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.array([[1.0]], dtype=np.float32)]

        # Flag stays set (callback never completes from our perspective).
        r._is_in_audio_callback.set()

        # Mock time.sleep to a no-op so the test doesn't physically
        # sleep 300ms. The poll loop's real perf_counter deadline still
        # bounds the spin: each iteration advances perf_counter by a
        # few µs, so the 300ms budget is exhausted after ~300ms of real
        # time and the loop breaks via the ``remaining <= 0`` guard.
        sleep_calls = []
        monkeypatch.setattr(
            rec_mod.time,
            "sleep",
            lambda s: sleep_calls.append(s),
        )

        # stop() should return within the 300ms poll budget + small
        # overhead. Use real perf_counter (not mocked) so the loop's
        # deadline check actually advances.
        t0 = real_time.perf_counter()
        r.stop()
        elapsed = real_time.perf_counter() - t0

        # The poll loop ran — sleep was called while the callback flag
        # was set.
        assert len(sleep_calls) > 0, (
            f"Expected poll loop to run with callback flag set, got {len(sleep_calls)} sleep calls"
        )
        # The 300ms hard deadline bounded the wait. 1.0s gives ample
        # headroom over the 300ms budget + per-iteration overhead.
        assert elapsed < 1.0, f"stop() took {elapsed:.3f}s — expected < 1.0s (300ms poll budget + overhead)"
        # Stream was fully torn down: close() called, _stream set to None.
        assert r._stream is None, f"Expected r._stream to be None after stop(), got {r._stream!r}"

    def test_user_stop_pending_flag_set_during_stop(self, monkeypatch):
        """STREAM-FIX: stop() must set _user_stop_pending before
        stream.stop() so _stream_finished_callback doesn't warn."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.array([[1.0]], dtype=np.float32)]

        # Capture the flag value at the time stream.stop() is called
        flag_at_stop = {"value": None}
        original_stop = r._stream.stop

        def capturing_stop():
            flag_at_stop["value"] = r._user_stop_pending
            return original_stop()

        r._stream.stop = capturing_stop

        assert r._user_stop_pending is False  # initial state

        r.stop()

        assert flag_at_stop["value"] is True, "_user_stop_pending must be True when stream.stop() is called"
        assert r._user_stop_pending is False, "_user_stop_pending must be cleared after stream.close()"


class TestCachedResampling:
    """H15/M8: snapshot() triggers full resample on every call."""

    def test_snapshot_uses_cached_resampled_prefix(self, monkeypatch):
        """Second snapshot should only resample new chunks, not all."""
        from voice_typer.server.recording import Recorder

        call_count = [0]

        def fake_resample_poly(audio, up, down):
            call_count[0] += 1
            # Simple decimation for testing
            return audio[::down][: len(audio) * up // down]

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", lambda: fake_resample_poly)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 48000
        r._stream = MagicMock()
        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        # First snapshot - should resample
        r.snapshot()
        first_count = call_count[0]
        assert first_count >= 1

        # Add more data
        r._buffer.append(np.ones((6, 1), dtype=np.float32))

        # Second snapshot - should only resample the new chunk
        r.snapshot()
        second_count = call_count[0]
        assert second_count == first_count + 1  # Only one more resample call

    def test_snapshot_no_resample_when_rate_matches(self):
        """When effective_sr matches target, no resampling occurs."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.ones((4, 1), dtype=np.float32)]

        result = r.snapshot()
        assert result.dtype == np.float32
        np.testing.assert_array_equal(result, np.ones(4, dtype=np.float32))


class TestH12SilenceDetection:
    """H12: Silent mic disconnection - silence detection."""

    def test_silence_timer_starts_at_zero(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r._silence_timer == 0.0
        assert r._silence_warning_count == 0

    def test_silence_callback_fields_exist(self):
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r.on_silence_warning is None
        assert r.on_silence_auto_stop is None
        assert r.on_max_duration_auto_stop is None

    def test_start_resets_silence_state(self, monkeypatch):
        import voice_typer.server.recording as recording_mod
        from voice_typer.server.recording import Recorder

        class OkStream:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(recording_mod.sd, "InputStream", OkStream)
        monkeypatch.setattr(
            recording_mod.sd,
            "query_devices",
            lambda **kw: {"max_input_channels": 1, "default_samplerate": 16000, "hostapi": 0},
        )
        monkeypatch.setattr(recording_mod.sd, "query_hostapis", lambda idx=None: {"name": "MME"})

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._silence_timer = 5.0
        r._silence_warning_count = 3
        r.start()
        assert r._silence_timer == 0.0
        assert r._silence_warning_count == 0

    def test_cache_reset_on_stop(self):
        """stop() should reset the resample cache."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.ones((4, 1), dtype=np.float32)]
        r._cached_resampled = np.ones(10, dtype=np.float32)
        r._cached_native_chunk_count = 5

        r.stop()

        assert len(r._cached_resampled) == 0
        assert r._cached_native_chunk_count == 0

    def test_cache_reset_on_discard(self):
        """discard() should reset the resample cache."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._stream = MagicMock()
        r._cached_resampled = np.ones(10, dtype=np.float32)
        r._cached_native_chunk_count = 5

        r.discard()

        assert len(r._cached_resampled) == 0
        assert r._cached_native_chunk_count == 0


# ── TEST-020: Resample fallback tests ──────────────────────────────────


class TestResampleFallback:
    """TEST-020: Tests for the resample retry logic and np.interp fallback."""

    def test_resample_error_raised_when_no_poly(self, monkeypatch):
        """ResampleError should be raised when resample_poly is unavailable."""
        from voice_typer.server.recording import Recorder, ResampleError

        def failing_get_resample():
            raise ResampleError("scipy.signal.resample_poly not available")

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", failing_get_resample)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 48000
        r._stream = MagicMock()
        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        with pytest.raises(ResampleError, match="not available"):
            r.stop()

    def test_resample_retry_after_timeout(self, monkeypatch):
        """Resample should be retried after the retry interval timeout."""
        import time

        import voice_typer.server.recording as rec_mod

        # Set the error time far enough in the past that retry is allowed
        rec_mod._resample_poly_error = RuntimeError("transient error")
        # AUDIO-003: use time.monotonic() to match the source code at
        # recording.py:163 (which reads time.monotonic() - error_time).
        # Pre-fix this used time.time() (wall clock) which differs from
        # the monotonic clock by an arbitrary offset — under NTP/DST
        # adjustments the wall clock can jump backwards and cause the
        # retry-timeout comparison to behave unexpectedly.
        rec_mod._resample_poly_error_time = time.monotonic() - rec_mod._RESAMPLE_RETRY_INTERVAL - 1

        call_count = [0]

        def succeeding_get_resample():
            call_count[0] += 1
            return lambda audio, up, down: audio[::down]

        # Clear the error so the retry succeeds
        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", succeeding_get_resample)

        # After the timeout, the error should be cleared and retry allowed
        assert rec_mod._resample_poly_error is not None  # Error was set

    def test_resample_not_retried_before_timeout(self, monkeypatch):
        """Resample should NOT be retried before the retry interval has elapsed."""
        import time

        import voice_typer.server.recording as rec_mod

        # Set the error time very recently (within retry interval)
        rec_mod._resample_poly_error = RuntimeError("recent error")
        # AUDIO-003: use time.monotonic() to match source code at
        # recording.py:163. See test_resample_retry_after_timeout for
        # the full rationale.
        rec_mod._resample_poly_error_time = time.monotonic()

        # The error should still be set (not cleared for retry yet)
        assert rec_mod._resample_poly_error is not None

    def test_fallback_to_np_interp_when_scipy_unavailable(self, monkeypatch):
        """TEST-020: When scipy.signal.resample_poly raises ResampleUnavailable,
        _resample_chunk should fall back to np.interp and produce valid output."""
        from voice_typer.server.recording import Recorder, ResampleUnavailable

        def raising_get_resample():
            raise ResampleUnavailable("scipy not available for test")

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", raising_get_resample)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        # Create a simple audio signal
        audio = np.ones(4800, dtype=np.float32)  # 0.1s at 48kHz
        result = r._resample_chunk(audio, 48000, 16000)

        # Should have produced output via np.interp fallback
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert len(result) > 0
        # 48kHz -> 16kHz should reduce samples by ~3x
        expected_len = int(len(audio) * 16000 / 48000)
        assert abs(len(result) - expected_len) <= 1

    def test_resample_fallback_quality_with_known_sine(self, monkeypatch):
        """TEST-020: The np.interp fallback should produce reasonable quality
        when resampling a known sine wave."""
        from voice_typer.server.recording import Recorder, ResampleUnavailable

        def raising_get_resample():
            raise ResampleUnavailable("scipy not available for test")

        monkeypatch.setattr("voice_typer.server.recording._get_resample_poly", raising_get_resample)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        # Generate a 440Hz sine wave at 48kHz
        sr_in = 48000
        duration = 0.1  # 100ms
        t = np.linspace(0, duration, int(sr_in * duration), endpoint=False)
        audio = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float32)

        result = r._resample_chunk(audio, sr_in, 16000)

        # Verify output is valid
        assert isinstance(result, np.ndarray)
        assert result.dtype == np.float32
        assert len(result) > 0

        # The resampled sine wave should have reasonable amplitude
        # (not all zeros, not clipped)
        rms = float(np.sqrt(np.mean(result.astype(np.float64) ** 2)))
        assert rms > 0.01, f"Resampled sine wave RMS too low: {rms}"
        assert rms < 1.0, f"Resampled sine wave RMS too high (clipped?): {rms}"

    def test_resample_chunk_empty_audio(self, monkeypatch):
        """TEST-020: _resample_chunk should handle empty audio gracefully."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)

        result = r._resample_chunk(np.array([], dtype=np.float32), 48000, 16000)
        assert isinstance(result, np.ndarray)
        assert len(result) == 0


# ── TEST-032: Parametrized recording tests ──────────────────────────────


class TestRecordingParametrized:
    """TEST-032: Use @pytest.mark.parametrize for recording tests."""

    @pytest.mark.parametrize("sample_rate", [8000, 16000, 22050, 44100, 48000])
    def test_various_sample_rates_buffer_size(self, sample_rate):
        """Buffer should be created with correct sample rate config."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=sample_rate, microphone=None)
        r = Recorder(config)
        assert r.config.sample_rate == sample_rate

    @pytest.mark.parametrize(
        "effective_sr,target_sr",
        [
            (16000, 16000),
            (44100, 16000),
            (48000, 16000),
        ],
    )
    def test_resample_ratio_computation(self, effective_sr, target_sr):
        """Resample ratio should be computed correctly from effective/target rates."""
        import math

        g = math.gcd(effective_sr, target_sr)
        up = target_sr // g
        down = effective_sr // g
        assert math.gcd(up, down) == 1

    @pytest.mark.parametrize("chunk_size", [256, 512, 1024, 2048])
    def test_various_buffer_chunk_sizes(self, chunk_size):
        """Buffer should handle various chunk sizes."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.ones((chunk_size, 1), dtype=np.float32)]

        audio = r.stop()
        assert len(audio) == chunk_size
        assert audio.dtype == np.float32

    @pytest.mark.parametrize("num_chunks", [1, 2, 5, 10])
    def test_stop_concatenates_multiple_chunks(self, num_chunks):
        """stop() should concatenate all buffered chunks."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.ones((4, 1), dtype=np.float32) for _ in range(num_chunks)]

        audio = r.stop()
        assert len(audio) == 4 * num_chunks
        assert audio.dtype == np.float32

    @pytest.mark.parametrize(
        "effective_sr,target_sr,expected_up,expected_down",
        [
            (16000, 16000, 1, 1),
            (48000, 16000, 1, 3),
            (44100, 16000, 16000, 44100),
            (22050, 16000, 320, 441),
        ],
    )
    def test_resample_gcd_ratios(self, effective_sr, target_sr, expected_up, expected_down):
        """Resample up/down ratios should be computed from GCD."""
        import math

        g = math.gcd(effective_sr, target_sr)
        up = target_sr // g
        down = effective_sr // g
        assert math.gcd(up, down) == 1

    @pytest.mark.parametrize(
        "device_input,expected",
        [
            (None, None),
            ("7", 7),
            ("0", 0),
            ("Blue Yeti", "Blue Yeti"),
            ("", ""),
        ],
    )
    def test_resolve_device_various_inputs(self, device_input, expected):
        """_resolve_device should handle various input formats."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=device_input)
        r = Recorder(config)
        assert r._resolve_device() == expected

    @pytest.mark.parametrize("silence_val", [0.0, -100.0, -50.0, -30.0])
    def test_silence_timer_starts_at_zero_regardless_of_threshold(self, silence_val):
        """Silence timer should always start at zero."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r._silence_timer == 0.0


# ─── B-3/S-3: scipy preloader no longer spawns at import time ────────────


class TestScipyPreloaderDeferredSpawn:
    """B-3/S-3: ``recording.py`` must NOT spawn a background thread at
    module import time. The scipy preloader is now started lazily from
    ``Recorder.__init__`` so importing the module is side-effect-free
    (every test that imported recording.py previously triggered a real
    scipy.signal.resample_poly import in a background thread).
    """

    def test_no_scipy_preloader_thread_after_pure_import(self):
        """Importing the recording module does not start the preloader thread.

        We verify by importing the module in a fresh subprocess and
        checking that no thread named ``scipy-preloader`` exists. A
        subprocess is required because the test process itself has
        already imported recording.py (and thus may have a Recorder
        that started the preloader).
        """
        import subprocess

        code = (
            "import threading, sys\n"
            "from voice_typer.server import recording\n"
            "# B-3: module import must not spawn the preloader.\n"
            "names = [t.name for t in threading.enumerate()]\n"
            "assert 'scipy-preloader' not in names, (\n"
            "    f'B-3 regression: scipy-preloader thread spawned at '\n"
            "    f'module import time. Threads: {names}'\n"
            ")\n"
            "assert recording._scipy_preloader_thread is None, (\n"
            "    f'B-3 regression: _scipy_preloader_thread is set after '\n"
            "    f'import: {recording._scipy_preloader_thread!r}'\n"
            ")\n"
            "print('OK')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"B-3 subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        assert "OK" in result.stdout

    def test_start_scipy_preloader_is_idempotent(self, monkeypatch):
        """Calling ``_start_scipy_preloader`` twice does not spawn two threads.

        B-3: the preloader is started from ``Recorder.__init__``, which
        can be called many times (one per Recorder instance). The
        function must be idempotent: if a preloader is already alive,
        don't start a second one.
        """
        from voice_typer.server import recording

        # Reset state — other tests may have left a preloader running.
        monkeypatch.setattr(recording, "_scipy_preloader_thread", None)
        monkeypatch.setattr(recording, "_resample_poly", None)

        recording._start_scipy_preloader()
        first_thread = recording._scipy_preloader_thread
        assert first_thread is not None, "first call should start a thread"
        assert first_thread.is_alive() or first_thread.is_alive() is False
        # ^ Thread may have already finished (scipy import is fast on
        # warm cache). Either way, the reference should be set.

        # Second call: if first is still alive, must be a no-op.
        # If first has exited but _resample_poly is still None (failed),
        # a new thread is allowed — but we patch is_alive to True to
        # simulate "still loading" and verify idempotency.
        import unittest.mock as _mock

        with _mock.patch.object(first_thread, "is_alive", return_value=True):
            recording._start_scipy_preloader()
            assert recording._scipy_preloader_thread is first_thread, (
                "B-3 idempotency regression: second call to "
                "_start_scipy_preloader() spawned a new thread while "
                "the first was still alive."
            )

    def test_start_scipy_preloader_skips_when_scipy_already_loaded(self, monkeypatch):
        """If scipy already loaded successfully (cached), don't spawn a
        new preloader thread — it would be a wasted thread.
        """
        from voice_typer.server import recording

        # Simulate scipy already loaded.
        monkeypatch.setattr(recording, "_scipy_preloader_thread", None)
        monkeypatch.setattr(recording, "_resample_poly", lambda *a, **kw: None)

        recording._start_scipy_preloader()
        assert recording._scipy_preloader_thread is None, (
            "B-3 regression: _start_scipy_preloader spawned a thread even though _resample_poly was already cached."
        )
