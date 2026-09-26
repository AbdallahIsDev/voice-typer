"""Silero VAD wrapper for waveform visualizer noise gating.

Provides a lazy-loaded Voice Activity Detection (VAD) module that
filters out silence/noise from the waveform visualizer so it only
updates when the user is actually speaking.  Without VAD, the visualizer
reacts to any ambient noise, which is misleading.

Architecture:
    Audio chunk → Silero VAD → vad_prob > 0.5? → YES → Update visualizer
                                              NO → Don't update (decay)

The Silero VAD model is small (~2MB) and runs in real-time on CPU.
It is loaded lazily on first use so the app doesn't pay the import cost
unless VAD is enabled.

The model is bundled locally as ``silero_vad.onnx`` (next to this file)
and loaded via ``onnxruntime.InferenceSession``. This keeps the app fully
offline (no GitHub fetch at first-use time) and ensures the PyInstaller
bundle is self-contained. If the bundled file is missing or the load
fails, ``_load_model`` logs an ERROR and returns ``(None, None)`` so VAD
degrades to the RMS energy fallback (already handled by callers). No
network call is ever made, the offline guarantee (C-DATA-1) is preserved.

CRITICAL, streaming-state threading (ADR 0005):
    ``onnxruntime.InferenceSession`` is **stateless**. The bundled
    Silero ONNX export takes ``(input, state, sr)`` and returns
    ``(output, stateN)``, and the official streaming contract requires
    each ``input`` to be a rolling context (64 samples at 16 kHz, 32
    at 8 kHz) prepended to the new 512/256-sample window (576/288
    samples total). The caller must thread BOTH the LSTM hidden-state
    buffer (shape ``(2, 1, 128)`` float32) and the context (trailing
    samples of the previous window) through every ``compute_vad_prob``
    call, re-zeroing both on ``reset_states()``, ``unload()``, and
    first load. Bare-window inference materially under-reports speech
    probabilities.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.duration import format_duration
from voice_typer.server.log_rate_limit import log_rate_limited

np = lazy_module("numpy")

log = logging.getLogger(__name__)

# Rate-limit cadence for the VAD failure-path logs below. ``_load_model``
_VAD_FAILURE_FIRST_ONLY_EVERY_N: int = 0  # first occurrence only
_VAD_LOAD_FAILED_EVERY_N: int = 225  # every ~14s at 16 Hz


# hoisted to module level so every ``compute_vad_prob`` call avoids
_EXPECTED_SAMPLES: dict[int, int] = {16000: 512, 8000: 256}

# Rolling-context sizes (official streaming contract): every ONNX input is
# context + window = 576/288 samples, never a bare window.
_CONTEXT_SAMPLES: dict[int, int] = {16000: 64, 8000: 32}

# Silero VAD probability threshold, values above this are considered speech.
VAD_THRESHOLD = 0.5

# early-exit threshold for the multi-sub-chunk inference loop in
_VAD_EARLY_EXIT_PROB: float = 0.95

# Path to the bundled Silero VAD ONNX model (next to this file).
_VAD_MODEL_PATH = Path(__file__).resolve().parent / "silero_vad.onnx"

# Silero LSTM hidden-state shape: see the module docstring's
_VAD_STATE_SHAPE: tuple[int, int, int] = (2, 1, 128)

# Lazy-loaded ORT session reference. Stays ``None`` until ``_load_model``
_model = None

#: The LSTM hidden-state buffer threaded across ``compute_vad_prob``
_state = None  # initialized lazily in _load_model to avoid eager numpy use

#: Rolling context (trailing samples of the previous window) prepended to
#: every ONNX input. ``None`` = stream not started / just reset.
_context = None

# ORT I/O names discovered at load time. Silero's ONNX export uses non-default
_input_name: str | None = None
_state_name: str | None = None
_sr_name: str | None = None
_output_name: str | None = None
_state_out_name: str | None = None

#: Guards the one-time ``[VAD] Silero VAD model loaded from local ONNX,
_preload_warmed_logged: bool = False


def is_available() -> bool:
    """Check if Silero VAD can be loaded (onnxruntime + bundled model).

    Returns ``True`` only when both (a) ``onnxruntime`` is importable
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return _VAD_MODEL_PATH.exists()


