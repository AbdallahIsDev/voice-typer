"""Regression test for AudioProcessor active must NOT cause double-resampling in stop()/snapshot()."""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    """Mock sounddevice so the test doesn't need a real audio device."""
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


class FakeInputStream:
    """Fake sounddevice.InputStream that captures the callback for direct"""

    def __init__(self, samplerate, channels, dtype, device=None, callback=None, **kwargs):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device
        self.callback = callback
        self.started = False
        self.blocksize = kwargs.get("blocksize")

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.started = False

    def push_chunk(self, samples: np.ndarray) -> None:
        """Simulate PortAudio delivering one audio chunk to the callback."""
        frames = samples.shape[0]
        self.callback(samples, frames, None, 0)


def _capture_stream(streams):
    """Return the real capture InputStream."""
    for s in streams:
        if s.callback is not None:
            return s
    raise AssertionError("no InputStream with a callback was opened by start()")


def _drain_ring_buffer(rec, timeout_s: float = 2.0) -> None:
    """Wait until the audio worker has fully processed queued chunks."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if len(rec._ring_buffer) == 0:
            break
        time.sleep(0.005)
    else:
        # Timed out with chunks still queued; caller assertions will fail
        return

    pipeline = rec._audio_pipeline
    settle_deadline = time.perf_counter() + 0.5
    while time.perf_counter() < settle_deadline:
        if pipeline._buffer_sr is not None or len(pipeline._buffer) > 0:
            return
        time.sleep(0.001)


def _make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    """Generate a mono sine wave reshaped to (frames, 1) for PortAudio."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32).reshape(-1, 1)


class _FilterConfig:
    """Minimal config stub for ``AudioProcessor`` (matches the pattern in"""

    def __init__(
        self,
        *,
        highpass: bool = True,
        highpass_cutoff_hz: float = 80.0,
        noise_gate: bool = False,
        noise_suppression: str = "none",
        eq: bool = False,
        compressor: bool = False,
        limiter: bool = False,
        notch: bool = False,
    ) -> None:
        self.noise_filter_highpass = highpass
        self.noise_filter_highpass_cutoff_hz = highpass_cutoff_hz
        self.noise_filter_gate = noise_gate
        self.noise_suppression_method = noise_suppression
        self.noise_filter_eq = eq
        self.noise_filter_compressor = compressor
        self.noise_filter_limiter = limiter
        self.noise_filter_notch = notch


def _make_recorder_with_processor(monkeypatch, *, device_native_sr: int, chain_sr: int = 16000):
    """simulated ``device_native_sr`` microphone."""
    from voice_typer.server import recording as rec_mod
    from voice_typer.server.audio_processor import AudioProcessor
    from voice_typer.server.recording import Recorder

    captured_streams: list[FakeInputStream] = []

    def fake_input_stream(*args, **kwargs):
        s = FakeInputStream(*args, **kwargs)
        captured_streams.append(s)
        return s

    monkeypatch.setattr(rec_mod.sd, "InputStream", fake_input_stream)

    monkeypatch.setattr(
        "voice_typer.server.recording.disconnect_handler.retune_audio_processor",
        lambda *a, **kw: None,
    )

    def fake_query_devices(device=None, kind=None):
        return {
            "name": "fake-mic",
            "default_samplerate": device_native_sr,
            "max_input_channels": 1,
            "hostapi": 0,
        }

    monkeypatch.setattr(rec_mod.sd, "query_devices", fake_query_devices)
    monkeypatch.setattr(
        rec_mod.sd,
        "query_hostapis",
        lambda idx: {"name": "fake-host-api"},
    )

    config = MagicMock(
        sample_rate=16000,
        microphone=None,
        silence_warning_seconds=20.0,
        stop_on_silence_seconds=120.0,
        max_recording_time_seconds=900,
        device="cpu",
        use_silero_vad=False,
        vad_speech_threshold=0.5,
    )
    proc = AudioProcessor(
        _FilterConfig(highpass=True, highpass_cutoff_hz=80.0, noise_gate=False),
        sample_rate=chain_sr,
    )
    r = Recorder(config, audio_processor=proc)
    return r, captured_streams


