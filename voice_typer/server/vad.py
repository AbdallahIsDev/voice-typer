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
"""

import contextlib
import io
import logging
import numpy as np
from typing import Optional

log = logging.getLogger(__name__)

# Silero VAD probability threshold — values above this are considered speech
VAD_THRESHOLD = 0.5

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


def _load_model():
    """Lazily load the Silero VAD model and utils.

    Uses torch.hub to download the model on first use.  Subsequent
    calls return the cached model immediately.
    """
    global _model, _utils
    if _model is not None:
        return _model, _utils

    try:
        import torch
        with contextlib.redirect_stdout(io.StringIO()):
            _model, _utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                trust_repo=True,
            )
        log.info("[VAD] Silero VAD model loaded successfully")
        return _model, _utils
    except Exception as exc:
        log.warning("[VAD] Failed to load Silero VAD model: %s", exc)
        _model = None
        _utils = None
        return None, None


def compute_vad_prob(audio_chunk: np.ndarray, sample_rate: int = 16000) -> Optional[float]:
    """Compute the VAD probability for an audio chunk.

    Args:
        audio_chunk: numpy float32 array of audio samples (16kHz mono)
        sample_rate: sample rate of the audio (default: 16000)

    Returns:
        Probability of speech (0.0–1.0), or None if VAD is unavailable.
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

        with torch.no_grad():
            prob = model(audio_tensor, sample_rate).item()
        return prob
    except Exception as exc:
        log.debug("[VAD] Inference failed: %s", exc)
        return None


def is_speech(audio_chunk: np.ndarray, sample_rate: int = 16000) -> bool:
    """Determine if an audio chunk contains speech.

    Returns True if the VAD probability exceeds the threshold,
    False otherwise.  Falls back to a simple RMS-based check
    if VAD is unavailable.
    """
    prob = compute_vad_prob(audio_chunk, sample_rate)
    if prob is not None:
        return prob > VAD_THRESHOLD

    # Fallback: simple RMS energy check if VAD is unavailable
    if len(audio_chunk) == 0:
        return False
    rms = float(np.sqrt(np.mean(audio_chunk ** 2)))
    return rms > 0.01


def reset():
    """Reset the cached model (for testing or if model needs re-loading)."""
    global _model, _utils
    _model = None
    _utils = None