def _load_model():
    """Lazily load the Silero VAD ONNX model and initialize the hidden state.

    Loads the local bundled ``silero_vad.onnx`` via
        ``onnxruntime.InferenceSession(providers=["CPUExecutionProvider"])``.
        Subsequent calls return the cached session immediately. If the
        bundled file is missing or the load fails, logs an ERROR and
        returns ``(None, None)`` so VAD degrades to the RMS energy
        fallback (handled by callers). No network call is ever made —
        the app stays fully offline (C-DATA-1).

    Returns:
        ``(session, io_names)`` on success where ``io_names`` is the
        5-tuple ``(input, state, sr, output, stateN)`` of ORT I/O names
        discovered at load time. ``(None, None)`` on any failure path,
        preserving the 2-tuple contract so callers (and tests)
        that destructure the result keep working unchanged.

    ``providers=["CPUExecutionProvider"]`` is PINNED, not defaulted.
        ORT's default provider list includes CUDA when
        ``onnxruntime-gpu`` is installed. VAD is CPU-only by design:
        routing VAD to GPU adds GPU→CPU upload latency per 512-sample
        window and breaks the existing latency budget.
    """
    global _model, _state, _context, _input_name, _state_name, _sr_name, _output_name, _state_out_name
    if _model is not None:
        return _model, (_input_name, _state_name, _sr_name, _output_name, _state_out_name)

    try:
        import onnxruntime as _ort
    except ImportError:
        # Rate-limited: the failure is NOT cached (model stays None), so
        log_rate_limited(
            log,
            logging.WARNING,
            "[VAD] onnxruntime not importable. Silero VAD disabled",
            every_n=_VAD_FAILURE_FIRST_ONLY_EVERY_N,
        )
        return None, None

    if not _VAD_MODEL_PATH.exists():
        log_rate_limited(
            log,
            logging.ERROR,
            "[VAD] bundled model not found at %s. Silero VAD disabled; "
            "degrading to RMS fallback (no network fetch is attempted)",
            _VAD_MODEL_PATH,
            every_n=_VAD_FAILURE_FIRST_ONLY_EVERY_N,
        )
        return None, None

    try:
        log.debug("[VAD] Loading local Silero VAD ONNX model from %s", _VAD_MODEL_PATH)
        # Silero VAD is a small LSTM (~2 MB) run per 512-sample window.
        # Pin single-threaded pools: the forward pass is sub-millisecond, so
        # a core-wide pool costs more in spin-wait than it can save.
        session_options = _ort.SessionOptions()
        session_options.intra_op_num_threads = 1
        session_options.inter_op_num_threads = 1
        session = _ort.InferenceSession(
            str(_VAD_MODEL_PATH),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        # Discover I/O names (Silero's ONNX export uses non-default names).
        inputs = {i.name: i for i in session.get_inputs()}
        outputs = {o.name: o for o in session.get_outputs()}
        _input_name = "input" if "input" in inputs else next(iter(inputs))
        _state_name = "state" if "state" in inputs else next(iter(inputs))
        _sr_name = "sr" if "sr" in inputs else None
        _output_name = "output" if "output" in outputs else next(iter(outputs))
        _state_out_name = "stateN" if "stateN" in outputs else next(iter(outputs))
        _model = session
        # Initialize the LSTM hidden state to zeros on first load —
        _state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)
        _context = None
        # DEBUG only: the one-time INFO line is emitted by ``preload()``
        log.debug("[VAD] Silero VAD model loaded from local ONNX")
        return _model, (_input_name, _state_name, _sr_name, _output_name, _state_out_name)
    except Exception as local_exc:
        # Rate-limited for the same reason as the other failure paths:
        log_rate_limited(
            log,
            logging.ERROR,
            "[VAD] local Silero VAD ONNX model load failed: %s, Silero VAD "
            "disabled; degrading to RMS fallback (no network fetch is attempted)",
            local_exc,
            every_n=_VAD_LOAD_FAILED_EVERY_N,
        )
        _model = None
        return None, None


def _reflect_pad_to(chunk: np.ndarray, expected: int) -> np.ndarray:
    """Reflect-pad a 1-D audio chunk to ``expected`` samples."""
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
    return np.concatenate([chunk, reflect], dtype=out_dtype)