class TestNoDoubleResample:
    """When AudioProcessor is active, buffer_sr must equal 16000, not the"""

    def test_buffer_sr_tracks_processor_rate_when_active(self, monkeypatch):
        """_buffer_sr must be 16000 when AudioProcessor is active, even if"""
        r, captured_streams = _make_recorder_with_processor(
            monkeypatch,
            device_native_sr=48000,
            chain_sr=16000,
        )
        r.start()
        stream = _capture_stream(captured_streams)

        # The device native rate (48 kHz) should now be the effective rate.
        assert r._effective_sr == 48000, f"Expected _effective_sr=48000 (device native), got {r._effective_sr}"

        # Push one chunk of 1024 samples at 48 kHz. The processor will
        chunk_48k = _make_sine(freq=440, duration_s=1024 / 48000, sr=48000, amp=0.3)
        stream.push_chunk(chunk_48k)
        _drain_ring_buffer(r)

        # (16000), NOT the device's native rate (48000).
        assert r._audio_pipeline._buffer_sr == 16000, (
            f"Expected _buffer_sr=16000 (chain rate after process_chunk), "
            f"got {r._audio_pipeline._buffer_sr}. CR-5 regression: stop()/snapshot() will "
            f"resample a second time from {r._effective_sr} Hz."
        )

        # Sanity check: the buffer grew by exactly one chunk.
        assert len(r._audio_pipeline._buffer) == 1, f"Expected 1 buffered chunk, got {len(r._audio_pipeline._buffer)}"

        r.stop()

    def test_buffer_sr_tracks_device_rate_when_no_processor(self, monkeypatch):
        """_buffer_sr must equal _effective_sr when AudioProcessor is None."""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.recording import Recorder

        captured_streams: list[FakeInputStream] = []

        def fake_input_stream(*args, **kwargs):
            s = FakeInputStream(*args, **kwargs)
            captured_streams.append(s)
            return s

        monkeypatch.setattr(rec_mod.sd, "InputStream", fake_input_stream)
        # Simulate a 48 kHz device.
        monkeypatch.setattr(
            rec_mod.sd,
            "query_devices",
            lambda device=None, kind=None: {
                "name": "fake-mic",
                "default_samplerate": 48000,
                "max_input_channels": 1,
                "hostapi": 0,
            },
        )
        monkeypatch.setattr(
            rec_mod.sd,
            "query_hostapis",
            lambda idx: {"name": "fake-host-api"},
        )

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
            max_recording_time_seconds=900,
            device="cpu",
            use_silero_vad=False,
            vad_speech_threshold=0.5,
        )
        r = Recorder(config, audio_processor=None)
        r.start()
        stream = _capture_stream(captured_streams)

        assert r._effective_sr == 48000

        chunk_48k = _make_sine(freq=440, duration_s=1024 / 48000, sr=48000, amp=0.3)
        stream.push_chunk(chunk_48k)
        _drain_ring_buffer(r)

        assert r._audio_pipeline._buffer_sr == 48000, (
            f"Expected _buffer_sr=48000 (device native rate when no processor), got {r._audio_pipeline._buffer_sr}"
        )

        # Sanity check: the buffered chunk should be at 48 kHz (no
        assert len(r._audio_pipeline._buffer) == 1
        buffered = r._audio_pipeline._buffer[0]
        assert buffered.size == 1024, f"Expected 1024 samples (48 kHz, no resampling), got {buffered.size}"

        r.stop()

    def test_stop_uses_buffer_sr_not_effective_sr(self, monkeypatch):
        """stop() must pass _buffer_sr to _prepare_audio, not _effective_sr."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
            max_recording_time_seconds=900,
            device="cpu",
            use_silero_vad=False,
            vad_speech_threshold=0.5,
        )
        r = Recorder(config, audio_processor=None)

        # Simulate the post-process_chunk state: buffer holds 16 kHz audio
        r._effective_sr = 48000
        r._audio_pipeline._buffer_sr = 16000
        # Pretend recording is active so stop() doesn't early-return.
        r._recording_event.set()
        # Put a chunk in the buffer so stop()'s concat path runs.
        r._audio_pipeline._buffer.append(np.zeros(1024, dtype=np.float32))

        # Mock the teardown helpers so stop() doesn't try to stop real
        r._teardown_stream = MagicMock()
        r._stop_audio_worker = MagicMock()
        r._capture.stop_event_worker_body = MagicMock()
        r._stop_device_health_checker = MagicMock()

        captured_sr: list[int] = []
        import voice_typer.server.recording.recording_lifecycle as split_mod
        from voice_typer.server.recording.format import prepare_audio as real_prepare

        def spy_prepare(rec, audio, effective_sr, **kw):
            captured_sr.append(int(effective_sr))
            return real_prepare(rec, audio, effective_sr, **kw)

        monkeypatch.setattr(split_mod, "prepare_audio", spy_prepare)

        r.stop()

        assert len(captured_sr) == 1, f"Expected _prepare_audio to be called once, got {len(captured_sr)} calls"
        assert captured_sr[0] == 16000, (
            f"Expected _prepare_audio to receive effective_sr=16000 "
            f"(_buffer_sr), got {captured_sr[0]}. CR-5 regression: "
            f"stop() is passing _effective_sr (48000) instead of "
            f"_buffer_sr (16000), causing a double-resample."
        )

    def test_snapshot_uses_buffer_sr_not_effective_sr(self, monkeypatch):
        """snapshot() must use _buffer_sr, not _effective_sr, when"""
        from voice_typer.server.recording import Recorder

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
            max_recording_time_seconds=900,
            device="cpu",
            use_silero_vad=False,
            vad_speech_threshold=0.5,
        )
        r = Recorder(config, audio_processor=None)

        # Simulate the post-process_chunk state: buffer holds 16 kHz audio
        r._effective_sr = 48000
        r._audio_pipeline._buffer_sr = 16000
        # Put a chunk in the buffer (1024 samples of 16 kHz audio).
        r._audio_pipeline._buffer.append(np.zeros(1024, dtype=np.float32))

        # removed), it must NOT be called when
        resample_calls: list[tuple[int, int]] = []
        import voice_typer.server.recording.recording_snapshot as split_mod
        from voice_typer.server.recording.format import resample_chunk as real_resample

        def spy_resample(rec, audio, effective_sr, target_sr):
            resample_calls.append((int(effective_sr), int(target_sr)))
            return real_resample(rec, audio, effective_sr, target_sr)

        monkeypatch.setattr(split_mod, "resample_chunk", spy_resample)

        audio = r.snapshot()

        assert resample_calls == [], (
            f"Expected _resample_chunk NOT to be called (buffer_sr=16000 "
            f"== target_sr=16000), but it was called with: "
            f"{resample_calls}. CR-5 regression: snapshot() is "
            f"resampling a second time from _effective_sr (48000)."
        )

        # And the returned audio must be the full 1024 samples, not
        assert audio.size == 1024, (
            f"Expected 1024 samples (no resampling), got {audio.size}. "
            "CR-5 regression: snapshot() shrank the audio by resampling "
            "from _effective_sr (48000) instead of _buffer_sr (16000)."
        )

    def test_buffer_sr_resets_on_stop_and_discard(self, monkeypatch):
        """subsequent start() cycle doesn't reuse the stale rate."""
        from voice_typer.server.recording import Recorder

        config = MagicMock(
            sample_rate=16000,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
            max_recording_time_seconds=900,
            device="cpu",
            use_silero_vad=False,
            vad_speech_threshold=0.5,
        )
        r = Recorder(config, audio_processor=None)

        # Pre-state: a previous session set _buffer_sr.
        r._effective_sr = 48000
        r._audio_pipeline._buffer_sr = 16000
        r._recording_event.set()
        r._audio_pipeline._buffer.append(np.zeros(1024, dtype=np.float32))

        # Mock the teardown helpers.
        r._teardown_stream = MagicMock()
        r._stop_audio_worker = MagicMock()
        r._capture.stop_event_worker_body = MagicMock()
        r._stop_device_health_checker = MagicMock()

        r.stop()
        assert r._audio_pipeline._buffer_sr is None, (
            f"Expected _buffer_sr=None after stop(), got {r._audio_pipeline._buffer_sr}"
        )

        r._audio_pipeline._buffer_sr = 16000  # simulate a subsequent session
        r._recording_event.set()
        r.discard()
        assert r._audio_pipeline._buffer_sr is None, (
            f"Expected _buffer_sr=None after discard(), got {r._audio_pipeline._buffer_sr}"
        )
