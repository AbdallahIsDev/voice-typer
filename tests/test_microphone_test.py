"""Unit tests for voice_typer.server.microphone_test.

Tests cover:
- start_test / stop_test / cancel_test lifecycle
- Module-level state reset between tests
- WAV encoding and base64 output correctness
- Edge cases: already running, no test running, sample rate fallback
- Audio chunk concatenation and WAV format validation

The underlying stream is managed by level_monitor.py (single stream for
both monitoring and test recording).  These tests verify the facade
layer and the level_monitor integration.

sounddevice is mocked by the autouse fixture in conftest.py.
"""

import base64
import io
import wave

import numpy as np
import pytest


# ── Helpers ──────────────────────────────────────────────────────────

def _reset_module_state():
    """Reset module-level state in level_monitor between tests.

    Since both modules use global variables and each test mutates them,
    we must reset them before each test to avoid cross-test contamination.
    """
    import voice_typer.server.level_monitor as lm
    lm._test_mode = False
    lm._test_chunks = []
    lm._test_raw_chunks = []
    lm._test_start_time = 0.0
    lm._test_duration = 10.0
    lm._monitor_sample_rate = 16000
    lm._monitor_active = False
    lm._monitor_stream = None
    lm._monitor_level = 0.0
    lm._monitor_peak = 0.0


def _push_test_chunk(chunk: np.ndarray) -> None:
    """Append an audio chunk to BOTH _test_chunks and _test_raw_chunks.

    level_monitor's stop_test() concatenates both lists in lockstep
    (see level_monitor.py:243-244). Tests that push chunks directly
    must mirror them into the raw list or the concatenation step
    raises ``ValueError: need at least one array to concatenate``.
    """
    import voice_typer.server.level_monitor as lm
    lm._test_chunks.append(chunk)
    lm._test_raw_chunks.append(chunk.copy())


def _decode_wav(audio_b64: str) -> tuple[int, int, np.ndarray]:
    """Decode a base64 WAV string and return (sample_rate, sampwidth, audio_int16)."""
    wav_bytes = base64.b64decode(audio_b64)
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as wf:
        sr = wf.getframerate()
        sw = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16)
    return sr, sw, audio


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    """Reset module state before every test."""
    _reset_module_state()


# ── Tests ────────────────────────────────────────────────────────────

class TestIsTestActive:
    """is_test_active() lifecycle checks."""

    def test_initial_state_is_false(self):
        """Before any start call, no test should be active."""
        from voice_typer.server.microphone_test import is_test_active
        assert is_test_active() is False

    def test_returns_false_after_cancel(self, monkeypatch):
        """After cancel_test, is_test_active must return False."""
        from voice_typer.server.microphone_test import start_test, cancel_test, is_test_active
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        result = start_test(mic_id=None, duration=5.0)
        assert result["success"] is True
        assert is_test_active() is True

        cancel_test()
        assert is_test_active() is False

    def test_returns_false_after_stop(self, monkeypatch):
        """After stop_test, is_test_active must return False."""
        from voice_typer.server.microphone_test import start_test, stop_test, is_test_active
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=5.0)
        assert is_test_active() is True

        # Simulate audio chunks arriving via callback
        lm._test_chunks.append(np.ones((512, 1), dtype=np.float32) * 0.25)

        stop_test()
        assert is_test_active() is False


