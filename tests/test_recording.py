"""Tests for recording module — device resolution."""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch

# Mock sounddevice at module level
@pytest.fixture(autouse=True)
def mock_sounddevice(monkeypatch):
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)

import sys


class TestResolveDevice:
    def test_none_config_returns_none(self):
        from voice_typer.recording import Recorder
        config = MagicMock()
        config.microphone = None
        config.sample_rate = 16000
        r = Recorder(config)
        assert r._resolve_device() is None

    def test_string_index_converts_to_int(self):
        from voice_typer.recording import Recorder
        config = MagicMock()
        config.microphone = "7"
        config.sample_rate = 16000
        r = Recorder(config)
        assert r._resolve_device() == 7

    def test_legacy_name_string_passes_through(self):
        """If someone put a device name (not numeric), pass it as-is."""
        from voice_typer.recording import Recorder
        config = MagicMock()
        config.microphone = "Blue Yeti"
        config.sample_rate = 16000
        r = Recorder(config)
        assert r._resolve_device() == "Blue Yeti"


class TestStopAudioPrep:
    def test_stop_concatenates_chunks_to_1d_and_clears_buffer(self, monkeypatch):
        from voice_typer.recording import Recorder

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
        assert r._buffer == []
        assert r._stream is None

    def test_stop_resamples_when_effective_rate_differs(self, monkeypatch):
        from voice_typer.recording import Recorder

        calls = []

        def fake_resample_poly(audio, up, down):
            calls.append((up, down))
            return np.array([0.25, 0.5], dtype=np.float32)

        monkeypatch.setattr("voice_typer.recording._get_resample_poly", lambda: fake_resample_poly)

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
        from voice_typer.recording import Recorder

        get_resampler = MagicMock()
        monkeypatch.setattr("voice_typer.recording._get_resample_poly", get_resampler)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._stream = MagicMock()
        r._buffer = [np.ones((4, 1), dtype=np.float32)]

        r.stop()

        get_resampler.assert_not_called()

    def test_start_failure_resets_recording_state(self, monkeypatch):
        from voice_typer.recording import Recorder
        import voice_typer.recording as recording_mod

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
        from voice_typer.recording import Recorder
        import voice_typer.recording as recording_mod

        devices = [
            {"index": 0, "name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 0},
            {"index": 1, "name": "Microphone (WO Mic Device)", "max_input_channels": 1, "default_samplerate": 44100, "hostapi": 0},
            {"index": 8, "name": "Primary Sound Capture Driver", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 1},
            {"index": 9, "name": "Microphone (WO Mic Device)", "max_input_channels": 1, "default_samplerate": 44100, "hostapi": 1},
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
        from voice_typer.recording import Recorder
        import voice_typer.recording as recording_mod

        devices = [
            {"index": 0, "name": "Microsoft Sound Mapper - Input", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 0},
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
        from voice_typer.recording import Recorder

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
        from voice_typer.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 16000
        r._buffer = []

        snapshot = r.snapshot()

        assert snapshot.dtype == np.float32
        assert snapshot.size == 0

    def test_snapshot_uses_same_resampling_path_as_stop(self, monkeypatch):
        from voice_typer.recording import Recorder

        calls = []

        def fake_resample_poly(audio, up, down):
            calls.append((audio.copy(), up, down))
            return np.array([0.25, 0.5], dtype=np.float32)

        monkeypatch.setattr("voice_typer.recording._get_resample_poly", lambda: fake_resample_poly)

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
        from voice_typer.recording import Recorder

        def fake_resample_poly(audio, up, down):
            return np.array([0.25, 0.5], dtype=np.float32)

        monkeypatch.setattr("voice_typer.recording._get_resample_poly", lambda: fake_resample_poly)

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._effective_sr = 48000
        r._buffer = [np.ones((6, 1), dtype=np.float32)]

        with caplog.at_level("INFO"):
            r.snapshot()

        assert "Resampled 48000 Hz -> 16000 Hz" not in caplog.text


class TestH15CachedResampling:
    """H15/M8: snapshot() triggers full resample on every call."""

    def test_snapshot_uses_cached_resampled_prefix(self, monkeypatch):
        """Second snapshot should only resample new chunks, not all."""
        from voice_typer.recording import Recorder

        call_count = [0]

        def fake_resample_poly(audio, up, down):
            call_count[0] += 1
            # Simple decimation for testing
            return audio[::down][:len(audio) * up // down]

        monkeypatch.setattr("voice_typer.recording._get_resample_poly", lambda: fake_resample_poly)

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
        from voice_typer.recording import Recorder

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
        from voice_typer.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r._silence_timer == 0.0
        assert r._silence_warning_count == 0

    def test_silence_callback_fields_exist(self):
        from voice_typer.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        assert r.on_silence_warning is None
        assert r.on_silence_auto_stop is None
        assert r.on_max_duration_auto_stop is None

    def test_start_resets_silence_state(self, monkeypatch):
        from voice_typer.recording import Recorder
        import voice_typer.recording as recording_mod

        class OkStream:
            def __init__(self, *args, **kwargs): pass
            def start(self): pass
            def close(self): pass

        monkeypatch.setattr(recording_mod.sd, "InputStream", OkStream)
        monkeypatch.setattr(recording_mod.sd, "query_devices", lambda **kw: {"max_input_channels": 1, "default_samplerate": 16000, "hostapi": 0})
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
        from voice_typer.recording import Recorder

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
        from voice_typer.recording import Recorder

        config = MagicMock(sample_rate=16000, microphone=None)
        r = Recorder(config)
        r._recording_event.set()
        r._stream = MagicMock()
        r._cached_resampled = np.ones(10, dtype=np.float32)
        r._cached_native_chunk_count = 5

        r.discard()

        assert len(r._cached_resampled) == 0
        assert r._cached_native_chunk_count == 0
