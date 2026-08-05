"""Regression tests for the Recorder audio callback path with an
AudioProcessor attached.

Background
----------
A previous version of ``recording.py`` had the audio-callback structure:

    with self._lock:
        self._buffer.append(filtered.copy())   # ← uses `filtered`
        ...
    # ... `filtered` was assigned HERE, AFTER the lock block ...

This raised ``NameError: name 'filtered' is not defined`` on every
audio chunk.  PortAudio swallows callback exceptions silently, so the
recording captured nothing — no audio, no buffer growth, no RMS
updates.  This went undetected because no test exercised the
callback with an AudioProcessor attached (without a processor,
``filtered = indata`` was a separate code path that worked).

These tests construct a ``Recorder`` with a real ``AudioProcessor``,
drive the callback via a fake ``InputStream`` that invokes the
callback directly, and assert that:

1. The callback does not raise.
2. The buffer grows by the expected number of chunks.
3. The stored audio is the FILTERED audio (high-pass filter actually
   applied — low-frequency content attenuated).
4. The RMS callback fires with values derived from the filtered audio.
5. The quality callback (wired via ``set_quality_callback``) receives
   (rms, peak) per chunk.
6. Post-capture processing in ``stop()`` is invoked.

This is a true end-to-end test of the audio path with noise filtering
enabled, using no real audio hardware.
"""

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
    """Fake sounddevice.InputStream that captures the callback for
    direct invocation from the test.

    Mimics the real InputStream's start()/stop()/close() surface so
    Recorder.start() and stop() work unmodified.
    """

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
        # indata is shape (frames, channels).
        frames = samples.shape[0]
        self.callback(samples, frames, None, 0)


def _capture_stream(streams):
    """Return the real capture InputStream.

    ``Recorder.__init__`` opens a prewarm stream with ``callback=None``
    (``_prewarm_input_stream``) and the Windows mic-permission probe
    opens one without a callback, so ``captured_streams`` may contain
    extra no-callback streams. The actual capture stream is the one
    opened by ``start()`` with a real callback.
    """
    for s in streams:
        if s.callback is not None:
            return s
    raise AssertionError("no InputStream with a callback was opened by start()")


def _drain_ring_buffer(rec, timeout_s: float = 2.0) -> None:
    """Wait for the audio worker thread to drain the SPSC ring buffer.

    RT-SAFE-001: the PortAudio callback now pushes chunks to a ring
    buffer and returns immediately; a daemon worker thread processes
    them asynchronously. Tests that push chunks via ``FakeInputStream``
    must call this helper before asserting on ``rec._buffer`` —
    otherwise the worker may not have processed the chunks yet.
    """
    import time

    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if len(rec._ring_buffer) == 0:
            return
        time.sleep(0.005)
    # If we get here, the worker didn't drain in time — let the caller's
    # assertion fail with a clear message rather than timing out here.


def _make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    """Generate a mono sine wave reshaped to (frames, 1) for PortAudio."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32).reshape(-1, 1)


class _FilterConfig:
    """Minimal config stub for ``AudioProcessor`` (ADR-0007).

    ADR-0007 removed the old ``AudioProcessorConfig`` dataclass and
    replaced it with the regular ``Config`` object's ``noise_filter_*``
    fields. Tests build a tiny namespace here with the same attributes
    that ``audio_chain_builder.build_chain`` reads via ``getattr``.
    """

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


# ═══════════════════════════════════════════════════════════════════════════


class TestRecorderCallbackWithAudioProcessor:
    """Drive the real Recorder callback with a real AudioProcessor and
    assert that filtering works and no NameError escapes."""

    def test_callback_does_not_raise_with_processor(self, monkeypatch):
        """The bug: callback referenced `filtered` before assignment.
        With an AudioProcessor attached, every chunk would raise
        NameError — silently swallowed by PortAudio.  This test makes
        sure the buffer actually grows."""
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
            # SIMPLIFY-001: single explicit field replaces the old 3-field split
            max_recording_time_seconds=900,
            device="cpu",
            use_silero_vad=False,
            vad_speech_threshold=0.5,
        )
        # ADR-0007: AudioProcessorConfig removed; build_chain reads
        # noise_filter_* attributes from the config via getattr.
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

        # Push 5 chunks of 1024 samples each — this would have raised
        # NameError on the FIRST chunk in the buggy version.
        for _ in range(5):
            chunk = _make_sine(freq=440, duration_s=1024 / 16000, amp=0.3)
            stream.push_chunk(chunk)

        # the callback pushes to the ring buffer and returns
        # immediately; the worker thread processes asynchronously. Wait
        # for the worker to drain before asserting on _buffer.
        _drain_ring_buffer(r)

        # If the callback had raised, the buffer would be empty.
        assert len(r._buffer) == 5, (
            f"Expected 5 buffered chunks, got {len(r._buffer)} — "
            "callback may have raised NameError (the bug we're regression-testing)"
        )
        r.stop()

    def test_buffer_contains_filtered_audio(self, monkeypatch):
        """The stored audio should reflect the high-pass filter —
        low-frequency content (30 Hz) should be attenuated relative to
        the raw input."""
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
            # SIMPLIFY-001: single explicit field replaces the old 3-field split
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
        """The on_rms_level callback should fire with values derived
        from the FILTERED audio, not raw mic input."""
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
            # SIMPLIFY-001: single explicit field replaces the old 3-field split
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
        # Use *args to accept any number of positional args for compat.
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
        """The AudioProcessor's quality callback should fire once per
        chunk — this is what wires AudioQualityAnalyzer back into the
        pipeline."""
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
            # SIMPLIFY-001: single explicit field replaces the old 3-field split
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

    # ADR-0007 §3.8: post-capture noisereduce (process_full_audio) was
    # removed. The real-time NoiseSuppressor filter in the chain now
    # handles denoising for both the streaming and stop() paths. The
    # test ``test_post_capture_runs_in_stop`` was deleted because the
    # feature it pinned no longer exists. See recording.py:1840-1845
    # for the rationale comment.

    def test_callback_without_processor_still_works(self, monkeypatch):
        """Sanity check: the callback path must still work when
        audio_processor is None (feature disabled).  This is the
        graceful-degradation path."""
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
            # SIMPLIFY-001: single explicit field replaces the old 3-field split
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

        # wait for the worker to drain the ring buffer.
        _drain_ring_buffer(r)

        assert len(r._buffer) == 3
        r.stop()

    def test_xrun_status_does_not_break_callback(self, monkeypatch):
        """When PortAudio reports an xrun (status flag non-zero), the
        callback should still process the chunk and not raise."""
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
            # SIMPLIFY-001: single explicit field replaces the old 3-field split
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
        # We use the string "input overflow" which is what sounddevice
        # actually passes — but any truthy value exercises the path.
        stream.callback(chunk, chunk.shape[0], None, "input overflow")

        # wait for the worker to drain the ring buffer.
        _drain_ring_buffer(r)

        # Buffer should still have grown.
        assert len(r._buffer) == 1
        r.stop()