class TestStartTest:
    """start_test() behavior."""

    def test_start_returns_success_with_defaults(self, monkeypatch):
        """A fresh start_test must return success and set test state."""
        from voice_typer.server.microphone_test import start_test, is_test_active
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        result = start_test()
        assert result["success"] is True
        assert result["message"] == "Recording test..."
        assert result["duration"] == 10.0
        assert result["sample_rate"] == 16000
        assert is_test_active() is True

    def test_start_with_custom_duration(self, monkeypatch):
        """Duration parameter must be respected and clamped."""
        from voice_typer.server.microphone_test import start_test, cancel_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        result = start_test(duration=3.0)
        assert result["success"] is True
        assert result["duration"] == 3.0
        cancel_test()

        # Duration must be clamped to [1, 30]
        _wire_working_stream(monkeypatch)
        result2 = start_test(duration=0.5)
        assert result2["success"] is True
        assert result2["duration"] == 1.0
        cancel_test()

        # Test max clamp
        _wire_working_stream(monkeypatch)
        result3 = start_test(duration=60.0)
        assert result3["success"] is True
        assert result3["duration"] == 30.0

    def test_start_rejects_duplicate(self, monkeypatch):
        """Starting a test while one is already running must return error."""
        from voice_typer.server.microphone_test import start_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        first = start_test()
        assert first["success"] is True

        second = start_test()
        assert second["success"] is False
        assert "already running" in second["message"].lower()

    def test_start_with_mic_id(self, monkeypatch):
        """Passing a mic_id must resolve to an int and pass to InputStream."""
        from voice_typer.server.microphone_test import start_test
        import voice_typer.server.level_monitor as lm

        opened_devices = []
        _wire_working_stream(monkeypatch, opened_devices)

        start_test(mic_id="3", duration=5.0)
        # The InputStream should have been called with device=3
        assert 3 in opened_devices

    def test_start_handles_stream_failure(self, monkeypatch):
        """If sd.InputStream raises, start_test must return error."""
        from voice_typer.server.microphone_test import start_test, is_test_active
        import voice_typer.server.level_monitor as lm
        import sounddevice as sd

        # Make InputStream raise on construction
        sd.InputStream.side_effect = RuntimeError("No device")

        result = start_test()
        assert result["success"] is False
        assert "No device" in result["message"]
        assert is_test_active() is False


class TestStopTest:
    """stop_test() behavior."""

    def test_stop_when_not_running_returns_error(self):
        """Calling stop_test with no active test and no chunks must return error."""
        from voice_typer.server.microphone_test import stop_test

        result = stop_test()
        assert result["success"] is False
        assert result["audio_base64"] == ""
        assert "No test running" in result["message"]

    def test_stop_returns_chunks_even_after_auto_stop(self, monkeypatch):
        """If auto-stop fired (test not active) but chunks exist, stop_test must return them."""
        from voice_typer.server.microphone_test import stop_test
        import voice_typer.server.level_monitor as lm

        # Simulate auto-stop: test inactive, chunks in buffer
        lm._test_mode = False
        lm._test_start_time = 1000.0
        lm._monitor_sample_rate = 16000
        for _ in range(3):
            _push_test_chunk(np.ones((512, 1), dtype=np.float32) * 0.25)

        result = stop_test()
        assert result["success"] is True
        assert result["audio_base64"] != ""
        assert result["duration_ms"] > 0
        assert "No test running" not in result["message"]

    def test_stop_returns_encoded_wav(self, monkeypatch):
        """After a successful recording, stop_test must return a valid WAV."""
        from voice_typer.server.microphone_test import start_test, stop_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)

        # Simulate audio chunks arriving via callback
        for _ in range(5):
            chunk = np.ones((1024, 1), dtype=np.float32) * 0.25
            _push_test_chunk(chunk)

        result = stop_test()
        assert result["success"] is True
        assert result["audio_base64"] != ""
        assert result["duration_ms"] > 0
        assert result["sample_rate"] == 16000

        # Verify WAV integrity
        sr, sampwidth, audio = _decode_wav(result["audio_base64"])
        assert sr == 16000
        assert sampwidth == 2  # 16-bit
        assert len(audio) > 0

    def test_stop_clears_module_state(self, monkeypatch):
        """After stop_test, module state must be reset for next recording."""
        from voice_typer.server.microphone_test import start_test, stop_test, is_test_active
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)

        # Populate some audio chunks
        for _ in range(3):
            lm._test_chunks.append(np.ones((512, 1), dtype=np.float32))

        stop_test()
        assert is_test_active() is False
        assert len(lm._test_chunks) == 0
        assert lm._test_mode is False

    def test_stop_handles_empty_chunks(self, monkeypatch):
        """If no audio chunks were captured, stop must return empty audio."""
        from voice_typer.server.microphone_test import start_test, stop_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)
        # Don't add any chunks — simulate silence
        result = stop_test()
        assert result["success"] is True
        assert result["audio_base64"] == ""
        assert result["duration_ms"] == 0
        assert "No audio captured" in result["message"]

    def test_stream_stays_open_after_test(self, monkeypatch):
        """The shared monitor stream must NOT be closed when a test ends."""
        from voice_typer.server.microphone_test import start_test, stop_test
        import voice_typer.server.level_monitor as lm

        mock_stream = _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)
        stop_test()

        # The stream is shared with the level monitor — it stays open
        # for continuous monitoring after the test ends.
        mock_stream.stop.assert_not_called()
        mock_stream.close.assert_not_called()


