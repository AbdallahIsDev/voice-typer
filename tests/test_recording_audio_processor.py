"""AudioProcessor attached."""

from __future__ import annotations

import sys
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
    """direct invocation from the test."""

    def __init__(self, samplerate, channels, dtype, device=None, callback=None, **kwargs):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device
        self.callback = callback
        self.started = False
        # VAD-001: blocksize is now passed by recording.py; accept it
        self.blocksize = kwargs.get("blocksize")

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.started = False

    def push_chunk(self, samples: np.ndarray) -> None:
        """Simulate PortAudio delivering one audio chunk to the callback."""
        # The callback signature is (indata, frames, time_info, status).
        frames = samples.shape[0]
        self.callback(samples, frames, None, 0)


def _capture_stream(streams):
    """Return the real capture InputStream."""
    for s in streams:
        if s.callback is not None:
            return s
    raise AssertionError("no InputStream with a callback was opened by start()")


def _drain_ring_buffer(rec, timeout_s: float = 2.0) -> None:
    """Wait for the audio worker thread to drain the SPSC ring buffer"""
    import time

    deadline = time.perf_counter() + timeout_s
    last_len: int | None = None
    stable_polls = 0
    while time.perf_counter() < deadline:
        buf_len = len(rec._audio_pipeline._buffer)
        if len(rec._ring_buffer) == 0 and buf_len == last_len:
            stable_polls += 1
            if stable_polls >= 4:  # ~4×12ms of observed quiescence
                return
        else:
            stable_polls = 0
            last_len = buf_len
        time.sleep(0.012)
    # If we get here, the worker didn't reach quiescence in time, let


