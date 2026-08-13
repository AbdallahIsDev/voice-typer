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

The model is bundled locally as ``silero_vad.onnx`` (next to this file)
and loaded via ``onnxruntime.InferenceSession``. This keeps the app fully
offline (no GitHub fetch at first-use time) and ensures the PyInstaller
bundle is self-contained. If the bundled file is missing or the load
fails, ``_load_model`` logs an ERROR and returns ``(None, None)`` so VAD
degrades to the RMS energy fallback (already handled by callers). No
network call is ever made — the offline guarantee (C-DATA-1) is preserved.

CRITICAL — hidden-state threading (companion §2.2):
    ``onnxruntime.InferenceSession`` is **stateless**. The Silero v4
    ONNX export takes ``(input, state, sr)`` as inputs and returns
    ``(output, stateN)`` — the caller must hold the LSTM hidden-state
    buffer (shape ``(2, 1, 128)`` float32) and thread it through every
    ``compute_vad_prob`` call, re-zeroing it on ``reset_states()``,
    ``unload()``, and first load. If the state is not threaded
    correctly, VAD probabilities become garbage after the first
    512-sample window.
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
# does NOT cache a failure (``_model`` stays None), so when VAD is
# permanently unavailable (onnxruntime missing / bundled model missing or
# corrupt) every 16 Hz audio chunk re-attempts the load and re-logs the
# error — ~960 lines/minute of identical ERROR spam. The first
# occurrence logs at the configured level (with full context); repeats
# drop to DEBUG with a 60s INFO suppression summary
# (``log_rate_limited``), so the operator still sees the chronic
# condition without the wall of noise. ``every_n`` for the load-failure
# path is sized so a transient hiccup surfaces again quickly (16 Hz
# chunks → every 225 = ~14s), while the stable-conditions (onnxruntime
# missing, file missing) use first-only (``every_n=0``).
_VAD_FAILURE_FIRST_ONLY_EVERY_N: int = 0  # first occurrence only
_VAD_LOAD_FAILED_EVERY_N: int = 225  # every ~14s at 16 Hz



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
# and we skip further ORT inference cycles. The threshold is chosen
# above the typical +50 dB speech-prob ceiling so the early-exit only
# fires on high-confidence speech frames; both sub-chunk probabilities
# in ``test_compute_vad_prob_long_chunk_returns_max`` (0.3 and 0.9)
# are below the threshold so the slicing contract is preserved.
_VAD_EARLY_EXIT_PROB: float = 0.95

# Path to the bundled Silero VAD ONNX model (next to this file).
# The legacy ``silero_vad.jit`` is RETAINED in the repo and the frozen
# bundle until Phase 1c (companion §2.5) — Parakeet + Qwen still need
# torch between Phase 1a and Phase 1c, and the Nuitka flag
# ``--module-parameter=torch-disable-jit=no`` (C-CI-8 / NU-106) stays
# until then. Do NOT delete the .jit file or the .jit MANIFEST.in entry
# in Phase 1a.
_VAD_MODEL_PATH = Path(__file__).resolve().parent / "silero_vad.onnx"

# Silero v4 LSTM hidden-state shape — see the module docstring's
# "hidden-state threading" note. The buffer is hoisted to module level
# (one per process) so every ``compute_vad_prob`` call threads the
# running state forward, exactly mirroring the JIT-era model's internal
# ``_model.reset_states()`` / stateful ``_model(input, sr)`` semantics.
_VAD_STATE_SHAPE: tuple[int, int, int] = (2, 1, 128)

# Lazy-loaded ORT session reference. Stays ``None`` until ``_load_model``
# succeeds; ``unload()`` resets it to ``None`` so Python can GC the
# session and ORT can free the (CPU-only) arena.
_model = None

#: The LSTM hidden-state buffer threaded across ``compute_vad_prob``
#: calls. Re-zeroed on ``reset_states()``, ``unload()``, and first load.
#: Module-level (single per process) because the JIT model's internal
#: state was also per-process — preserving that semantics keeps the
#: call-site contract unchanged.
_state = None  # initialized lazily in _load_model to avoid eager numpy use

# ORT I/O names discovered at load time. Silero v4 ONNX uses non-default
# names (``input`` / ``state`` / ``sr`` → ``output`` / ``stateN``); the
# discovery falls back to the first available name if the export variant
# ever changes.
_input_name: str | None = None
_state_name: str | None = None
_sr_name: str | None = None
_output_name: str | None = None
_state_out_name: str | None = None

