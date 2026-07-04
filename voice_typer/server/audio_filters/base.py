"""Base classes and DSP helpers for the audio filter chain."""

from __future__ import annotations

import math
import threading
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

# Anti-denormal epsilon. Added to IIR filter state to prevent CPU-killing
# denormal floats (copied from OBS Studio's eq-filter.c).
ANTIDENORMAL_EPSILON: float = 1.0 / 4294967295.0


def db_to_mul(db: float) -> float:
    """Convert decibels to linear amplitude multiplier.

    ``db_to_mul(0) == 1.0``, ``db_to_mul(-6) ≈ 0.501``, ``db_to_mul(-60) ≈ 0.001``.
    Returns 0.0 for -inf.
    """
    if db == float("-inf"):
        return 0.0
    return 10.0 ** (db / 20.0)


def mul_to_db(mul: float) -> float:
    """Convert linear amplitude multiplier to decibels.

    ``mul_to_db(1.0) == 0.0``, ``mul_to_db(0.5) ≈ -6.02``.
    Returns -inf for mul <= 0.
    """
    if mul <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(mul)


def one_pole_coeff(sample_rate: int, time_seconds: float) -> float:
    """OBS-style one-pole envelope smoother coefficient.

    ``coefficient = exp(-1 / (sample_rate * time_seconds))``

    Used by Compressor, Limiter, NoiseGate for attack/release ballistics.
    A higher coefficient = slower response (closer to 1.0).
    """
    if time_seconds <= 0.0 or sample_rate <= 0:
        return 0.0
    return math.exp(-1.0 / (sample_rate * time_seconds))


class AudioFilter(ABC):
    """Base class for all audio filters in the chain.

    Subclasses must implement :meth:`process`. State (IIR filter state,
    envelope followers, gate openness) is carried across ``process()``
    calls. Call :meth:`reset` to clear state (e.g. on mic change).
    """

    name: str = "AudioFilter"

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
        """Process a chunk of mono float32 audio.

        Args:
            audio: 1-D float32 array, values in [-1, 1].
            sample_rate: sample rate of the audio (Hz).

        Returns:
            Filtered audio (same shape/dtype), or ``None`` if the filter
            is buffering and has no output yet (e.g. RNNoise needs a
            full 480-sample frame). Callers should propagate ``None``.
        """
        ...

    def reset(self) -> None:
        """Reset internal state. Default: no-op. Override if stateful."""
        pass

    @property
    def latency_ms(self) -> float:
        """Added latency in milliseconds (0 for sample-by-sample filters)."""
        return 0.0

    @property
    def is_degraded(self) -> bool:
        """True if this filter fell back to a degraded mode (e.g. missing library)."""
        return False

    @property
    def degraded_reason(self) -> str:
        """Human-readable reason for degradation, or empty string."""
        return ""


class FilterChain:
    """Ordered list of :class:`AudioFilter` instances.

    Audio flows through each filter in order. If any filter returns
    ``None`` (buffering), the chain returns ``None`` immediately —
    callers should skip the chunk.
    """

    def __init__(self, filters: Optional[list[AudioFilter]] = None) -> None:
        self._filters: list[AudioFilter] = list(filters) if filters else []
        self._lock = threading.Lock()

    def process(self, audio: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
        """Run audio through all filters in order."""
        with self._lock:
            for f in self._filters:
                if audio is None or audio.size == 0:
                    return audio
                result = f.process(audio, sample_rate)
                if result is None:
                    return None
                audio = result
            return audio

    def reset(self) -> None:
        """Reset all filters' internal state."""
        with self._lock:
            for f in self._filters:
                try:
                    f.reset()
                except Exception:
                    pass

    @property
    def filters(self) -> list[AudioFilter]:
        """List of filters in chain order (copy)."""
        with self._lock:
            return list(self._filters)

    @property
    def filter_names(self) -> list[str]:
        """Display names of active filters."""
        with self._lock:
            return [f.name for f in self._filters]

    @property
    def is_degraded(self) -> bool:
        """True if any filter is in degraded mode."""
        with self._lock:
            return any(f.is_degraded for f in self._filters)

    @property
    def degraded_reasons(self) -> list[str]:
        """List of degradation reasons from all filters."""
        with self._lock:
            return [f.degraded_reason for f in self._filters if f.is_degraded]

    @property
    def total_latency_ms(self) -> float:
        """Sum of all filters' latency."""
        with self._lock:
            return sum(f.latency_ms for f in self._filters)

    def swap(self, new_filters: list[AudioFilter]) -> None:
        """Atomically swap the filter list. Used for live config rebuilds."""
        with self._lock:
            old = self._filters
            self._filters = list(new_filters)
        # Reset old filters outside the lock (no-op for stateless)
        for f in old:
            try:
                f.reset()
            except Exception:
                pass