def _run_one_inference(audio_1d: np.ndarray, sr: int) -> float:
    """Run one ORT forward pass and thread the hidden state + context."""
    global _state, _context
    # ``_load_model`` sets these names before returning a non-None
    assert _input_name is not None
    assert _state_name is not None
    context_size = _CONTEXT_SAMPLES.get(sr, 64)
    if _context is None or _context.shape[0] != context_size:
        # First call after load/reset, or a sample-rate switch: restart the
        # stream with a zero context and a clean LSTM state (a state threaded
        # across rates is garbage), matching the official wrappers.
        _context = np.zeros(context_size, dtype=np.float32)
        _state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)
    window = np.asarray(audio_1d, dtype=np.float32)
    # Silero streaming contract: input = context + window (576/288 samples),
    # batch dim of 1.
    audio_batched = np.concatenate([_context, window]).reshape(1, -1)
    feed: dict[str, np.ndarray] = {
        _input_name: audio_batched,
        _state_name: _state,
    }
    if _sr_name is not None:
        feed[_sr_name] = np.array(sr, dtype=np.int64)
    out = _model.run(None, feed)
    # out[0] = output (shape (1, 1) for a single-window batch).
    prob = float(np.asarray(out[0]).reshape(-1)[0])
    # Thread the new hidden state + trailing context forward, the two
    # critical steps for a stateless ORT session.
    _state = np.asarray(out[1], dtype=np.float32)
    _context = audio_batched[0, -context_size:].copy()
    return prob


def compute_vad_prob(audio_chunk: np.ndarray, sample_rate: int = WHISPER_SAMPLE_RATE) -> float | None:
    """Compute the VAD probability for an audio chunk."""
    session, _names = _load_model()
    if session is None:
        return None

    try:
        # Silero expects a 1D float32 array. Coerce dtype + squeeze any
        audio = np.asarray(audio_chunk, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        expected = _EXPECTED_SAMPLES.get(sample_rate, 512)
        n = int(audio.shape[0])

        # reflect-pad short chunks BEFORE inference (zero-padding
        if n < expected:
            audio = _reflect_pad_to(audio, expected)
            n = expected

        if n == expected:
            # Exact fit, single inference, no slicing overhead.
            return _run_one_inference(audio, sample_rate)

        # multi-sub-chunk path. Run the model on each
        num_sub = n // expected
        probs: list[float] = []
        for i in range(num_sub):
            sub = audio[i * expected : (i + 1) * expected]
            prob = _run_one_inference(sub, sample_rate)
            probs.append(prob)
            if prob >= _VAD_EARLY_EXIT_PROB:
                # High-confidence speech, skip remaining sub-chunks.
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
    """
    if len(audio_chunk) == 0:
        return False

    effective_threshold = threshold if threshold is not None else VAD_THRESHOLD

    prob = compute_vad_prob(audio_chunk, sample_rate)
    if prob is not None:
        return prob > effective_threshold

    # Fallback: simple RMS energy check if VAD is unavailable.
    flat = audio_chunk.ravel()
    rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
    return rms > 0.01


def preload() -> bool:
    """Eagerly load + warm up the Silero VAD model."""
    # C-LOG-2: report total preload duration (model load + warmup) on
    _t0 = time.perf_counter()
    session, _ = _load_model()
    if session is None:
        return False
    try:
        # Silero expects 512 samples at 16kHz for warmup.
        dummy = np.zeros(512, dtype=np.float32)
        _run_one_inference(dummy, WHISPER_SAMPLE_RATE)
        # warmup pollutes the LSTM hidden state, reset to fresh zeros
        reset_states()
    except Exception:
        # Loaded but not warmed, still usable (first chunk pays the
        log.info("[VAD] Silero VAD model loaded from local ONNX, not warmed")
        log.debug("[VAD] warmup inference failed", exc_info=True)
        return False
    global _preload_warmed_logged
    if not _preload_warmed_logged:
        # Emit the one-time INFO so the log stays clean when both
        _preload_warmed_logged = True
        log.info(
            "[VAD] Silero VAD model loaded from local ONNX, preloaded + warmed%s",
            format_duration(time.perf_counter() - _t0),
        )
    else:
        log.debug("[VAD] Silero VAD model preloaded + warmed (repeat call)")
    return True


def unload() -> None:
    """Release the Silero VAD session from memory."""
    global _model
    _model = None
    reset_states()
    log.info("[VAD] Silero VAD model unloaded")


def reset_states() -> None:
    """Reset the Silero VAD LSTM hidden state + rolling context."""
    global _state, _context
    if _model is None:
        # No active session, leave ``_state`` as ``None`` so the next
        _state = None
        _context = None
        return
    _state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)
    # ``None`` = no context yet; lazily re-zeroed at the next inference,
    # sized by that call's sample rate.
    _context = None


def reset():
    """Reset the cached model + hidden state (for testing or re-loading)."""
    global _model, _state, _context
    _model = None
    # ``reset_states()`` checks ``_model``: we just set it to None, so
    _state = None
    _context = None