#: Guards the one-time ``[VAD] Silero VAD model preloaded + warmed``
#: INFO line. ``preload()`` is invoked from BOTH the app-startup
#: background thread (app.py) AND the startup-sequence prewarm task —
#: without the guard the identical line was logged twice within
#: milliseconds from two threads, cluttering the log with a duplicate.
_preload_warmed_logged: bool = False


def is_available() -> bool:
    """Check if Silero VAD can be loaded (onnxruntime + bundled model).

    Companion §2.3.4: replaced the JIT-era ``import torch`` probe with
    an ``onnxruntime`` probe + bundled-file existence check. Returns
    ``True`` only when both (a) ``onnxruntime`` is importable AND (b)
    ``silero_vad.onnx`` exists on disk — so a real ``InferenceSession``
    can be constructed without a network round-trip.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return _VAD_MODEL_PATH.exists()


def _check_vad_available() -> bool:
    """Cheap startup check: is Silero VAD usable WITHOUT a network round-trip?

        Returns True only if onnxruntime is importable AND the bundled
        local model file exists, so ``_load_model`` will succeed via
        ``onnxruntime.InferenceSession`` without ever touching the
        network. Returns False if either:

          * onnxruntime is not importable (VAD entirely unavailable), or
          * the bundled ``silero_vad.onnx`` is missing — in which case
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
        discovered at load time. ``(None, None)`` on any failure path
        — preserves the JIT-era 2-tuple contract so callers (and tests)
        that destructure the result keep working unchanged.

    Companion §2.3.3 — ``providers=["CPUExecutionProvider"]`` is PINNED,
        not defaulted. ORT's default provider list is
        ``["CUDAExecutionProvider", "CPUExecutionProvider"]`` when
        ``onnxruntime-gpu`` is installed. VAD is CPU-only by design
        (the JIT-era code at vad.py:174-181 explicitly documented this
        and intentionally did NOT probe/move to CUDA). Routing VAD to
        GPU adds GPU→CPU upload latency per 512-sample window and
        breaks the existing latency budget.
    """
    global _model, _state, _input_name, _state_name, _sr_name, _output_name, _state_out_name
    if _model is not None:
        return _model, (_input_name, _state_name, _sr_name, _output_name, _state_out_name)

    try:
        import onnxruntime as _ort
    except ImportError:
        # Rate-limited: the failure is NOT cached (model stays None), so
        # every 16 Hz audio chunk would otherwise re-log this identical
        # WARNING (~960/min). First-only keeps the single diagnostic
        # while suppressing the flood (DEBUG + 60s INFO summary via
        # log_rate_limited).
        log_rate_limited(
            log,
            logging.WARNING,
            "[VAD] onnxruntime not importable — Silero VAD disabled",
            every_n=_VAD_FAILURE_FIRST_ONLY_EVERY_N,
        )
        return None, None

    if not _VAD_MODEL_PATH.exists():
        log_rate_limited(
            log,
            logging.ERROR,
            "[VAD] bundled model not found at %s — Silero VAD disabled; "
            "degrading to RMS fallback (no network fetch is attempted)",
            _VAD_MODEL_PATH,
            every_n=_VAD_FAILURE_FIRST_ONLY_EVERY_N,
        )
        return None, None

    try:
        log.debug("[VAD] Loading local Silero VAD ONNX model from %s", _VAD_MODEL_PATH)
        # Silero VAD is a small LSTM (~2 MB). For 512-sample
        # chunks at 16 Hz, CPU inference (~0.5 ms) is faster than the
        # GPU transfer overhead (~1-2 ms roundtrip). Keep on CPU even
        # when CUDA is available — intentionally NOT probing / moving
        # to CUDA. Other ML paths (parakeet_engine, qwen_engine,
        # transcription) DO probe CUDA because their workloads benefit
        # from it; VAD's small model + tiny per-call tensor size does
        # not. Documented here so a future reader doesn't 'fix' this
        # by adding CUDAExecutionProvider and regressing performance.
        # See companion §2.3.3 for the full rationale.
        session = _ort.InferenceSession(
            str(_VAD_MODEL_PATH),
            providers=["CPUExecutionProvider"],
        )
        # Discover I/O names (Silero v4 ONNX uses non-default names).
        # Fall back to the first available name so the runtime is
        # robust against export variants that rename the slots.
        inputs = {i.name: i for i in session.get_inputs()}
        outputs = {o.name: o for o in session.get_outputs()}
        _input_name = "input" if "input" in inputs else next(iter(inputs))
        _state_name = "state" if "state" in inputs else next(iter(inputs))
        _sr_name = "sr" if "sr" in inputs else None
        _output_name = "output" if "output" in outputs else next(iter(outputs))
        _state_out_name = (
            "stateN" if "stateN" in outputs else next(iter(outputs))
        )
        _model = session
        # Initialize the LSTM hidden state to zeros on first load —
        # see the module docstring's hidden-state threading note.
        # ``_state`` is also re-zeroed by ``reset_states()`` and
        # ``unload()``; this assignment handles the first-load path.
        _state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)
        log.info("[VAD] Silero VAD model loaded from local ONNX file")
        return _model, (_input_name, _state_name, _sr_name, _output_name, _state_out_name)
    except Exception as local_exc:
        # Rate-limited for the same reason as the other failure paths:
        # ``_model = None`` means every 16 Hz chunk retries the load and
        # re-logs. every_n=225 re-surfaces a transient disk hiccup
        # (~14s) while capping a permanently-corrupt-model flood.
        log_rate_limited(
            log,
            logging.ERROR,
            "[VAD] local Silero VAD ONNX model load failed: %s — Silero VAD "
            "disabled; degrading to RMS fallback (no network fetch is attempted)",
            local_exc,
            every_n=_VAD_LOAD_FAILED_EVERY_N,
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

        Done in numpy so the test fake ORT session — which only stubs
        ``InferenceSession.run`` — does not need ``flip`` / ``repeat``
        shims.

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


def _run_one_inference(audio_1d: np.ndarray, sr: int) -> float:
    """Run one ORT forward pass and thread the LSTM hidden state.

    Companion §2.2 — the stateless ``InferenceSession`` cannot hold the
    LSTM hidden state internally (unlike the JIT module). The caller
    (``compute_vad_prob``) holds it at module level via ``_state``;
    this helper passes the current state in, runs the session, and
    stores the returned ``stateN`` back into ``_state`` so the next
    call continues the LSTM sequence.

    Args:
        audio_1d: 1-D float32 numpy array of length ``expected`` (512 at
            16 kHz). Reshaped to ``(1, N)`` before the feed dict — Silero
            v4 ONNX expects a batched input.
        sr: sample rate (passed as a scalar int64 tensor — Silero v4
            ONNX takes ``sr`` as an input).

    Returns:
        The float speech probability from ``output`` (shape ``(1, 1)``).

    Raises:
        Whatever ``session.run`` raises — the caller (``compute_vad_prob``)
        wraps the call in a ``try/except`` that returns ``None`` on
        failure so the RMS fallback fires.
    """
    global _state
    # Silero v4 ONNX expects shape (1, N) — batch dim of 1.
    audio_batched = np.asarray(audio_1d, dtype=np.float32).reshape(1, -1)
    feed: dict[str, np.ndarray] = {
        _input_name: audio_batched,
        _state_name: _state,
    }
    if _sr_name is not None:
        feed[_sr_name] = np.array(sr, dtype=np.int64)
    out = _model.run(None, feed)
    # out[0] = output (shape (1, 1) for a single-window batch).
    # Use ``np.asarray(...).reshape(-1)[0]`` so the indexing is robust
    # against either a (1,1) ndarray or a (1,) ndarray returned by the
    # session.
    prob = float(np.asarray(out[0]).reshape(-1)[0])
    # Thread the new hidden state forward — companion §2.2 says this is
    # the critical step. If we forget, VAD probabilities are garbage
    # after the first 512-sample window.
    _state = np.asarray(out[1], dtype=np.float32)
    return prob


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
        The audio capture thread does NOT run inference. Options (a)
        "drop the multi-sub-chunk loop" and (b) "batch sub-chunks as a
        single 2D tensor" would break the ``test_compute_vad_prob_long_chunk_returns_max``
        contract (pinned call_count=2 for a 1024-sample input sliced into
        two 512-sample sub-chunks) and are NOT applied here — the worker-
        thread context makes N× inference acceptable per  The
        ``_VAD_EARLY_EXIT_PROB`` threshold below provides a partial speed-up:
        once a sub-chunk returns a very-high probability, no further sub-
        chunks are inferred (speech is an "any sub-chunk contains it"
        decision, so a single high-prob sub-chunk is sufficient evidence).

    Hidden-state threading (companion §2.2): each sub-chunk call
        consumes the running ``_state`` and produces a new one — the
        next sub-chunk inherits it. This mirrors the JIT-era model's
        internal stateful behavior. ``reset_states()`` zeros the buffer
        at session boundaries.
    """
    session, _names = _load_model()
    if session is None:
        return None

    try:
        # Silero expects a 1D float32 array. Coerce dtype + squeeze any
        # leftover batch dim so the slicing math below is unambiguous.
        audio = np.asarray(audio_chunk, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        expected = _EXPECTED_SAMPLES.get(sample_rate, 512)
        n = int(audio.shape[0])

        # reflect-pad short chunks BEFORE inference (zero-padding
        # is out-of-distribution for Silero and under-reports speech).
        if n < expected:
            audio = _reflect_pad_to(audio, expected)
            n = expected

        if n == expected:
            # Exact fit — single inference, no slicing overhead.
            return _run_one_inference(audio, sample_rate)

        # multi-sub-chunk path. Run the model on each
        # full 512-sample sub-chunk and take MAX — speech is an "any
        # sub-chunk contains it" decision. The trailing remainder
        # (< 512 samples) is dropped: at 16 kHz that's ≤31 ms of audio,
        # below the Silero false-negative floor.
        #
        # early-exit once a sub-chunk returns a very-high
        # probability. Speech is an "any sub-chunk contains it"
        # decision, so a single high-prob sub-chunk is sufficient
        # evidence — no need to spend further ORT inference cycles on
        # the remaining sub-chunks. The threshold (0.95) is chosen
        # above the typical noise-floor +50 dB speech-prob ceiling so
        # the early-exit only fires on high-confidence speech frames;
        # the test contract (probs 0.3 and 0.9 for the two sub-chunks
        # in ``test_compute_vad_prob_long_chunk_returns_max``) is
        # preserved (both values are below the threshold).
        num_sub = n // expected
        probs: list[float] = []
        for i in range(num_sub):
            sub = audio[i * expected : (i + 1) * expected]
            prob = _run_one_inference(sub, sample_rate)
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
        initial dropout (model load from disk + first ORT graph compile).
        Call this from a startup background thread (e.g. ``Recorder.__init__``
        or app startup) so the model is hot by the time the first audio
        chunk arrives.

    (warmup): the first ``session.run()`` call after load
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
    session, _ = _load_model()
    if session is None:
        return False
    try:
        # Silero expects 512 samples at 16kHz for warmup.
        dummy = np.zeros(512, dtype=np.float32)
        _run_one_inference(dummy, WHISPER_SAMPLE_RATE)
        # warmup pollutes the LSTM hidden state — reset to fresh zeros
        # so the first real audio chunk starts from a clean state (the
        # JIT-era model exposed ``reset_states()`` for the same reason;
        # see companion §2.3.5).
        reset_states()
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
    """Release the Silero VAD session from memory.

    VAD can be disabled mid-session (user toggles all noise
        filters off via the "Off" audio preset). Without unload, the ~2MB
        Silero session stays pinned in RAM for the lifetime of the process.
        This drops the reference so Python can GC it and ORT can free the
        (CPU-only) arena. Safe to call when VAD is already unloaded (no-op).

    Companion §2.3.5: also calls ``reset_states()`` so the LSTM hidden
        buffer is zeroed — a subsequent ``preload()`` / first-chunk load
        starts from a fresh state (otherwise stale state from the prior
        session bleeds into the next).
    """
    global _model
    _model = None
    reset_states()
    log.info("[VAD] Silero VAD model unloaded")


def reset_states() -> None:
    """Reset the Silero VAD LSTM hidden state.

    Silero VAD is an LSTM — its hidden state accumulates across
        chunks. Without reset, state from one session bleeds into the next,
        causing the model to "expect" speech patterns from the prior
        speaker/environment and produce stale probabilities. Call this at
        session boundaries (``Recorder.start()`` via ``VadProcessor.reset()``).

    Companion §2.2 / §2.3.5: under the ORT backend the state lives at
        module level (``_state``) instead of inside the JIT model.
        Re-zeroing it here is the load-bearing reset — every
        ``compute_vad_prob`` call threads whatever ``_state`` currently
        holds into the next session.run, so a non-zeroed state would
        produce garbage probabilities on the first chunk after a
        session boundary.

        No-op if the model isn't loaded (avoids triggering a load just to
        reset state — the model starts with a fresh state on first load).
    """
    global _state
    if _model is None:
        # No active session — leave ``_state`` as ``None`` so the next
        # ``_load_model`` initializes it. Calling ``np.zeros`` here
        # would allocate a buffer that's never read and would mask a
        # "model unloaded" bug as "model loaded with zeroed state".
        _state = None
        return
    _state = np.zeros(_VAD_STATE_SHAPE, dtype=np.float32)


def reset():
    """Reset the cached model + hidden state (for testing or re-loading)."""
    global _model
    _model = None
    # ``reset_states()`` checks ``_model`` — we just set it to None, so
    # call the inline zeroing path here directly to keep the post-condition
    # "``_state`` is None after ``reset()``" honest (matches the JIT-era
    # behavior where ``reset()`` cleared both the cached model and the
    # internal LSTM state).
    global _state
    _state = None