class TestCancelTest:
    """cancel_test() behavior."""

    def test_cancel_when_not_running_returns_ok(self):
        """Cancelling when no test is running must return success."""
        from voice_typer.server.microphone_test import cancel_test

        result = cancel_test()
        assert result["success"] is True
        assert "No test running" in result["message"]

    def test_cancel_clears_state(self, monkeypatch):
        """Cancelling a running test must reset module state."""
        from voice_typer.server.microphone_test import start_test, cancel_test, is_test_active
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=5.0)
        assert is_test_active() is True

        cancel_test()
        assert is_test_active() is False
        assert lm._test_mode is False
        assert len(lm._test_chunks) == 0

    def test_cancel_does_not_return_audio(self, monkeypatch):
        """Cancelling a test must NOT return audio data."""
        from voice_typer.server.microphone_test import start_test, cancel_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=5.0)
        # Add some chunks to verify they're discarded
        lm._test_chunks.append(np.ones((512, 1), dtype=np.float32))
        result = cancel_test()
        # Cancel returns a simple message, no audio data
        assert result.get("audio_base64", "") == ""


class TestWavEncoding:
    """WAV encoding correctness."""

    def test_wav_is_valid_format(self, monkeypatch):
        """The base64 WAV must decode to a valid audio file with correct properties."""
        from voice_typer.server.microphone_test import start_test, stop_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)

        # Add test audio chunks AFTER start_test so they aren't cleared
        for _ in range(3):
            _push_test_chunk(np.ones((512, 1), dtype=np.float32) * 0.25)

        result = stop_test()

        audio_b64 = result["audio_base64"]
        sr, sampwidth, audio = _decode_wav(audio_b64)

        # WAV properties
        assert sr == 16000, f"Expected 16000 Hz, got {sr}"
        assert sampwidth == 2, f"Expected 16-bit (2 bytes), got {sampwidth}"
        assert audio.dtype == np.int16, f"Expected int16, got {audio.dtype}"
        assert len(audio) > 0, "WAV audio data must not be empty"

    def test_wav_amplitude_range(self, monkeypatch):
        """The int16 audio must be in [-32768, 32767] range."""
        from voice_typer.server.microphone_test import start_test, stop_test
        import voice_typer.server.level_monitor as lm

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)

        for _ in range(3):
            _push_test_chunk(np.ones((512, 1), dtype=np.float32) * 0.25)

        result = stop_test()

        _, _, audio = _decode_wav(result["audio_base64"])
        assert np.all(audio >= -32768), "Audio underflows int16"
        assert np.all(audio <= 32767), "Audio overflows int16"

    def test_wav_duration_matches_chunks(self, monkeypatch):
        """The reported duration_ms must match the chunk data."""
        from voice_typer.server.microphone_test import start_test, stop_test
        import voice_typer.server.level_monitor as lm

        chunk_samples = 1024
        num_chunks = 5
        sr = 16000
        expected_duration_ms = int(chunk_samples * num_chunks / sr * 1000)

        _wire_working_stream(monkeypatch)

        start_test(mic_id=None, duration=2.0)

        for _ in range(num_chunks):
            _push_test_chunk(np.ones((chunk_samples, 1), dtype=np.float32) * 0.25)

        result = stop_test()

        assert result["duration_ms"] == expected_duration_ms, (
            f"Expected {expected_duration_ms}ms, got {result['duration_ms']}ms"
        )


