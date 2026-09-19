"""Audio format helpers for Recorder (mono downmix + resample wrappers).

Importing this module must NOT pull numpy eagerly (cold-start).
NOTE: see docs/code-notes/recording.md#resampling-fir
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voice_typer.server.recording.resampling import resample_audio

if TYPE_CHECKING:
    from .recorder import Recorder


def ensure_mono(recorder: Recorder, audio: Any) -> Any:
    """Convert multi-channel audio to mono by averaging channels."""
    if audio.ndim == 1:
        return audio
    if audio.ndim == 2 and audio.shape[1] > 1:
        n = audio.shape[0]
        if audio.shape[1] == 2:
            out = np.empty(n, dtype=np.float32)
            np.add(audio[:, 0], audio[:, 1], out=out)
            out *= 0.5
            return out
        return np.mean(audio, axis=1, dtype=np.float32)
    if audio.ndim == 2 and audio.shape[1] == 1:
        return audio.reshape(-1)
    return audio.reshape(-1)


def resample_chunk(recorder: Recorder, audio: Any, effective_sr: int, target_sr: int) -> Any:
    """Resample one chunk via shared ``resample_audio``."""
    if len(audio) == 0:
        return np.array([], dtype=np.float32)
    return resample_audio(audio, effective_sr, target_sr, log_resample=False)


def prepare_audio(
    recorder: Recorder,
    audio: Any,
    effective_sr: int,
    log_resample: bool = True,
) -> Any:
    """Convert captured audio to the configured sample rate."""
    target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate
    if effective_sr != target_sr and len(audio) > 0:
        return resample_audio(audio, effective_sr, target_sr, log_resample=log_resample)
    return audio


# Lazy numpy proxy. MUST stay lazy (cold-start). noqa: E402 = post-hoc import.
from voice_typer.server._lazy_import import lazy_module  # noqa: E402

np = lazy_module("numpy")
