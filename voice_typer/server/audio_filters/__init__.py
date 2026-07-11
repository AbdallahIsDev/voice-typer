"""Audio filter chain package.

Implements the OBS-inspired filter chain architecture from ADR 0007.
Each filter is an independent class implementing :class:`AudioFilter`.
Filters are composed into a :class:`FilterChain` and applied in order.

Chain order (matches OBS best practice):

    Mic → HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter → ASR

All filters operate on ``float32`` numpy arrays (mono). State is
per-instance and carried across ``process()`` calls. All dynamics
filters use the OBS one-pole envelope smoother:
``coefficient = exp(-1 / (sample_rate * time_seconds))``.
"""

from voice_typer.server.audio_filters.base import (
    ANTIDENORMAL_EPSILON,
    AudioFilter,
    FilterChain,
    db_to_mul,
    mul_to_db,
    one_pole_coeff,
)
from voice_typer.server.audio_filters.compressor import Compressor
from voice_typer.server.audio_filters.equalizer import Equalizer
from voice_typer.server.audio_filters.highpass import HighPassFilter
from voice_typer.server.audio_filters.limiter import Limiter
from voice_typer.server.audio_filters.noise_gate import NoiseGate
from voice_typer.server.audio_filters.noise_suppressor import NoiseSuppressor
from voice_typer.server.audio_filters.notch import NotchFilter

__all__ = [
    "AudioFilter",
    "FilterChain",
    "db_to_mul",
    "mul_to_db",
    "one_pole_coeff",
    "ANTIDENORMAL_EPSILON",
    "HighPassFilter",
    "NoiseGate",
    "Equalizer",
    "Compressor",
    "Limiter",
    "NotchFilter",
    "NoiseSuppressor",
]