class TestGetLevel:
    """get_level() real-time audio level tracking."""

    def test_get_level_returns_defaults_when_idle(self):
        """Without an active test, get_level must return 0 level and active=False."""
        from voice_typer.server.microphone_test import get_level

        result = get_level()
        assert result["level"] == 0.0
        assert result["peak"] == 0.0
        assert result["active"] is False

    def test_get_level_returns_active_during_test(self, monkeypatch):
        """During a running test, get_level must return active=True."""
        from voice_typer.server.microphone_test import start_test, get_level
        import voice_typer.server.level_monitor as lm
        from unittest.mock import MagicMock
        import sounddevice as sd

        mock_stream = MagicMock()
        sd.InputStream.return_value = mock_stream

        start_test(mic_id=None, duration=5.0)
        result = get_level()
        # Level monitor's active flag reflects whether it's monitoring
        assert result["active"] is True

        # Cleanup
        lm._test_mode = False
        lm._test_chunks = []

    def test_get_level_reflects_recent_level(self, monkeypatch):
        """get_level must reflect the monitor's current level."""
        from voice_typer.server.microphone_test import get_level
        import voice_typer.server.level_monitor as lm

        # Manually set level values (simulating callback)
        lm._monitor_active = True
        lm._monitor_level = 0.05
        lm._monitor_peak = 0.12

        result = get_level()
        assert result["level"] > 0
        assert result["peak"] == 0.12
        assert result["active"] is True

        lm._monitor_active = False


class TestSampleRateFallback:
    """Sample rate detection fallback."""

    def test_fallback_to_16k_when_query_fails(self, monkeypatch):
        """If sd.query_devices raises, sample rate must fall back to 16000."""
        from voice_typer.server.microphone_test import start_test, is_test_active
        import voice_typer.server.level_monitor as lm
        import sounddevice as sd

        # Make query_devices raise
        sd.query_devices.side_effect = RuntimeError("query failed")
        mock_stream = MagicMock()
        sd.InputStream.return_value = mock_stream

        result = start_test(mic_id=None, duration=2.0)
        assert result["success"] is True
        assert result["sample_rate"] == 16000, "Must fall back to 16000 Hz"

    def test_fallback_uses_native_rate_when_available(self, monkeypatch):
        """When query_devices succeeds, the device's native rate must be used."""
        from voice_typer.server.microphone_test import start_test
        import voice_typer.server.level_monitor as lm
        import sounddevice as sd

        # Return a device with 44100 Hz native rate
        sd.query_devices.return_value = {
            "name": "Test Mic",
            "default_samplerate": 44100,
            "max_input_channels": 1,
            "hostapi": 0,
        }

        mock_stream = MagicMock()
        sd.InputStream.return_value = mock_stream

        result = start_test(mic_id=None, duration=2.0)
        assert result["success"] is True
        assert result["sample_rate"] == 44100


# ── Mock helpers ─────────────────────────────────────────────────────

from unittest.mock import MagicMock


def _wire_working_stream(monkeypatch, opened_devices=None):
    """Set up a working sounddevice InputStream mock.

    Replaces sounddevice's InputStream with a mock so level_monitor
    can open a stream without actual hardware.  Test audio chunks are
    populated manually AFTER start_test() in each test (since start_test
    clears chunks).
    """
    import sounddevice as sd

    mock_stream = MagicMock()

    def input_stream_init(*args, **kwargs):
        if opened_devices is not None:
            opened_devices.append(kwargs.get("device"))
        return mock_stream

    sd.InputStream.side_effect = input_stream_init
    sd.query_devices.return_value = {
        "name": "Mock Mic",
        "default_samplerate": 16000,
        "max_input_channels": 1,
        "hostapi": 0,
    }

    return mock_stream
