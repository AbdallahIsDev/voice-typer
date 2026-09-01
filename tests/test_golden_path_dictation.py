"""Golden-path dictation integration test — real pipeline, boundary mocks only.

This is the first integration test that drives the REAL dictation
pipeline end to end:

    synthetic sine waveform
      → real ``Recorder`` capture loop (ring buffer → audio worker)
      → real ``AudioProcessor`` filter chain (high-pass stage active)
      → real ``recorder.stop()`` resample + stats
      → scripted (behavioral) ASR engine via the real registry seam
      → real ``DictationPipeline.run()`` — all 11 stages unmocked
        (cleanup → vocabulary → templates → punctuation → llm-skip →
        ai-skip → vocab-auto-skip → history DB → clipboard paste)
    → assert pasted payload + history row + crash-recovery lifecycle

External boundaries mocked (and ONLY these):

* ``sounddevice.InputStream`` — no audio hardware. The fake captures
  the PortAudio callback so the test feeds chunks exactly like the
  driver would (same pattern as ``tests/test_recording_audio_processor.py``).
* ASR model weights — a small scripted engine object registered through
  the production ``app.models.transcriber`` setter, so the registry /
  busy-flag / active-backend selection machinery stays REAL.
* OS clipboard + paste keystroke — a behavioral ``ClipboardManager``
  stand-in injected through the production ``app.clipboard`` setter.
  The real manager would SendInput Ctrl+V into whatever window has
  focus on the developer's machine.

Everything else is production code: ``VoiceTyperApp``, ``Config``,
``Recorder``, ``AudioProcessor`` chain, text cleanup, bundled
vocabulary corrections, template matching, auto-punctuation,
``HistoryDB``, ``CrashRecovery``, ``CorrectionUsageTracker``, the
11-stage pipeline loop and its finally-block teardown.

Observable contract pinned here (regressions fail CI):

* filter-contract: recorded samples survive the real chain with
  speech-band energy intact (a silent/zeroed buffer fails).
* recorder→engine seam: the exact filtered array + pre-computed audio
  stats captured by ``recorder.stop()`` are what the engine receives.
* pipeline ordering: cleanup capitalizes BEFORE vocabulary corrects
  ("recieve"→"receive", "grammer"→"grammar" from the bundled
  corrections.json) and punctuation appends the terminal period AFTER
  vocabulary — the final pasted string is fully deterministic.
* storage contract: one history row with the final text; one
  crash-recovery entry added unpasted by the store step and flipped to
  pasted after the successful paste; the in-flight sentinel is cleaned.
"""

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
_TONE_HZ = 440.0  # speech band — passes the 80 Hz high-pass untouched
_TONE_AMP = 0.3

# Deterministic golden transcript: two misspellings covered by the
# BUNDLED defaults in voice_typer/server/corrections.json plus plain
# filler words that survive every cleanup helper unchanged.
_RAW_TRANSCRIPT = "please recieve the report grammer"
_FINAL_TRANSCRIPT = "Please receive the report grammar."


class FakeInputStream:
    """Stand-in for ``sounddevice.InputStream`` (no audio hardware).

    Captures the callback so the test can deliver chunks exactly as
    PortAudio would. Mirrors the proven pattern in
    ``tests/test_recording_audio_processor.py``.
    """

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
    """Return the InputStream opened by ``Recorder.start()`` (has a callback).

    ``Recorder.__init__`` may open prewarm/probe streams without a
    callback; the capture stream is the one carrying the real one.
    """
    for stream in streams:
        if stream.callback is not None:
            return stream
    raise AssertionError("no InputStream with a callback was opened by Recorder.start()")


