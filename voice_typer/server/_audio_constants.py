"""Shared audio tunables."""

from __future__ import annotations

from typing import Any

from voice_typer.server._lazy_import import lazy_module

# PERF-COLDSTART: `audio_filters` imports WHISPER_SAMPLE_RATE from here during
# package import, so numpy must stay lazy (tests/test_audio_filters_lazy_imports.py).
np = lazy_module("numpy")

# Whisper models are trained on 16 kHz mono input. Every audio path
WHISPER_SAMPLE_RATE: int = 16000

# Silero VAD accepts only {8000, 16000} Hz input. The recorder uses
SILERO_VAD_SAMPLE_RATES: frozenset[int] = frozenset({8000, 16000})

# RNNoise (the noise-suppression filter) is trained on 48 kHz input.
RNNOISE_SAMPLE_RATE: int = 48000

# Common native microphone sample rates. Used by the microphone-list
NATIVE_MIC_RATES: frozenset[int] = frozenset({8000, 16000, 44100, 48000})

# PortAudio ``blocksize`` literal for the Silero VAD path.
_AUDIO_BLOCKSIZE: int = 512


def scaled_audio_blocksize(native_rate: int) -> int:
    """Rate-scaled PortAudio ``blocksize`` for a recording stream.

    Returns the blocksize that makes each callback chunk represent
    """
    rate = int(native_rate)
    if rate <= 0:
        # Unknown / unresolved rate: fall back to the fixed 512 contract
        return _AUDIO_BLOCKSIZE
    return max(_AUDIO_BLOCKSIZE, int(rate * 0.032))


# Digital-clipping peak threshold shared by the dictation quality analyzer
AUDIO_CLIPPING_THRESHOLD: float = 0.99

# Volume-level agreement between the dictation path and the mic-test path.
AUDIO_LOW_VOLUME_RMS: float = 0.005
AUDIO_SILENCE_RMS: float = 0.0005

# Amplitude below which a sample counts as silence in the recording /
# transcription statistics (the long-standing ``|x| < 0.001`` test).
AUDIO_SILENCE_AMPLITUDE: float = 0.001


def peak_amplitude(audio: Any) -> float:
    """Peak ``|sample|`` without materialising an ``np.abs`` copy."""
    flat = np.asarray(audio).reshape(-1)
    if flat.size == 0:
        return 0.0
    return max(float(flat.max()), -float(flat.min()))


def silence_percent(audio: Any) -> float:
    """Percentage of samples quieter than :data:`AUDIO_SILENCE_AMPLITUDE`.

    Two 1-byte boolean passes (~2 bytes/sample peak) say the same thing as
    ``np.abs(flat) < eps`` while avoiding its full-size float-copy temp
    (~5 bytes/sample) on long recordings.
    """
    flat = np.asarray(audio).reshape(-1)
    if flat.size == 0:
        return 0.0
    below = np.less(flat, AUDIO_SILENCE_AMPLITUDE)
    np.logical_and(below, np.greater(flat, -AUDIO_SILENCE_AMPLITUDE), out=below)
    return float(np.count_nonzero(below) / flat.size * 100)


# ``_teardown_stream`` busy-poll budget + interval. The
_TEARDOWN_CALLBACK_DRAIN_BUDGET_S: float = 0.300
_TEARDOWN_CALLBACK_POLL_INTERVAL_S: float = 0.005


# Default smart-duck polling interval (ms). Moved here from volume_ducker.py to
_DEFAULT_SMART_DUCK_POLL_MS: int = 500
