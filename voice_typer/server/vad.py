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
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# Silero VAD probability threshold — values above this are considered speech
VAD_THRESHOLD = 0.5

# MEM-03: Path to the bundled Silero VAD JIT model (next to this file)
_VAD_MODEL_PATH = Path(__file__).resolve().parent / "silero_vad.jit"

# Lazy-loaded model reference
_model = None
_utils = None


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


def _load_model():
    """Lazily load the Silero VAD model and utils.

    MEM-03: Tries the local bundled ``silero_vad.jit`` first via
    ``torch.jit.load()``. Falls back to ``torch.hub.load()`` if the
    local file is missing (development mode without bundled model).
    Subsequent calls return the cached model immediately.
    """
    global _model, _utils
    if _model is not None:
        return _model, _utils

    try:
        import torch

        # MEM-03: try local bundled model first
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

        # Fallback: torch.hub.load (network download if no local model)
        # ERR-LINT-001 (fix): torch.hub.load writes "Using cache found in..."
        # to STDERR, not STDOUT. redirect_stdout alone doesn't catch it.
        # Redirect BOTH streams to suppress the noisy cache message.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            loaded: Any = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            _model, _utils = loaded
        log.info("[VAD] Silero VAD model loaded via torch.hub")
        return _model, _utils
    except Exception as exc:
        log.warning("[VAD] Failed to load Silero VAD model: %s", exc)
        _model = None
        _utils = None
        return None, None


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

        # VAD-001 + AUDIO-10: Silero VAD requires exactly 512 samples at
        # 16kHz (or 256 at 8kHz). PortAudio may deliver chunks of arbitrary
        # size (e.g. 1136 on WASAPI, 480 on CoreAudio).
        #
        # VAD-001 (prior fix): padded/truncated to 512 samples — but
        # *truncation* discarded up to 55% of each chunk (624 of 1136
        # samples on WASAPI), making VAD miss speech that occurred in the
        # latter half of the chunk.
        #
        # AUDIO-10 (this fix): slice the input into multiple 512-sample
        # sub-chunks, run the model on each, and take the MAX probability
        # (speech is an "any sub-chunk contains it" decision — max is more
        # sensitive than mean for short speech bursts). Sub-chunks are fed
        # sequentially through the SAME model instance so the Silero LSTM
        # hidden state advances naturally across the chunk boundary.
        # Short trailing remainder (< 512 samples) is dropped — at 16 kHz
        # that's ≤31 ms of audio, below the Silero false-negative floor.
        _expected_samples = {16000: 512, 8000: 256}
        expected = _expected_samples.get(sample_rate, 512)
        n = audio_tensor.shape[0]

        if n < expected:
            # Pad short chunks to expected size (preserves VAD-001 behavior
            # for the small-chunk case — no sub-chunking possible).
            padding = torch.zeros(expected - n)
            audio_tensor = torch.cat([audio_tensor, padding])
            with torch.no_grad():
                prob = model(audio_tensor, sample_rate).item()
            return prob

        if n == expected:
            # Exact fit — single inference, no slicing overhead.
            with torch.no_grad():
                prob = model(audio_tensor, sample_rate).item()
            return prob

        # n > expected: slice into sub-chunks of `expected` samples.
        # range(0, n - expected + 1, expected) yields start indices for
        # full sub-chunks only (drops the trailing remainder).
        probs: list[float] = []
        with torch.no_grad():
            for start in range(0, n - expected + 1, expected):
                sub = audio_tensor[start : start + expected]
                probs.append(float(model(sub, sample_rate).item()))

        if not probs:
            # Defensive: should not happen since n > expected guarantees
            # at least one full sub-chunk, but keep the fallback to be safe.
            with torch.no_grad():
                prob = model(audio_tensor[:expected], sample_rate).item()
            return prob

        # Max probability across sub-chunks — speech is a "any segment
        # has it" decision. Mean would under-report short speech bursts
        # that occupy only one sub-chunk of a multi-sub-chunk input.
        return max(probs)
    except Exception as exc:
        log.debug("[VAD] Inference failed: %s", exc)
        return None


def is_speech(audio_chunk: np.ndarray, sample_rate: int = 16000) -> bool:
    """Determine if an audio chunk contains speech.

    Returns True if the VAD probability exceeds the threshold,
    False otherwise.  Falls back to a simple RMS-based check
    if VAD is unavailable.
    """
    if len(audio_chunk) == 0:
        return False

    prob = compute_vad_prob(audio_chunk, sample_rate)
    if prob is not None:
        return prob > VAD_THRESHOLD

    # Fallback: simple RMS energy check if VAD is unavailable
    rms = float(np.sqrt(np.mean(audio_chunk**2)))
    return rms > 0.01


def reset():
    """Reset the cached model (for testing or if model needs re-loading)."""
    global _model, _utils
    _model = None
    _utils = None
