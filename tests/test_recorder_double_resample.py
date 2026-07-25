"""Regression test for CR-5: AudioProcessor active must NOT cause double-resampling in stop()/snapshot().

Pre-fix bug (verified by code-flow in review.md):
  1. ``AudioProcessor.process_chunk`` resamples each chunk from the device's
     native rate (e.g. 48 kHz) to the filter chain's construction rate
     (typically 16 kHz) BEFORE appending to the buffer.
  2. ``Recorder.stop()`` / ``snapshot()`` then concatenated the buffer and
     called ``_prepare_audio`` / ``_resample_chunk`` with
     ``effective_sr=self._effective_sr`` (the device's 48 kHz native rate).
  3. The resampler saw 16 kHz audio mislabeled as 48 kHz and produced a
     3×-too-short garbage array — every dictation on non-16 kHz mics was
     unusable.

Post-fix:
  - ``Recorder`` tracks ``self._buffer_sr``: the actual sample rate of the
    audio currently in ``_buffer`` (16 kHz when a processor is attached,
    native rate otherwise).
  - ``stop()`` and ``snapshot()`` use ``self._buffer_sr or self._effective_sr``
    instead of ``self._effective_sr`` — so when the processor already
    resampled to 16 kHz, no second resample happens.

These tests construct a ``Recorder`` with mocked deps, drive the audio
callback path (or directly set state for the call-site tests), and assert
the resample call sites receive the correct rate.
"""

from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    """Mock sounddevice so the test doesn't need a real audio device.

    Overrides the project-wide ``mock_heavy_imports`` fixture in
    ``tests/conftest.py`` so we can stub ``query_devices`` per-test to
    simulate a 48 kHz native-rate microphone (the CR-5 failure scenario).
    """
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)


# ─── Helpers shared with tests/test_recording_audio_processor.py ─────────


class FakeInputStream:
    """Fake sounddevice.InputStream that captures the callback for direct
    invocation from the test.

    Mimics the real InputStream's start()/stop()/close() surface so
    Recorder.start() and stop() work unmodified.
    """

    def __init__(self, samplerate, channels, dtype, device, callback, **kwargs):
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


def _drain_ring_buffer(rec, timeout_s: float = 2.0) -> None:
    """Wait for the audio worker thread to drain the SPSC ring buffer.

    RT-SAFE-001: the PortAudio callback pushes chunks to a ring buffer and
    returns immediately; a daemon worker thread processes them
    asynchronously. Tests that push chunks via ``FakeInputStream`` must
    call this helper before asserting on ``rec._buffer`` — otherwise the
    worker may not have processed the chunks yet.
    """
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if len(rec._ring_buffer) == 0:
            return
        time.sleep(0.005)