def _make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    """Generate a mono sine wave reshaped to (frames, 1) for PortAudio."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32).reshape(-1, 1)


class _FilterConfig:
    """Minimal config stub for ``AudioProcessor`` (ADR-0007)."""

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


class TestRecorderCallbackWithAudioProcessor:
    """Drive the real Recorder callback with a real AudioProcessor and"""

    def test_callback_does_not_raise_with_processor(self, monkeypatch):
        """The bug: callback referenced `filtered` before assignment."""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.audio_processor import AudioProcessor
        from voice_typer.server.recording import Recorder

        captured_streams = []

        def fake_input_stream(*args, **kwargs):
            s = FakeInputStream(*args, **kwargs)
            captured_streams.append(s)
            return s

        monkeypatch.setattr(rec_mod.sd, "InputStream", fake_input_stream)

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
        # ADR-0007: AudioProcessorConfig removed; build_chain reads
        proc = AudioProcessor(
            _FilterConfig(
                highpass=True,
                highpass_cutoff_hz=80.0,
                noise_gate=False,
            ),
            sample_rate=16000,
        )
        r = Recorder(config, audio_processor=proc)

        r.start()
        stream = _capture_stream(captured_streams)

        # Push 5 chunks of 1024 samples each, this would have raised
        for _ in range(5):
            chunk = _make_sine(freq=440, duration_s=1024 / 16000, amp=0.3)
            stream.push_chunk(chunk)

        _drain_ring_buffer(r)

        # If the callback had raised, the buffer would be empty.
        assert len(r._audio_pipeline._buffer) == 5, (
            f"Expected 5 buffered chunks, got {len(r._audio_pipeline._buffer)}, "
            "callback may have raised NameError (the bug we're regression-testing)"
        )
        r.stop()

    def test_buffer_contains_filtered_audio(self, monkeypatch):
        """The stored audio should reflect the high-pass filter —"""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.audio_processor import AudioProcessor
        from voice_typer.server.recording import Recorder

        captured_streams = []

        def fake_input_stream(*args, **kwargs):
            s = FakeInputStream(*args, **kwargs)
            captured_streams.append(s)
            return s

        monkeypatch.setattr(rec_mod.sd, "InputStream", fake_input_stream)

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
        # ADR-0007: AudioProcessorConfig removed; use noise_filter_* config.
        proc = AudioProcessor(
            _FilterConfig(
                highpass=True,
                highpass_cutoff_hz=80.0,
                noise_gate=False,
            ),
            sample_rate=16000,
        )
        r = Recorder(config, audio_processor=proc)
        r.start()
        stream = _capture_stream(captured_streams)

        # Push a 0.5s chunk of 30 Hz sine (below the 80 Hz cutoff).
        raw_chunk = _make_sine(freq=30, duration_s=0.5, amp=0.5)
        raw_rms = float(np.sqrt(np.mean(raw_chunk**2)))
        stream.push_chunk(raw_chunk)

        # Stop and inspect what was buffered.
        audio = r.stop()
        filtered_rms = float(np.sqrt(np.mean(audio**2)))

        # The high-pass should have significantly attenuated 30 Hz.
        assert filtered_rms < raw_rms * 0.5, (
            f"High-pass filter not applied: raw RMS={raw_rms:.4f}, filtered RMS={filtered_rms:.4f}"
        )

    def test_rms_callback_receives_filtered_values(self, monkeypatch):
        """The on_rms_level callback should fire with values derived"""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.audio_processor import AudioProcessor
        from voice_typer.server.recording import Recorder

        captured_streams = []
        monkeypatch.setattr(
            rec_mod.sd,
            "InputStream",
            lambda *a, **kw: captured_streams.append(FakeInputStream(*a, **kw)) or captured_streams[-1],
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
        # ADR-0007: AudioProcessorConfig removed; use noise_filter_* config.
        proc = AudioProcessor(
            _FilterConfig(
                highpass=True,
                highpass_cutoff_hz=80.0,
                noise_gate=False,
            ),
            sample_rate=16000,
        )
        r = Recorder(config, audio_processor=proc)

        rms_calls = []
        # T021: callback signature now includes audio_chunk (3rd arg).
        r.on_rms_level = lambda rms, peak, *args: rms_calls.append((rms, peak))

        r.start()
        stream = _capture_stream(captured_streams)
        stream.push_chunk(_make_sine(freq=440, duration_s=0.1, amp=0.3))
        r.stop()

        assert len(rms_calls) >= 1, "on_rms_level callback never fired"
        rms, peak = rms_calls[0]
        assert 0.0 < rms < 1.0
        assert 0.0 < peak <= 1.0

    def test_quality_callback_fires_per_chunk(self, monkeypatch):
        """The AudioProcessor's quality callback should fire once per"""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.audio_processor import AudioProcessor
        from voice_typer.server.recording import Recorder

        captured_streams = []
        monkeypatch.setattr(
            rec_mod.sd,
            "InputStream",
            lambda *a, **kw: captured_streams.append(FakeInputStream(*a, **kw)) or captured_streams[-1],
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
        # ADR-0007: AudioProcessorConfig removed; use noise_filter_* config.
        proc = AudioProcessor(
            _FilterConfig(
                highpass=False,
                noise_gate=False,
            ),
            sample_rate=16000,
        )
        quality_calls = []
        proc.set_quality_callback(lambda rms, peak: quality_calls.append((rms, peak)))

        r = Recorder(config, audio_processor=proc)
        r.start()
        stream = _capture_stream(captured_streams)

        for _ in range(3):
            stream.push_chunk(_make_sine(freq=440, duration_s=0.05, amp=0.3))

        r.stop()
        assert len(quality_calls) == 3, (
            f"Quality callback should fire once per chunk; got {len(quality_calls)} calls for 3 chunks"
        )

    def test_callback_without_processor_still_works(self, monkeypatch):
        """Sanity check: the callback path must still work when"""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.recording import Recorder

        captured_streams = []
        monkeypatch.setattr(
            rec_mod.sd,
            "InputStream",
            lambda *a, **kw: captured_streams.append(FakeInputStream(*a, **kw)) or captured_streams[-1],
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

        for _ in range(3):
            stream.push_chunk(_make_sine(freq=440, duration_s=0.05, amp=0.3))

        _drain_ring_buffer(r)

        assert len(r._audio_pipeline._buffer) == 3
        r.stop()

    def test_xrun_status_does_not_break_callback(self, monkeypatch):
        """When PortAudio reports an xrun (status flag non-zero), the"""
        from voice_typer.server import recording as rec_mod
        from voice_typer.server.audio_processor import AudioProcessor
        from voice_typer.server.recording import Recorder

        captured_streams = []
        monkeypatch.setattr(
            rec_mod.sd,
            "InputStream",
            lambda *a, **kw: captured_streams.append(FakeInputStream(*a, **kw)) or captured_streams[-1],
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
        # ADR-0007: AudioProcessorConfig removed; use noise_filter_* config.
        proc = AudioProcessor(
            _FilterConfig(
                highpass=False,
                noise_gate=False,
            ),
            sample_rate=16000,
        )
        r = Recorder(config, audio_processor=proc)
        r.start()
        stream = _capture_stream(captured_streams)

        # Push a chunk WITH a non-zero status flag (simulating xrun).
        chunk = _make_sine(freq=440, duration_s=0.05, amp=0.3)
        # Manually invoke the callback with status=sd.InputStream.flags  # noqa: E501
        stream.callback(chunk, chunk.shape[0], None, "input overflow")

        _drain_ring_buffer(r)

        # Buffer should still have grown.
        assert len(r._audio_pipeline._buffer) == 1
        r.stop()
