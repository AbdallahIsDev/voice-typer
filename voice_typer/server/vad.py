"""Silero VAD wrapper for waveform visualizer noise gating.

T021: Provides a lazy-loaded Voice Activity Detection (VAD) module that
filters out silence/noise from the waveform visualizer so it only
updates when the user is actually speaking.  Without VAD, the visualizer
reacts to any ambient noise, which is misleading.

Architecture:
    Audio chunk → Silero VAD → vad_prob > 0.5? → YES → Update visualizer
                                              NO → Don't update (decay)

The Silero VAD model is small (~2MB) and runs in real-time on CPU.
It is loaded lazily on first use so the app doesn't pay the import cost
unless VAD is enabled.

The model is bundled locally as ``silero_vad.jit`` (next to this file)
and loaded via ``torch.jit.load()``. This keeps the app fully offline
(no GitHub fetch at first-use time) and ensures the PyInstaller bundle
is self-contained. If the bundled file is missing or the load fails,
``_load_model`` logs an ERROR and returns ``(None, None)`` so VAD
degrades to the RMS energy fallback (already handled by callers). No
network call is ever made — the offline guarantee (C-DATA-1) is preserved.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.duration import format_duration

np = lazy_module("numpy")

log = logging.getLogger(__name__)

# hoisted to module level so every ``compute_vad_prob`` call avoids
# rebuilding the dict on the hot path (16 Hz audio worker). Silero VAD
# strictly expects 512 samples at 16 kHz (or 256 at 8 kHz); other rates
# fall back to the 16 kHz default of 512.
_EXPECTED_SAMPLES: dict[int, int] = {16000: 512, 8000: 256}

# Silero VAD probability threshold — values above this are considered speech.
# this is now a *fallback default* used only when callers don't pass
# their own threshold. Upstream callers (VadProcessor) pass a config-derived
# value via ``is_speech(..., threshold=...)`` so the VAD module doesn't
# impose its own threshold on the rest of the pipeline.
VAD_THRESHOLD = 0.5

# early-exit threshold for the multi-sub-chunk inference loop in
# :func:`compute_vad_prob`. Once a sub-chunk returns a probability ≥ this
# value, the loop breaks — speech is an "any sub-chunk contains it"
# decision, so a single high-confidence sub-chunk is sufficient evidence
# and we skip further torch inference cycles. The threshold is chosen
# above the typical +50 dB speech-prob ceiling so the early-exit only
# fires on high-confidence speech frames; the test contract in
# ``tests/test_vad_dtype_optimization.py::test_compute_vad_prob_long_chunk_float32_input``
# (probs 0.3 and 0.9 for the two sub-chunks) is preserved because both
# values are below the threshold.
_VAD_EARLY_EXIT_PROB: float = 0.95

# Path to the bundled Silero VAD JIT model (next to this file)
_VAD_MODEL_PATH = Path(__file__).resolve().parent / "silero_vad.jit"

# Lazy-loaded model reference
_model = None
_utils = None

#: Guards the one-time ``[VAD] Silero VAD model preloaded + warmed``
#: INFO line. ``preload()`` is invoked from BOTH the app-startup
#: background thread (app.py) AND the startup-sequence prewarm task —
#: without the guard the identical line was logged twice within
#: milliseconds from two threads, cluttering the log with a duplicate.
_preload_warmed_logged: bool = False


def is_available() -> bool:
    """Check if Silero VAD can be loaded (torch + silero dependencies)."""
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _check_vad_available() -> bool:
    """Cheap startup check: is Silero VAD usable WITHOUT a network round-trip?

        Returns True only if torch is importable AND the bundled local model
        file exists, so ``_load_model`` will succeed via ``torch.jit.load``
        without ever touching the network. Returns False if either:

          * torch is not importable (VAD entirely unavailable), or
          * the bundled ``silero_vad.jit`` is missing — in which case
            ``_load_model`` logs an ERROR and returns ``(None, None)`` so
            VAD degrades to the RMS fallback (handled by callers).

    this is the helper the issue asked for — called once at
        startup so the app can surface a warning when VAD will be unavailable
        *before* the first dictation, rather than failing silently. It does a
        filesystem stat only (no model load, no network), so it is safe to call
        from ``RecordingController.__init__`` on the startup path.
    """
    if not is_available():
        return False
    return _VAD_MODEL_PATH.exists()


def _load_model():
    """Lazily load the Silero VAD model and utils.

    Loads the local bundled ``silero_vad.jit`` via ``torch.jit.load()``.
        Subsequent calls return the cached model immediately. If the
        bundled file is missing or the load fails, logs an ERROR and
        returns ``(None, None)`` so VAD degrades to the RMS energy
        fallback (handled by callers). No network call is ever made —
        the app stays fully offline (C-DATA-1).
    """
    global _model, _utils
    if _model is not None:
        return _model, _utils

    try:
        import torch
    except ImportError:
        log.warning("[VAD] torch not importable — Silero VAD disabled")
        return None, None

    if not _VAD_MODEL_PATH.exists():
        log.error(
            "[VAD] bundled model not found at %s — Silero VAD disabled; "
            "degrading to RMS fallback (no network fetch is attempted)",
            _VAD_MODEL_PATH,
        )
        return None, None

    try:
        log.debug("[VAD] Loading local Silero VAD model from %s", _VAD_MODEL_PATH)
        # Silero VAD is a small LSTM (~2 MB). For 512-sample
        # chunks at 16 Hz, CPU inference (~0.5 ms) is faster than the
        # GPU transfer overhead (~1-2 ms roundtrip). Keep on CPU even
        # when CUDA is available — intentionally NOT probing / moving
        # to CUDA. Other ML paths (parakeet_engine, qwen_engine,
        # transcription) DO probe CUDA because their workloads benefit
        # from it; VAD's small model + tiny per-call tensor size does
        # not. Documented here so a future reader doesn't 'fix' this
        # by adding .to('cuda') and regressing performance.
        _model = torch.jit.load(str(_VAD_MODEL_PATH))
        _model.eval()
        _utils = None  # JIT model bundles everything, no utils needed
        log.info("[VAD] Silero VAD model loaded from local file")
        return _model, _utils
    except Exception as local_exc:
        log.error(
            "[VAD] local Silero VAD model load failed: %s — Silero VAD "
            "disabled; degrading to RMS fallback (no network fetch is attempted)",
            local_exc,
        )
        _model = None
        return None, None


def _reflect_pad_to(chunk: np.ndarray, expected: int) -> np.ndarray:
    """Reflect-pad a 1-D audio chunk to ``expected`` samples.

    zero-padding short chunks is out-of-distribution for Silero
        (the LSTM interprets the trailing silence as a noise frame and
        systematically under-reports speech → false negatives). Reflect-
        padding mirrors the chunk's own spectral content, keeping the input
        in-distribution.

        Done in numpy (before tensor conversion) so the test torch-mock —
        which only stubs ``from_numpy`` / ``zeros`` / ``cat`` — does not need
        ``flip`` / ``repeat`` shims.

        Strategy:
          * If the chunk is long enough to reflect from its own tail
            (``n >= shortfall``), mirror the last ``shortfall`` samples.
          * Otherwise tile the chunk to fill, then truncate.
          * Empty chunk → zero-fill (no information to reflect).
    """
    n = int(chunk.shape[0])
    shortfall = expected - n
    if shortfall <= 0:
        return chunk
    out_dtype = chunk.dtype if chunk.dtype != np.float64 else np.float32
    if n == 0:
        return np.zeros(expected, dtype=out_dtype)
    if n >= shortfall:
        reflect = np.flip(chunk[-shortfall:])
    else:
        repeats = (shortfall + n - 1) // n
        reflect = np.tile(chunk, repeats)[:shortfall]
    # single allocation via the ``dtype=`` kwarg instead of
    # ``.astype()`` (which would allocate a second array and copy).
    return np.concatenate([chunk, reflect], dtype=out_dtype)


def compute_vad_prob(audio_chunk: np.ndarray, sample_rate: int = WHISPER_SAMPLE_RATE) -> float | None:
    """Compute the VAD probability for an audio chunk.

        Args:
            audio_chunk: numpy float32 array of audio samples (16kHz mono)
            sample_rate: sample rate of the audio (default: 16000)

        Returns:
            Probability of speech (0.0–1.0), or None if VAD is unavailable.

        VAD-001: Silero VAD strictly expects 512 samples at 16kHz (or 256
        at 8kHz). When the audio chunk is a different size (e.g. 1136
        samples from a WASAPI device with no blocksize set), the model
        raises ValueError. We now pad or truncate the chunk to the
        expected size before inference, so VAD works regardless of the
        PortAudio buffer size.

    short chunks are reflect-padded (not zero-padded) to stay
        in-distribution for the Silero LSTM and avoid false negatives.

    long chunks are sliced into 512-sample sub-chunks
        and the model is run on each. The MAX probability is returned —
        speech is an "any sub-chunk contains it" decision, so max is more
        sensitive than mean for short bursts. (Cost is bounded by the
    worker-thread context per  — VAD no longer runs on the
        audio callback, so N× inference is acceptable.)

    option (c) "move compute_vad_prob to a dedicated VAD worker
        thread fed by a queue (decouple from capture)" is ALREADY
    IMPLEMENTED per  — ``recorder._audio_callback_dispatch``
        enqueues the chunk into an SPSC ring buffer and wakes the audio
        worker thread (``_audio_worker_loop`` / ``_process_audio_chunk``),
        which calls ``compute_vad_prob`` from ``audio_pipeline.run_vad_state_machine``.
        The audio capture thread does NOT run torch inference. Options (a)
        "drop the multi-sub-chunk loop" and (b) "batch sub-chunks as a
        single 2D tensor" would break the ``test_compute_vad_prob_long_chunk_float32_input``
        contract (pinned call_count=2 for a 1024-sample input sliced into
        two 512-sample sub-chunks) and are NOT applied here — the worker-
    thread context makes N× inference acceptable per  The
        ``_VAD_EARLY_EXIT_PROB`` threshold below provides a partial speed-up:
        once a sub-chunk returns a very-high probability, no further sub-
        chunks are inferred (speech is an "any sub-chunk contains it"
        decision, so a single high-prob sub-chunk is sufficient evidence).
    """
    model, utils = _load_model()
    if model is None:
        return None

    try:
        import torch

        # Silero expects a 1D float32 tensor
        # pass copy=False so torch skips the dtype-conversion copy
        # when the input is already float32 (which is the case everywhere
        # upstream — audio_pipeline.py:441 and audio_processor.py:318 both
        # call .astype(np.float32) before reaching here). copy=False is a
        # no-op when the dtype already matches; falls back to a copy only
        # when a real conversion is needed (defensive for any future caller
        # that feeds int16 / float64).
        audio_tensor = torch.from_numpy(audio_chunk).to(torch.float32, copy=False)
        if audio_tensor.dim() > 1:
            audio_tensor = audio_tensor.squeeze()

        # use the module-level ``_EXPECTED_SAMPLES`` constant
        # instead of rebuilding the dict on every call.
        expected = _EXPECTED_SAMPLES.get(sample_rate, 512)
        n = audio_tensor.shape[0]

        # reflect-pad short chunks BEFORE inference (zero-padding
        # is out-of-distribution for Silero and under-reports speech).
        if n < expected:
            padded = _reflect_pad_to(audio_chunk.astype(np.float32, copy=False), expected)
            audio_tensor = torch.from_numpy(padded).to(torch.float32, copy=False)
            if audio_tensor.dim() > 1:
                audio_tensor = audio_tensor.squeeze()
            n = expected

        if n == expected:
            # Exact fit — single inference, no slicing overhead.
            with torch.no_grad():
                return model(audio_tensor, sample_rate).item()

        # multi-sub-chunk path. Run the model on each
        # full 512-sample sub-chunk and take MAX — speech is an "any
        # sub-chunk contains it" decision. The trailing remainder
        # (< 512 samples) is dropped: at 16 kHz that's ≤31 ms of audio,
        # below the Silero false-negative floor.
        #
        # early-exit once a sub-chunk returns a very-high
        # probability. Speech is an "any sub-chunk contains it"
        # decision, so a single high-prob sub-chunk is sufficient
        # evidence — no need to spend further torch inference cycles on
        # the remaining sub-chunks. The threshold (0.95) is chosen
        # above the typical noise-floor +50 dB speech-prob ceiling so
        # the early-exit only fires on high-confidence speech frames;
        # the test contract (probs 0.3 and 0.9 for the two sub-chunks
        # in ``test_compute_vad_prob_long_chunk_float32_input``) is
        # preserved (both values are below the threshold).
        num_sub = n // expected
        probs: list[float] = []
        with torch.no_grad():
            for i in range(num_sub):
                sub = audio_tensor[i * expected : (i + 1) * expected]
                prob = model(sub, sample_rate).item()
                probs.append(prob)
                if prob >= _VAD_EARLY_EXIT_PROB:
                    # High-confidence speech — skip remaining sub-chunks.
                    # MAX is now ``prob`` (it's >= the threshold, and
                    # all prior probs were < threshold).
                    break
        return max(probs) if probs else 0.0
    except Exception as exc:
        log.debug("[VAD] Inference failed: %s", exc)
        return None


def is_speech(
    audio_chunk: np.ndarray,
    sample_rate: int = WHISPER_SAMPLE_RATE,
    threshold: float | None = None,
) -> bool:
    """Determine if an audio chunk contains speech.

        Returns True if the VAD probability exceeds the threshold,
        False otherwise.  Falls back to a simple RMS-based check
        if VAD is unavailable.

    ``threshold`` lets upstream callers (e.g. ``VadProcessor``)
        pass their config-derived Silero probability threshold so the VAD
        module doesn't impose its own ``VAD_THRESHOLD`` constant on the
        pipeline. Defaults to ``VAD_THRESHOLD`` for backward compatibility
        with callers that don't pass one.
    """
    if len(audio_chunk) == 0:
        return False

    effective_threshold = threshold if threshold is not None else VAD_THRESHOLD

    prob = compute_vad_prob(audio_chunk, sample_rate)
    if prob is not None:
        return prob > effective_threshold

    # Fallback: simple RMS energy check if VAD is unavailable.
    # the RMS-fallback energy floor (0.01) is intentionally NOT
    # derived from ``effective_threshold`` — the two are in different
    # units (probability vs. linear amplitude) and conflating them
    # would silently change semantics for the rare RMS path. A future
    # refactor could expose this as a separate config field.
    #
    # use ``np.dot`` on a flat view instead of the
    # ``np.sqrt(np.mean(audio_chunk**2))`` expression. The old form
    # allocated a temporary squared array (O(n) memory + a separate
    # mean reduction pass); ``np.dot`` is a single BLAS sdot call with
    # no intermediate allocation.
    flat = audio_chunk.ravel()
    rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
    return rms > 0.01


def preload() -> bool:
    """Eagerly load + warm up the Silero VAD model.

    lazy-loading on the first audio chunk caused 150-600ms of
        initial dropout (model load from disk + JIT graph compile). Call
        this from a startup background thread (e.g. ``Recorder.__init__``
        or app startup) so the model is hot by the time the first audio
        chunk arrives.

    (warmup): the first ``model()`` call after load
        JIT-compiles the graph (~50-200ms on CPU). Pre-warming with a
        zero tensor moves that cost off the first real audio chunk.

        Returns:
            True if the model is loaded (and warm-up inference succeeded),
            False if VAD is unavailable or warm-up failed. Safe to call
            multiple times — the cached model is returned immediately on
            subsequent calls.
    """
    # C-LOG-2: report total preload duration (model load + warmup) on
    # the "preloaded + warmed" completion line.
    _t0 = time.perf_counter()
    model, _ = _load_model()
    if model is None:
        return False
    try:
        import torch

        # Silero expects 512 samples at 16kHz for warmup.
        dummy = torch.zeros(512, dtype=torch.float32)
        with torch.no_grad():
            model(dummy, WHISPER_SAMPLE_RATE)
    except Exception:
        log.debug("[VAD] warmup inference failed", exc_info=True)
        return False
    global _preload_warmed_logged
    if not _preload_warmed_logged:
        # Emit the one-time INFO so the log stays clean when both
        # startup paths (app thread + prewarm task) call preload().
        _preload_warmed_logged = True
        log.info(
            "[VAD] Silero VAD model preloaded + warmed%s",
            format_duration(time.perf_counter() - _t0),
        )
    else:
        log.debug("[VAD] Silero VAD model preloaded + warmed (repeat call)")
    return True


def unload() -> None:
    """Release the Silero VAD model from memory.

    VAD can be disabled mid-session (user toggles all noise
        filters off via the "Off" audio preset). Without unload, the ~2MB
        Silero model stays pinned in RAM for the lifetime of the process.
        This drops the reference so Python can GC it. Safe to call when
        VAD is already unloaded (no-op).
    """
    global _model, _utils
    _model = None
    _utils = None
    log.info("[VAD] Silero VAD model unloaded")


def reset_states() -> None:
    """Reset the Silero VAD LSTM hidden state.

    Silero VAD is an LSTM — its hidden state accumulates across
        chunks. Without reset, state from one session bleeds into the next,
        causing the model to "expect" speech patterns from the prior
        speaker/environment and produce stale probabilities. Call this at
        session boundaries (``Recorder.start()`` via ``VadProcessor.reset()``).

        No-op if the model isn't loaded (avoids triggering a load just to
        reset state — the model starts with a fresh state on first load).
    """
    if _model is None:
        return
    try:
        if hasattr(_model, "reset_states"):
            _model.reset_states()
    except Exception as exc:
        log.debug("[VAD] reset_states failed: %s", exc)


def reset():
    """Reset the cached model (for testing or if model needs re-loading)."""
    global _model, _utils
    _model = None
    _utils = None
