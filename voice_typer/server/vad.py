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

MEM-03: The model is now bundled locally as ``silero_vad.jit`` (next to
this file) and loaded via ``torch.jit.load()`` instead of
``torch.hub.load()``. This eliminates the network dependency on GitHub
at first-use time and ensures the PyInstaller bundle is self-contained.
Falls back to ``torch.hub.load()`` if the local model is missing
(e.g. development mode without the bundled file, or a fresh git clone).
"""

import contextlib
import io
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# Silero VAD probability threshold — values above this are considered speech.
# XV-51: this is now a *fallback default* used only when callers don't pass
# their own threshold. Upstream callers (VadProcessor) pass a config-derived
# value via ``is_speech(..., threshold=...)`` so the VAD module doesn't
# impose its own threshold on the rest of the pipeline.
VAD_THRESHOLD = 0.5

# XV-49: deadline for the ``torch.hub.load`` network fallback. On offline
# or firewalled machines the call retries for 30+ seconds before failing;
# bounding it to 5s keeps the audio worker responsive and lets the
# negative-cache path take over quickly.
_HUB_LOAD_TIMEOUT_S: float = 5.0

# MEM-03: Path to the bundled Silero VAD JIT model (next to this file)
_VAD_MODEL_PATH = Path(__file__).resolve().parent / "silero_vad.jit"

# Lazy-loaded model reference
_model = None
_utils = None

# XV-49: negative cache for the ``torch.hub.load`` fallback. Once a hub
# load has timed out or failed, subsequent calls short-circuit to ``None``
# instead of re-attempting the (slow) network fetch on every audio chunk.
# Reset by ``reset()`` / ``unload()`` so a future ``preload()`` can retry.
_hub_load_failure_cached: bool = False


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
    without ever touching GitHub. Returns False if either:

      * torch is not importable (VAD entirely unavailable), or
      * the bundled ``silero_vad.jit`` is missing — in which case
        ``_load_model`` falls back to ``torch.hub.load``, which fails on an
        offline / firewalled machine and silently degrades to RMS with no
        warning at load time (detectable only via a debug log).

    MEM-03: this is the helper the issue asked for — called once at
    startup so the app can surface a warning when VAD will be unavailable
    *before* the first dictation, rather than failing silently. It does a
    filesystem stat only (no model load, no network), so it is safe to call
    from ``RecordingController.__init__`` on the startup path.
    """
    if not is_available():
        return False
    return _VAD_MODEL_PATH.exists()


def _hub_load_blocking(torch_module: Any) -> Any:
    """Blocking ``torch.hub.load`` call.

    XV-49: factored out so it can be run inside a ``ThreadPoolExecutor``
    with a deadline. On offline / firewalled machines the underlying
    ``urllib`` retry loop can block the audio worker for 30+ seconds;
    bounding it keeps the worker responsive and lets the negative-cache
    path take over quickly.
    """
    # ERR-LINT-001 (fix): torch.hub.load writes "Using cache found in..."
    # to STDERR, not STDOUT. redirect_stdout alone doesn't catch it.
    # Redirect BOTH streams to suppress the noisy cache message.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return torch_module.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )


def _load_model():
    """Lazily load the Silero VAD model and utils.

    MEM-03: Tries the local bundled ``silero_vad.jit`` first via
    ``torch.jit.load()``. Falls back to ``torch.hub.load()`` if the
    local file is missing (development mode without bundled model).
    Subsequent calls return the cached model immediately.

    XV-49: the hub fallback is wrapped in a ``ThreadPoolExecutor`` with
    a 5-second deadline and is negative-cached on timeout / failure so
    subsequent audio chunks don't re-attempt the slow network fetch.
    """
    global _model, _utils, _hub_load_failure_cached
    if _model is not None:
        return _model, _utils
    if _hub_load_failure_cached:
        # XV-49: prior hub load already failed — don't re-attempt on every
        # audio chunk. ``unload()`` / ``reset()`` clear this so a future
        # ``preload()`` can retry after the user's environment changes.
        return None, None

    try:
        import torch
    except ImportError:
        log.warning("[VAD] torch not importable — Silero VAD disabled")
        return None, None

    # MEM-03: try local bundled model first (no network).
    if _VAD_MODEL_PATH.exists():
        try:
            log.debug("[VAD] Loading local Silero VAD model from %s", _VAD_MODEL_PATH)
            _model = torch.jit.load(str(_VAD_MODEL_PATH))
            _model.eval()
            _utils = None  # JIT model bundles everything, no utils needed
            log.info("[VAD] Silero VAD model loaded from local file")
            return _model, _utils
        except Exception as local_exc:
            log.debug("[VAD] Local model load failed: %s — falling back to hub", local_exc)
            _model = None

    # XV-49: hub fallback with deadline + negative cache.
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_hub_load_blocking, torch)
            loaded = future.result(timeout=_HUB_LOAD_TIMEOUT_S)
        _model, _utils = loaded
        log.info("[VAD] Silero VAD model loaded via torch.hub")
        return _model, _utils
    except FuturesTimeoutError:
        log.warning(
            "[VAD] torch.hub.load timed out after %.1fs (offline or firewalled?) "
            "— negative-caching; Silero VAD disabled until reset()",
            _HUB_LOAD_TIMEOUT_S,
        )
        _hub_load_failure_cached = True
        return None, None
    except Exception as exc:
        log.warning(
            "[VAD] torch.hub.load failed: %s — negative-caching; Silero VAD "
            "disabled until reset()",
            exc,
        )
        _hub_load_failure_cached = True
        return None, None


