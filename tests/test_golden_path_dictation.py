"""Golden-path dictation integration test: real pipeline, boundary mocks only."""

from __future__ import annotations

import contextlib
import time
from typing import Any

import numpy as np
import pytest

from tests.fixtures.app_helpers import join_model_load_thread, make_sine, make_voice_typer_app

_WHISPER_SR = 16000
_CHUNK_SAMPLES = 512  # matches _AUDIO_BLOCKSIZE (VAD-001 contract)
_CHUNK_COUNT = 24  # 24 * 512 / 16000 = 0.768 s of audio
_TONE_HZ = 440.0  # speech band, passes the 80 Hz high-pass untouched
_TONE_AMP = 0.3

_RAW_TRANSCRIPT = "please recieve the report grammer"
_FINAL_TRANSCRIPT = "Please receive the report grammar."


class FakeInputStream:
    """Stand-in for ``sounddevice.InputStream`` (no audio hardware)."""

    def __init__(self, samplerate, channels, dtype, device=None, callback=None, **kwargs):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.device = device
        self.callback = callback
        self.started = False
        self.blocksize = kwargs.get("blocksize")

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.started = False

    def push_chunk(self, samples: np.ndarray) -> None:
        """Deliver one (frames, channels) block through the RT callback."""
        assert self.callback is not None, "capture stream was opened without a callback"
        self.callback(samples, samples.shape[0], None, 0)


def _capture_stream(streams: list[FakeInputStream]) -> FakeInputStream:
    """Return the InputStream opened by ``Recorder.start()`` (has a callback)."""
    for stream in streams:
        if stream.callback is not None:
            return stream
    raise AssertionError("no InputStream with a callback was opened by Recorder.start()")


