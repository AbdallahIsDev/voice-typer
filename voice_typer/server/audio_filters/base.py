"""Audio filter base class."""

from __future__ import annotations

import contextlib
import logging
import math
import threading
from abc import ABC, abstractmethod

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

# module-level logger used by ``FilterChain.process``'s
log = logging.getLogger(__name__)

# Anti-denormal epsilon. Added to IIR filter state to prevent CPU-killing
ANTIDENORMAL_EPSILON: float = 1.0 / 4294967295.0


def db_to_mul(db: float) -> float:
    """Convert decibels to linear amplitude multiplier.

    Returns 0.0 for -inf.
    """
    if db == float("-inf"):
        return 0.0
    return 10.0 ** (db / 20.0)


def mul_to_db(mul: float) -> float:
    """Convert linear amplitude multiplier to decibels.

    Returns -inf for mul <= 0.
    """
    if mul <= 0.0:
        return float("-inf")
    return 20.0 * math.log10(mul)


def one_pole_coeff(sample_rate: int, time_seconds: float) -> float:
    """OBS-style one-pole envelope smoother coefficient."""
    if time_seconds <= 0.0 or sample_rate <= 0:
        return 0.0
    return math.exp(-1.0 / (sample_rate * time_seconds))


class AudioFilter(ABC):
    """Base class for all audio filters in the chain."""

    name: str = "AudioFilter"

    # per-filter runtime bypass flag. When False, FilterChain.process
    enabled: bool = True

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        """Process a chunk of mono float32 audio.

        Returns:
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset internal state. Override if stateful."""
        ...

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
    """Ordered list of :class:`AudioFilter` instances."""

    def __init__(self, filters: list[AudioFilter] | None = None) -> None:
        self._filters: list[AudioFilter] = list(filters) if filters else []
        self._lock = threading.Lock()

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        """Run audio through all filters in order."""
        # Snapshot under the lock. O(n_filters), typically <7 elements.
        with self._lock:
            filters_snapshot = list(self._filters)
        # Run lock-free. Filter.process() implementations are
        for f in filters_snapshot:
            if audio is None or audio.size == 0:
                return audio
            # skip disabled filters without calling process() so
            if not getattr(f, "enabled", True):
                continue
            try:
                result = f.process(audio, sample_rate)
            except Exception as exc:
                log.warning(
                    "audio filter %s raised %s; dropping chunk",
                    getattr(f, "name", f.__class__.__name__),
                    exc,
                )
                return None
            if result is None:
                return None
            audio = result
        return audio

    def reset(self) -> None:
        """Reset all filters' internal state."""
        # snapshot under the lock, reset lock-free. Filter
        with self._lock:
            filters_snapshot = list(self._filters)
        for f in filters_snapshot:
            with contextlib.suppress(Exception):
                f.reset()

    @property
    def filters(self) -> list[AudioFilter]:
        """List of filters in chain order (copy)."""
        # snapshot under the lock, returns a fresh list the
        with self._lock:
            return list(self._filters)

    @property
    def filter_names(self) -> list[str]:
        """Display names of active filters."""
        # snapshot under the lock, read filter.name lock-free.
        with self._lock:
            snapshot = list(self._filters)
        return [f.name for f in snapshot]

    @property
    def is_degraded(self) -> bool:
        """True if any filter is in degraded mode."""
        # snapshot under the lock, read filter.is_degraded
        with self._lock:
            snapshot = list(self._filters)
        return any(f.is_degraded for f in snapshot)

    @property
    def degraded_reasons(self) -> list[str]:
        """List of degradation reasons from all filters."""
        # snapshot under the lock, read filter attributes
        with self._lock:
            snapshot = list(self._filters)
        return [f.degraded_reason for f in snapshot if f.is_degraded]

    @property
    def total_latency_ms(self) -> float:
        """Sum of all filters' latency."""
        # snapshot under the lock, read filter.latency_ms
        with self._lock:
            snapshot = list(self._filters)
        return sum(f.latency_ms for f in snapshot)

    def swap(self, new_filters: list[AudioFilter]) -> None:
        """Atomically swap the filter list. Used for live config rebuilds."""
        # Build the new list first (outside the lock, no contention
        new_list = list(new_filters)
        with self._lock:
            old = self._filters
            # Atomic reference swap, single bytecode STORE. Concurrent
            self._filters = new_list
        # zero state on old filters AFTER the swap (outside
        for f in old:
            with contextlib.suppress(Exception):
                f.reset()


# Cached lazy import of scipy.signal.lfilter.
_lfilter = None
_lfilter_import_error: Exception | None = None
_sosfilt = None
_sosfilt_import_error: Exception | None = None
_sosfilt_zi = None
_sosfilt_zi_import_error: Exception | None = None


def _get_sosfilt():
    """Return scipy.signal.sosfilt, importing it lazily on first call."""
    global _sosfilt, _sosfilt_import_error
    if _sosfilt is not None:
        return _sosfilt
    if _sosfilt_import_error is not None:
        raise _sosfilt_import_error
    try:
        from scipy.signal import sosfilt as _sf

        _sosfilt = _sf
    except ImportError as exc:
        _sosfilt_import_error = exc
        raise
    return _sosfilt


def _get_sosfilt_zi():
    """Return scipy.signal.sosfilt_zi, importing it lazily on first call."""
    global _sosfilt_zi, _sosfilt_zi_import_error
    if _sosfilt_zi is not None:
        return _sosfilt_zi
    if _sosfilt_zi_import_error is not None:
        raise _sosfilt_zi_import_error
    try:
        from scipy.signal import sosfilt_zi as _szi

        _sosfilt_zi = _szi
    except ImportError as exc:
        _sosfilt_zi_import_error = exc
        raise
    return _sosfilt_zi


def _get_lfilter():
    """Return scipy.signal.lfilter, importing it lazily on first call."""
    global _lfilter, _lfilter_import_error
    if _lfilter is not None:
        return _lfilter
    if _lfilter_import_error is not None:
        raise _lfilter_import_error
    try:
        from scipy.signal import lfilter as _lf

        _lfilter = _lf
        return _lf
    except ImportError as exc:
        _lfilter_import_error = exc
        raise