def _make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    """Generate a mono sine wave reshaped to (frames, 1) for PortAudio."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32).reshape(-1, 1)


class _FilterConfig:
    """Minimal config stub for ``AudioProcessor`` (matches the pattern in
    tests/test_recording_audio_processor.py)."""

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
    """Construct a Recorder attached to a real ``AudioProcessor`` and
    simulated ``device_native_sr`` microphone.

    Returns ``(recorder, captured_streams)`` so the test can drive the
    callback via ``captured_streams[0].push_chunk(...)``.
    """
    from voice_typer.server import recording as rec_mod
    from voice_typer.server.audio_processor import AudioProcessor
    from voice_typer.server.recording import Recorder

    captured_streams: list[FakeInputStream] = []

    def fake_input_stream(*args, **kwargs):
        s = FakeInputStream(*args, **kwargs)
        captured_streams.append(s)
        return s

    monkeypatch.setattr(rec_mod.sd, "InputStream", fake_input_stream)

    # Simulate a device whose native rate is ``device_native_sr`` — the
    # CR-5 failure scenario is a non-16 kHz mic (e.g. 48000).
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


# ═══════════════════════════════════════════════════════════════════════════


class TestNoDoubleResample:
    """When AudioProcessor is active, buffer_sr must equal 16000, not the
    device's native rate."""

    def test_buffer_sr_tracks_processor_rate_when_active(self, monkeypatch):
        """_buffer_sr must be 16000 when AudioProcessor is active, even if
        the device's native rate is 48000.

        Regression for CR-5 step 2: the callback path must set
        ``self._buffer_sr = self._audio_processor._sample_rate`` after
        ``process_chunk`` resamples to the chain's rate.
        """
        r, captured_streams = _make_recorder_with_processor(
            monkeypatch,
            device_native_sr=48000,
            chain_sr=16000,
        )
        r.start()
        assert captured_streams, "start() should have opened an InputStream"

        # The device native rate (48 kHz) should now be the effective rate.
        assert r._effective_sr == 48000, f"Expected _effective_sr=48000 (device native), got {r._effective_sr}"

        # Push one chunk of 1024 samples at 48 kHz. The processor will
        # resample to 16 kHz before appending to the buffer (when scipy
        # is available) or fall back to passthrough (when scipy is
        # unavailable — see audio_processor.py:148+). Either way, the
        # ``_buffer_sr`` tracker must reflect the chain's construction
        # rate (16000), NOT the device's native rate (48000).
        chunk_48k = _make_sine(freq=440, duration_s=1024 / 48000, sr=48000, amp=0.3)
        captured_streams[0].push_chunk(chunk_48k)
        _drain_ring_buffer(r)

        # CR-5: _buffer_sr must reflect the chain's construction rate
        # (16000), NOT the device's native rate (48000).
        assert r._buffer_sr == 16000, (
            f"Expected _buffer_sr=16000 (chain rate after process_chunk), "
            f"got {r._buffer_sr}. CR-5 regression: stop()/snapshot() will "
            f"resample a second time from {r._effective_sr} Hz."
        )

        # Sanity check: the buffer grew by exactly one chunk.
        assert len(r._buffer) == 1, f"Expected 1 buffered chunk, got {len(r._buffer)}"

        r.stop()

    def test_buffer_sr_tracks_device_rate_when_no_processor(self, monkeypatch):
        """_buffer_sr must equal _effective_sr when AudioProcessor is None.

        CR-5 step 2 (else branch): when no filter chain is active, the
        buffer stores audio at the device's native rate.
        """
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
        assert captured_streams

        assert r._effective_sr == 48000

        chunk_48k = _make_sine(freq=440, duration_s=1024 / 48000, sr=48000, amp=0.3)
        captured_streams[0].push_chunk(chunk_48k)
        _drain_ring_buffer(r)

        # CR-5 else-branch: no processor → buffer stores native-rate audio.
        assert r._buffer_sr == 48000, (
            f"Expected _buffer_sr=48000 (device native rate when no processor), got {r._buffer_sr}"
        )

        # Sanity check: the buffered chunk should be at 48 kHz (no
        # resampling happened).
        assert len(r._buffer) == 1
        buffered = r._buffer[0]
        assert buffered.size == 1024, f"Expected 1024 samples (48 kHz, no resampling), got {buffered.size}"

        r.stop()

    def test_stop_uses_buffer_sr_not_effective_sr(self, monkeypatch):
        """stop() must pass _buffer_sr to _prepare_audio, not _effective_sr.

        Regression for CR-5 step 3: the call site
        ``effective_sr = self._buffer_sr or self._effective_sr`` must
        prefer the buffer's actual rate.

        Setup: simulate a session where the buffer holds 16 kHz audio
        (post-process_chunk) but the device native rate was 48 kHz.
        ``stop()`` should pass 16000 to ``_prepare_audio``, NOT 48000.
        """
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
        # even though the device native rate (_effective_sr) is 48 kHz.
        r._effective_sr = 48000
        r._buffer_sr = 16000
        # Pretend recording is active so stop() doesn't early-return.
        r._recording_event.set()
        # Put a chunk in the buffer so stop()'s concat path runs.
        r._buffer.append(np.zeros(1024, dtype=np.float32))

        # Mock the teardown helpers so stop() doesn't try to stop real
        # threads (we never started them).
        r._teardown_stream = MagicMock()
        r._stop_audio_worker = MagicMock()
        r._stop_event_worker = MagicMock()
        r._stop_device_health_checker = MagicMock()

        # Capture the effective_sr argument to _prepare_audio.
        captured_sr: list[int] = []
        original_prepare = r._prepare_audio

        def spy_prepare(audio, effective_sr, **kw):
            captured_sr.append(int(effective_sr))
            return original_prepare(audio, effective_sr, **kw)

        r._prepare_audio = spy_prepare

        r.stop()

        assert len(captured_sr) == 1, f"Expected _prepare_audio to be called once, got {len(captured_sr)} calls"
        assert captured_sr[0] == 16000, (
            f"Expected _prepare_audio to receive effective_sr=16000 "
            f"(_buffer_sr), got {captured_sr[0]}. CR-5 regression: "
            f"stop() is passing _effective_sr (48000) instead of "
            f"_buffer_sr (16000), causing a double-resample."
        )

    def test_snapshot_uses_buffer_sr_not_effective_sr(self, monkeypatch):
        """snapshot() must use _buffer_sr, not _effective_sr, when
        deciding whether to resample.

        Regression for CR-5 step 4: when ``_buffer_sr == target_sr``
        (i.e. the processor already resampled to 16 kHz), snapshot()
        must take the no-resample branch and return audio at the
        buffer's actual length — NOT call ``_resample_chunk`` to shrink
        the audio by a 3× factor.

        Setup: buffer holds 1024 samples of 16 kHz audio, but
        ``_effective_sr`` is 48000 (the pre-CR-5 broken state).
        Pre-fix, snapshot() would see effective_sr=48000 != target_sr=16000
        and resample 1024 samples → ~341 samples (3× shrinkage = garbage).
        Post-fix, snapshot() sees _buffer_sr=16000 == target_sr=16000 and
        returns the 1024 samples unchanged.
        """
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
        # (1024 samples) even though the device native rate is 48 kHz.
        r._effective_sr = 48000
        r._buffer_sr = 16000
        # Put a chunk in the buffer (1024 samples of 16 kHz audio).
        r._buffer.append(np.zeros(1024, dtype=np.float32))

        # Spy on _resample_chunk — it must NOT be called when
        # _buffer_sr == target_sr.
        resample_calls: list[tuple[int, int]] = []
        original_resample = r._resample_chunk

        def spy_resample(audio, effective_sr, target_sr):
            resample_calls.append((int(effective_sr), int(target_sr)))
            return original_resample(audio, effective_sr, target_sr)

        r._resample_chunk = spy_resample

        audio = r.snapshot()

        # CR-5: no resampling should have happened.
        assert resample_calls == [], (
            f"Expected _resample_chunk NOT to be called (buffer_sr=16000 "
            f"== target_sr=16000), but it was called with: "
            f"{resample_calls}. CR-5 regression: snapshot() is "
            f"resampling a second time from _effective_sr (48000)."
        )

        # And the returned audio must be the full 1024 samples, not
        # shrunk to ~341 by a phantom 48k→16k resample.
        assert audio.size == 1024, (
            f"Expected 1024 samples (no resampling), got {audio.size}. "
            "CR-5 regression: snapshot() shrank the audio by resampling "
            "from _effective_sr (48000) instead of _buffer_sr (16000)."
        )

    def test_buffer_sr_resets_on_stop_and_discard(self, monkeypatch):
        """stop() and discard() must reset _buffer_sr to None so a
        subsequent start() cycle doesn't reuse the stale rate.

        CR-5 step 7: when stop() or discard() clears the buffer, also
        reset ``self._buffer_sr = None``.
        """
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
        r._buffer_sr = 16000
        r._recording_event.set()
        r._buffer.append(np.zeros(1024, dtype=np.float32))

        # Mock the teardown helpers.
        r._teardown_stream = MagicMock()
        r._stop_audio_worker = MagicMock()
        r._stop_event_worker = MagicMock()
        r._stop_device_health_checker = MagicMock()

        # stop() must reset _buffer_sr to None.
        r.stop()
        assert r._buffer_sr is None, f"Expected _buffer_sr=None after stop(), got {r._buffer_sr}"

        # discard() must also reset _buffer_sr to None.
        r._buffer_sr = 16000  # simulate a subsequent session
        r._recording_event.set()
        r.discard()
        assert r._buffer_sr is None, f"Expected _buffer_sr=None after discard(), got {r._buffer_sr}"