def _drain_ring_buffer(recorder: Any, timeout_s: float = 5.0) -> None:
    """Wait for the audio worker thread to drain the SPSC ring buffer."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if len(recorder._ring_buffer) == 0:
            # give the worker a beat to finish appending the popped chunk
            time.sleep(0.05)
            return
        time.sleep(0.005)


class ScriptedTranscriber:
    """Behavioral ASR-engine stand-in (model weights are an external dep).

    Registered through the production ``ModelManager.transcriber`` setter
    so the real ``AsrBackendRegistry`` (registration, active-backend
    selection, busy-flag context) executes around it. Records the audio
    + audio_stats it was handed so the recorder→engine seam is pinned.
    """

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
    """Behavioral ``ClipboardManager`` stand-in (OS clipboard boundary).

    Implements the exact surface ``DictationPipeline._copy_and_paste``
    consumes: ``copy(text)`` returns no snapshot (save/restore disabled
    semantics), ``paste(...)`` reports success, and both record their
    payloads for assertion. The production setter (``app.clipboard =``)
    injects it, so nothing inside the paste step is mocked.
    """

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
    """Real ``VoiceTyperApp`` with hardware/GUI deps mocked at the boundary.

    Mirrors the canonical ``tests/app/conftest.py::app`` fixture (via the
    shared ``make_voice_typer_app`` helper) and pins a deterministic
    noise-filter preset: the REAL AudioProcessor chain runs with the
    high-pass stage active while the stochastic/heavy stages (gate,
    RNNoise, EQ, compressor, limiter) are off so amplitude assertions
    stay deterministic. VAD is disabled — Silero model loading is an
    external-model boundary orthogonal to the filter chain under test.
    """
    instance = make_voice_typer_app(tmp_config_dir, monkeypatch)

    # Arrange the post-O2 on-disk layout (<config>/db/) BEFORE the first
    # ``app.history_db`` access, pinning the upgraded-install layout.
    # Production creates this dir itself: open_write_conn /
    # _get_read_conn mkdir it unconditionally on every platform (fresh
    # installs), and the legacy-DB migration creates it when a pre-O2
    # root ``history.db`` exists. Pre-creating it here keeps this test
    # independent of those paths — the fresh-install contract is pinned
    # by tests/test_history_db_fresh_install_dir.py.
    (tmp_config_dir / "db").mkdir(parents=True, exist_ok=True)

    config = instance.config
    config.asr_backend = "whisper"
    # Deterministic non-"off" filter preset — real chain, highpass active.
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
    # so future default drift fails HERE instead of silently changing
    # the expected final string).
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

    # ── Boundary mock: sounddevice.InputStream (audio hardware) ──
    captured_streams: list[FakeInputStream] = []

    def fake_input_stream(*args, **kwargs):
        stream = FakeInputStream(*args, **kwargs)
        captured_streams.append(stream)
        return stream

    monkeypatch.setattr(rec_pkg.sd, "InputStream", fake_input_stream)

    # ── Boundary mock: OS clipboard + keystroke paste ──
    clipboard = RecordingClipboard()
    app.clipboard = clipboard

    # ── Boundary mock: ASR model weights (registered via real setter) ──
    engine = ScriptedTranscriber(_RAW_TRANSCRIPT)
    app.models.transcriber = engine

    # ── Stage A: real Recorder capture through the real filter chain ──
    recorder = app.recorder
    recorder.start()
    stream = _capture_stream(captured_streams)
    for _ in range(_CHUNK_COUNT):
        chunk = make_sine(freq=_TONE_HZ, duration_s=_CHUNK_SAMPLES / _WHISPER_SR, sr=_WHISPER_SR, amp=_TONE_AMP)
        stream.push_chunk(chunk.reshape(-1, 1))

    _drain_ring_buffer(recorder)
    assert len(recorder._audio_pipeline._buffer) == _CHUNK_COUNT, (
        f"expected {_CHUNK_COUNT} buffered chunks, got {len(recorder._audio_pipeline._buffer)} — "
        "the real capture→filter-chain path dropped chunks"
    )

    audio = recorder.stop()

    # Filter-contract: the real chain delivered speech-band energy.
    assert isinstance(audio, np.ndarray) and audio.size > 0, "recorder.stop() returned no audio"
    filtered_rms = float(np.sqrt(np.mean(audio**2)))
    raw_rms = _TONE_AMP / np.sqrt(2.0)
    assert 0.5 * raw_rms <= filtered_rms <= 1.2 * raw_rms, (
        f"filtered RMS {filtered_rms:.4f} outside passband window "
        f"[{0.5 * raw_rms:.4f}, {1.2 * raw_rms:.4f}] — filter chain mangled the signal"
    )
    assert recorder._last_audio_stats is not None, "stop() did not record _last_audio_stats"

    # ── Stage B: real DictationPipeline.run() over all 11 stages ──
    duration = audio.shape[0] / _WHISPER_SR
    pipeline = DictationPipeline(app)
    cycle_id = "golden-path-cycle-0001"
    pipeline.run(
        audio=audio,
        duration=duration,
        recorded_rms=recorder.last_rms,
        cycle_id=cycle_id,
        watchdog=None,
    )

    # ── Contract 1: recorder→engine seam (filtered audio + stats) ──
    assert engine.received_audio is not None, "engine never received the captured audio"
    assert engine.received_audio.shape == audio.shape
    assert engine.received_audio_stats is recorder._last_audio_stats, (
        "engine did not receive the pre-computed recorder audio stats"
    )

    # ── Contract 2: final pasted payload (ordering: clean → vocab → punct) ──
    assert app._last_transcription == _FINAL_TRANSCRIPT, (
        f"final transcription drifted: {app._last_transcription!r} != {_FINAL_TRANSCRIPT!r}"
    )
    assert clipboard.copied == [_FINAL_TRANSCRIPT], "clipboard copy payload mismatch"
    assert clipboard.pasted == [_FINAL_TRANSCRIPT], "paste keystroke payload mismatch"

    # ── Contract 3: history row persisted with the final text ──
    assert app.history_db.get_latest_text() == _FINAL_TRANSCRIPT, "history DB row does not carry the final pasted text"

    # ── Contract 4: crash-recovery lifecycle (add unpasted → marked pasted) ──
    recovery_meta = app._crash_recovery.entries_metadata_snapshot()
    assert len(recovery_meta) == 1, f"expected exactly one recovery entry, got {len(recovery_meta)}"
    assert recovery_meta[0]["pasted"] is True, "recovery entry was not marked pasted after successful paste"

    # ── Contract 5: teardown — in-flight sentinel cleared, busy released ──
    sentinel = tmp_config_dir / ".dictation-in-flight"
    assert not sentinel.exists(), "in-flight sentinel survived a completed cycle"
    assert app._busy_event.is_set(), "busy event was not re-set (busy=False) by the finally block"


def test_golden_path_engine_receives_resampled_shape(golden_app, monkeypatch):
    """The engine sees audio already resampled to config.sample_rate.

    Pins the stop()-side resample seam: chunks pushed at the Whisper
    native rate must reach the engine as a flat float32 array whose
    length matches the configured sample rate × recorded duration.
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
        watchdog=None,
    )

    assert engine.received_audio.dtype == np.float32
    assert engine.received_audio.ndim == 1
    expected_samples = int(round(duration * app.config.sample_rate))
    assert abs(engine.received_audio.shape[0] - expected_samples) <= 8, (
        f"engine got {engine.received_audio.shape[0]} samples, "
        f"expected ~{expected_samples} at {app.config.sample_rate} Hz"
    )