def _reflect_pad_to(chunk: np.ndarray, expected: int) -> np.ndarray:
    """Reflect-pad a 1-D audio chunk to ``expected`` samples.

    XV-45: zero-padding short chunks is out-of-distribution for Silero
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
    return np.concatenate([chunk, reflect]).astype(out_dtype)


def compute_vad_prob(audio_chunk: np.ndarray, sample_rate: int = 16000) -> float | None:
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

    XV-45: short chunks are reflect-padded (not zero-padded) to stay
    in-distribution for the Silero LSTM and avoid false negatives.

    XV-41 / XV-44: long chunks are sliced into 512-sample sub-chunks
    and the model is run on each. The MAX probability is returned —
    speech is an "any sub-chunk contains it" decision, so max is more
    sensitive than mean for short bursts. (Cost is bounded by the
    worker-thread context per RT-SAFE-001 — VAD no longer runs on the
    audio callback, so N× inference is acceptable.)
    """
    model, utils = _load_model()
    if model is None:
        return None

    try:
        import torch

        # Silero expects a 1D float32 tensor
        audio_tensor = torch.from_numpy(audio_chunk).float()
        if audio_tensor.dim() > 1:
            audio_tensor = audio_tensor.squeeze()

        _expected_samples = {16000: 512, 8000: 256}
        expected = _expected_samples.get(sample_rate, 512)
        n = audio_tensor.shape[0]

        # XV-45: reflect-pad short chunks BEFORE inference (zero-padding
        # is out-of-distribution for Silero and under-reports speech).
        if n < expected:
            padded = _reflect_pad_to(audio_chunk.astype(np.float32, copy=False), expected)
            audio_tensor = torch.from_numpy(padded).float()
            if audio_tensor.dim() > 1:
                audio_tensor = audio_tensor.squeeze()
            n = expected

        if n == expected:
            # Exact fit — single inference, no slicing overhead.
            with torch.no_grad():
                return model(audio_tensor, sample_rate).item()

        # XV-41 / XV-44: multi-sub-chunk path. Run the model on each
        # full 512-sample sub-chunk and take MAX — speech is an "any
        # sub-chunk contains it" decision. The trailing remainder
        # (< 512 samples) is dropped: at 16 kHz that's ≤31 ms of audio,
        # below the Silero false-negative floor.
        num_sub = n // expected
        probs: list[float] = []
        with torch.no_grad():
            for i in range(num_sub):
                sub = audio_tensor[i * expected:(i + 1) * expected]
                probs.append(model(sub, sample_rate).item())
        return max(probs) if probs else 0.0
    except Exception as exc:
        log.debug("[VAD] Inference failed: %s", exc)
        return None


def is_speech(
    audio_chunk: np.ndarray,
    sample_rate: int = 16000,
    threshold: float | None = None,
) -> bool:
    """Determine if an audio chunk contains speech.

    Returns True if the VAD probability exceeds the threshold,
    False otherwise.  Falls back to a simple RMS-based check
    if VAD is unavailable.

    XV-51: ``threshold`` lets upstream callers (e.g. ``VadProcessor``)
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
    # XV-51: the RMS-fallback energy floor (0.01) is intentionally NOT
    # derived from ``effective_threshold`` — the two are in different
    # units (probability vs. linear amplitude) and conflating them
    # would silently change semantics for the rare RMS path. A future
    # refactor could expose this as a separate config field.
    rms = float(np.sqrt(np.mean(audio_chunk**2)))
    return rms > 0.01


def preload() -> bool:
    """Eagerly load + warm up the Silero VAD model.

    XV-43: lazy-loading on the first audio chunk caused 150-600ms of
    initial dropout (model load from disk + JIT graph compile). Call
    this from a startup background thread (e.g. ``Recorder.__init__``
    or app startup) so the model is hot by the time the first audio
    chunk arrives.

    XV-51-adjacent (warmup): the first ``model()`` call after load
    JIT-compiles the graph (~50-200ms on CPU). Pre-warming with a
    zero tensor moves that cost off the first real audio chunk.

    Returns:
        True if the model is loaded (and warm-up inference succeeded),
        False if VAD is unavailable or warm-up failed. Safe to call
        multiple times — the cached model is returned immediately on
        subsequent calls.
    """
    model, _ = _load_model()
    if model is None:
        return False
    try:
        import torch

        # Silero expects 512 samples at 16kHz for warmup.
        dummy = torch.zeros(512, dtype=torch.float32)
        with torch.no_grad():
            model(dummy, 16000)
    except Exception:
        log.debug("[VAD] warmup inference failed", exc_info=True)
        return False
    log.info("[VAD] Silero VAD model preloaded + warmed")
    return True


def unload() -> None:
    """Release the Silero VAD model from memory.

    XV-50: VAD can be disabled mid-session (user toggles all noise
    filters off via the "Off" audio preset). Without unload, the ~2MB
    Silero model stays pinned in RAM for the lifetime of the process.
    This drops the reference so Python can GC it. Safe to call when
    VAD is already unloaded (no-op).

    Also clears the ``torch.hub.load`` negative cache (XV-49) so a
    future ``preload()`` retries the hub load rather than persistently
    refusing after one transient failure.
    """
    global _model, _utils, _hub_load_failure_cached
    _model = None
    _utils = None
    _hub_load_failure_cached = False
    log.info("[VAD] Silero VAD model unloaded")


def reset_states() -> None:
    """Reset the Silero VAD LSTM hidden state.

    XV-46: Silero VAD is an LSTM — its hidden state accumulates across
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
    """Reset the cached model (for testing or if model needs re-loading).

    XV-49: also clears the ``torch.hub.load`` negative cache so test
    isolation is preserved across modules that exercise the hub fallback.
    """
    global _model, _utils, _hub_load_failure_cached
    _model = None
    _utils = None
    _hub_load_failure_cached = False