def _drain_ring_buffer(recorder: Any, timeout_s: float = 5.0) -> None:
    """Wait for the audio worker thread to drain the SPSC ring buffer."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if len(recorder._ring_buffer) == 0:
            time.sleep(0.05)
            return
        time.sleep(0.005)


class ScriptedTranscriber:
    """Behavioral ASR-engine stand-in (model weights are an external dep)."""

    def __init__(self, transcript: str):
        self.transcript = transcript
        self.is_loaded = True
        self.device_info = "cpu"
        self.received_audio: np.ndarray | None = None
        self.received_audio_stats: tuple[float, float, float] | None = None

    def transcribe_with_fallback(
        self,
        audio: np.ndarray,
        *,
        audio_stats: tuple[float, float, float] | None = None,
        local_engine: Any = None,
    ) -> str:
        self.received_audio = audio
        self.received_audio_stats = audio_stats
        return self.transcript


class RecordingClipboard:
    """Behavioral ``ClipboardManager`` stand-in (OS clipboard boundary)."""

    def __init__(self) -> None:
        self.copied: list[str] = []
        self.pasted: list[str | None] = []
        self._clipboard_seq = 0

    def refresh_config(self, config: Any) -> None:
        return None

    def copy(self, text: str) -> None:
        self.copied.append(text)
        return None

    def paste(
        self,
        snapshot: Any = None,
        restore_delay: float | None = None,
        pasted_text: str | None = None,
        force: bool = False,
        pasted_seq: int | None = None,
    ) -> bool:
        self.pasted.append(pasted_text)
        return True


@pytest.fixture
def golden_app(tmp_config_dir, monkeypatch):
    """Real ``VoiceTyperApp`` with hardware/GUI deps mocked at the boundary."""
    instance = make_voice_typer_app(tmp_config_dir, monkeypatch)

    # ``app.history_db`` access, pinning the upgraded-install layout.
    (tmp_config_dir / "db").mkdir(parents=True, exist_ok=True)

    config = instance.config
    config.asr_backend = "whisper"
    # Deterministic non-"off" filter preset, real chain, highpass active.
    config.noise_filter_enabled = True
    config.noise_filter_highpass = True
    config.noise_filter_highpass_cutoff_hz = 80.0
    config.noise_filter_gate = False
    config.noise_filter_rnnoise = False
    config.noise_suppression_method = "none"
    config.noise_filter_eq = False
    config.noise_filter_compressor = False
    config.noise_filter_limiter = False
    config.noise_filter_notch = False
    # External-model boundary (Silero ONNX load), not under test here.
    config.use_silero_vad = False
    # Golden-path text stages all enabled (defaults, pinned explicitly
    config.text_cleanup_enabled = True
    config.vocabulary_enabled = True
    config.auto_punctuation = True
    config.llm_polish = False
    config.ai_enhancement_enabled = False
    config.vocabulary_automation_enabled = False
    config.paste_on_stop = True
    config.clipboard_save_restore = False
    config.history_enabled = True
    config.crash_recovery_enabled = True
    config.log_transcriptions = False

    yield instance

    with contextlib.suppress(Exception):
        if instance.history_db is not None:
            instance.history_db.close()
    with contextlib.suppress(Exception):
        instance.tray._cancel_elapsed_timer()
    join_model_load_thread(instance)


def test_golden_path_sine_to_final_text_history_and_recovery(golden_app, tmp_config_dir, monkeypatch):
    """Full golden path: sine waveform → real chain → scripted ASR → run()."""
    from voice_typer.server import recording as rec_pkg
    from voice_typer.server.dictation_pipeline import DictationPipeline

    app = golden_app

    captured_streams: list[FakeInputStream] = []

    def fake_input_stream(*args, **kwargs):
        stream = FakeInputStream(*args, **kwargs)
        captured_streams.append(stream)
        return stream

    monkeypatch.setattr(rec_pkg.sd, "InputStream", fake_input_stream)

    clipboard = RecordingClipboard()
    app.clipboard = clipboard

    engine = ScriptedTranscriber(_RAW_TRANSCRIPT)
    app.models.transcriber = engine

    recorder = app.recorder
    recorder.start()
    stream = _capture_stream(captured_streams)
    for _ in range(_CHUNK_COUNT):
        chunk = make_sine(freq=_TONE_HZ, duration_s=_CHUNK_SAMPLES / _WHISPER_SR, sr=_WHISPER_SR, amp=_TONE_AMP)
        stream.push_chunk(chunk.reshape(-1, 1))

    _drain_ring_buffer(recorder)
    assert len(recorder._audio_pipeline._buffer) == _CHUNK_COUNT, (
        f"expected {_CHUNK_COUNT} buffered chunks, got {len(recorder._audio_pipeline._buffer)}, "
        "the real capture→filter-chain path dropped chunks"
    )

    audio = recorder.stop()

    # Filter-contract: the real chain delivered speech-band energy.
    assert isinstance(audio, np.ndarray) and audio.size > 0, "recorder.stop() returned no audio"
    filtered_rms = float(np.sqrt(np.mean(audio**2)))
    raw_rms = _TONE_AMP / np.sqrt(2.0)
    assert 0.5 * raw_rms <= filtered_rms <= 1.2 * raw_rms, (
        f"filtered RMS {filtered_rms:.4f} outside passband window "
        f"[{0.5 * raw_rms:.4f}, {1.2 * raw_rms:.4f}], filter chain mangled the signal"
    )
    assert recorder._last_audio_stats is not None, "stop() did not record _last_audio_stats"

    duration = audio.shape[0] / _WHISPER_SR
    pipeline = DictationPipeline(app)
    cycle_id = "golden-path-cycle-0001"
    pipeline.run(
        audio=audio,
        duration=duration,
        recorded_rms=recorder.last_rms,
        cycle_id=cycle_id,
    )

    assert engine.received_audio is not None, "engine never received the captured audio"
    assert engine.received_audio.shape == audio.shape
    assert engine.received_audio_stats is recorder._last_audio_stats, (
        "engine did not receive the pre-computed recorder audio stats"
    )

    assert app._last_transcription == _FINAL_TRANSCRIPT, (
        f"final transcription drifted: {app._last_transcription!r} != {_FINAL_TRANSCRIPT!r}"
    )
    assert clipboard.copied == [_FINAL_TRANSCRIPT], "clipboard copy payload mismatch"
    assert clipboard.pasted == [_FINAL_TRANSCRIPT], "paste keystroke payload mismatch"

    assert app.history_db.get_latest_text() == _FINAL_TRANSCRIPT, "history DB row does not carry the final pasted text"

    recovery_meta = app._crash_recovery.entries_metadata_snapshot()
    assert len(recovery_meta) == 1, f"expected exactly one recovery entry, got {len(recovery_meta)}"
    assert recovery_meta[0]["pasted"] is True, "recovery entry was not marked pasted after successful paste"

    sentinel = tmp_config_dir / ".dictation-in-flight"
    assert not sentinel.exists(), "in-flight sentinel survived a completed cycle"
    assert app._busy_event.is_set(), "busy event was not re-set (busy=False) by the finally block"


def test_golden_path_engine_receives_resampled_shape(golden_app, monkeypatch):
    """
    The engine sees audio already resampled to config.sample_rate.
    Pins the stop()-side resample seam: chunks pushed at the Whisper
    """
    from voice_typer.server import recording as rec_pkg
    from voice_typer.server.dictation_pipeline import DictationPipeline

    app = golden_app

    captured_streams: list[FakeInputStream] = []

    def fake_input_stream(*args, **kwargs):
        stream = FakeInputStream(*args, **kwargs)
        captured_streams.append(stream)
        return stream

    monkeypatch.setattr(rec_pkg.sd, "InputStream", fake_input_stream)
    app.clipboard = RecordingClipboard()
    engine = ScriptedTranscriber(_RAW_TRANSCRIPT)
    app.models.transcriber = engine

    recorder = app.recorder
    recorder.start()
    stream = _capture_stream(captured_streams)
    for _ in range(_CHUNK_COUNT):
        chunk = make_sine(freq=_TONE_HZ, duration_s=_CHUNK_SAMPLES / _WHISPER_SR, sr=_WHISPER_SR, amp=_TONE_AMP)
        stream.push_chunk(chunk.reshape(-1, 1))
    _drain_ring_buffer(recorder)

    audio = recorder.stop()
    duration = audio.shape[0] / _WHISPER_SR
    pipeline = DictationPipeline(app)
    pipeline.run(
        audio=audio,
        duration=audio.shape[0] / _WHISPER_SR,
        recorded_rms=recorder.last_rms,
        cycle_id="golden-path-cycle-0002",
    )

    assert engine.received_audio.dtype == np.float32
    assert engine.received_audio.ndim == 1
    expected_samples = int(round(duration * app.config.sample_rate))
    assert abs(engine.received_audio.shape[0] - expected_samples) <= 8, (
        f"engine got {engine.received_audio.shape[0]} samples, "
        f"expected ~{expected_samples} at {app.config.sample_rate} Hz"
    )
