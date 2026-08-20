"""Base classes and DSP helpers for the audio filter chain."""

from __future__ import annotations

import contextlib
import logging
import math
import threading
from abc import ABC, abstractmethod

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

# module-level logger used by ``FilterChain.process``'s
# except branch. Without it the first filter exception raises
# ``NameError`` (masking the real DSP error). Submodules inherit
# this logger name (``voice_typer.server.audio_filters.base``).
log = logging.getLogger(__name__)

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

    # per-filter runtime bypass flag. When False, FilterChain.process
    # skips this filter without calling its process method. State (IIR zi,
    # envelope follower, gate openness) survives the bypass window.
    enabled: bool = True

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
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
    """Ordered list of :class:`AudioFilter` instances.

    Audio flows through each filter in order. If any filter returns
    ``None`` (buffering), the chain returns ``None`` immediately —
    callers should skip the chunk.

     (lock-free process): ``process()`` snapshots the filter list
    under the lock, releases the lock, then runs the filters lock-free.
    Filter ``process()`` calls are pure CPU (no shared-state mutation
    that needs the chain lock) — holding the lock for the duration of
    the chain serializes the audio thread against config-rebuild
    ``swap()`` calls even when no rebuild is happening. The snapshot
    pattern means a rebuild sees the next ``process()`` call (no
    mid-chunk swap), but the current chunk runs without contending
    the lock. ``swap()`` uses an atomic reference swap (single
    ``self._filters = ...`` assignment under the lock — microseconds)
    so the worst-case blocking window is the snapshot copy time
    (``list(self._filters)`` is O(n_filters), typically <7).

    Introspection properties (``filter_names``, ``is_degraded``, etc.)
    take the lock and snapshot the list, then read filter attributes
    lock-free — same rationale (filter attribute reads are atomic).
    """

    def __init__(self, filters: list[AudioFilter] | None = None) -> None:
        self._filters: list[AudioFilter] = list(filters) if filters else []
        self._lock = threading.Lock()

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        """Run audio through all filters in order.

        any exception raised by a filter is logged and the
        chain returns ``None`` (drop the chunk) so a buggy filter
        doesn't crash the recording thread. Pre-fix the bare
        ``raise`` masked the underlying DSP error with a ``NameError``
        on the missing ``log`` symbol.

        snapshot the filter list under the lock, release the
        lock, then run the filters lock-free. The audio thread is the
        only ``process()`` caller; ``swap()`` (the only mutator of
        ``_filters``) takes the lock briefly to swap the reference.
        The snapshot is a fresh list so a concurrent ``swap()`` cannot
        mutate the array we're iterating — but a ``swap()`` that lands
        during ``process()`` only affects the NEXT ``process()`` call
        (the current chunk finishes on the old filter list). This is
        the desired semantics: a chunk is processed by exactly one
        filter list, never a mix.
        """
        # Snapshot under the lock — O(n_filters), typically <7 elements.
        with self._lock:
            filters_snapshot = list(self._filters)
        # Run lock-free. Filter.process() implementations are
        # thread-safe w.r.t. their own internal state (each filter is
        # only ever called from this audio thread), so no lock is
        # needed for the actual DSP work.
        for f in filters_snapshot:
            if audio is None or audio.size == 0:
                return audio
            # skip disabled filters without calling process() so
            # internal state survives the bypass window.
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
        # ``reset()`` implementations only mutate the filter's own
        # state (zero ``zi`` arrays, reset envelope followers) — no
        # cross-filter shared state, so no lock needed for the resets.
        with self._lock:
            filters_snapshot = list(self._filters)
        for f in filters_snapshot:
            with contextlib.suppress(Exception):
                f.reset()

    @property
    def filters(self) -> list[AudioFilter]:
        """List of filters in chain order (copy)."""
        # snapshot under the lock — returns a fresh list the
        # caller can iterate without holding the chain lock.
        with self._lock:
            return list(self._filters)

    @property
    def filter_names(self) -> list[str]:
        """Display names of active filters."""
        # snapshot under the lock, read filter.name lock-free.
        # ``f.name`` is a str attribute read (atomic under GIL).
        with self._lock:
            snapshot = list(self._filters)
        return [f.name for f in snapshot]

    @property
    def is_degraded(self) -> bool:
        """True if any filter is in degraded mode."""
        # snapshot under the lock, read filter.is_degraded
        # lock-free (atomic bool read under GIL).
        with self._lock:
            snapshot = list(self._filters)
        return any(f.is_degraded for f in snapshot)

    @property
    def degraded_reasons(self) -> list[str]:
        """List of degradation reasons from all filters."""
        # snapshot under the lock, read filter attributes
        # lock-free.
        with self._lock:
            snapshot = list(self._filters)
        return [f.degraded_reason for f in snapshot if f.is_degraded]

    @property
    def total_latency_ms(self) -> float:
        """Sum of all filters' latency."""
        # snapshot under the lock, read filter.latency_ms
        # lock-free.
        with self._lock:
            snapshot = list(self._filters)
        return sum(f.latency_ms for f in snapshot)

    def set_filter_enabled(self, name: str, enabled: bool) -> bool:
        """Toggle the ``enabled`` flag on all filters matching ``name``.

        The per-filter ``enabled`` flag (see :attr:`AudioFilter.enabled`)
        is consulted by :meth:`process` — when False, the filter is
        skipped without calling its ``process`` method, so internal
        state (IIR zi, envelope follower, gate openness) survives the
        bypass window. The architectural enabler existed on the ABC
        but had no runtime toggle path — filters could only be
        enabled/disabled via a full chain rebuild (which reloads
        RNNoise and resets all filter state). This method exposes a
        lightweight toggle that preserves filter state across the
        bypass window, so a momentarily-disabled filter re-engages
        with its prior state intact (no transient click from a cold
        IIR re-initialization).

        Thread-safe: snapshots the filter list under the lock
        (consistent with ``process`` / ``reset``), then sets
        ``enabled`` on the matching filter objects lock-free. The
        ``enabled`` attribute is a plain bool read live by ``process``
        (no snapshot), so the toggle takes effect on the NEXT
        ``process`` call — no lock contention with the audio thread.

        Args:
            name: filter display name (e.g. ``"HighPass(80Hz)"``,
                ``"NoiseSuppressor(rnnoise)"``). Matches
                ``filter.name`` on each filter in the chain.
            enabled: True to enable, False to bypass.

        Returns:
            True if at least one filter matched ``name`` and was
            toggled, False otherwise (so callers can detect a no-op
            / typo).
        """
        with self._lock:
            snapshot = list(self._filters)
        matched = False
        for f in snapshot:
            if getattr(f, "name", f.__class__.__name__) == name:
                f.enabled = bool(enabled)
                matched = True
        return matched

    def swap(self, new_filters: list[AudioFilter]) -> None:
        """Atomically swap the filter list. Used for live config rebuilds.

        ``reset()`` is called on each OLD filter so the
        previous session's audio residual is securely cleared (each
        filter's ``reset()`` zeroes its state array in-place via
        ``ndarray.fill(0)``). The original code did this BEFORE the
        swap, under the lock — but the audio thread releases the lock
        BEFORE running the filters, so a concurrent ``process()``
        that had already snapshotted the old list would race with the
        reset regardless of whether the reset is done before or after
        the swap. The reset is therefore moved AFTER the swap
        (outside the lock) for two reasons:

          1. It shortens the lock-hold window in ``swap()`` from
             ``O(n_filters × reset_cost)`` (each reset may zero a
             multi-KB state array) to ``O(1)`` (a single bytecode
             STORE). This matters because ``process()`` snapshots
             under the same lock — a long reset loop would stall the
             audio thread's snapshot for the duration of N resets.
          2. The race window (if any) is unchanged: the audio thread
             releases the lock before running filters in BOTH the old
             and new code, so a reset on the old filters can race
             with an in-flight ``process()`` on the old list in BOTH
             versions. Moving the reset out of the lock doesn't make
             this worse — it just makes the lock window shorter.

        the swap itself is a single atomic reference assignment
        (``self._filters = new_list``) under the lock. The
        ``list(new_filters)`` copy is constructed OUTSIDE the lock so
        the lock is held for microseconds, not for the duration of
        the copy. ``process()`` callers that snapshotted the old list
        before this swap will finish their chunk on the old filters
        (the snapshot is a fresh list, immune to the swap) — the next
        ``process()`` call sees the new list. This is the desired
        semantics: a chunk is processed by exactly one filter list,
        never a mix.

        ``reset()`` is wrapped in ``contextlib.suppress`` because a
        single buggy filter's reset() must not break the swap (the
        new chain has already taken effect).
        """
        # Build the new list first (outside the lock — no contention
        # with concurrent process() callers during the copy).
        new_list = list(new_filters)
        with self._lock:
            old = self._filters
            # Atomic reference swap — single bytecode STORE. Concurrent
            # process() callers see either the old or the new list,
            # never a mix (the GIL serializes the STORE against their
            # snapshot read).
            self._filters = new_list
        # zero state on old filters AFTER the swap (outside
        # the lock — see the docstring for the rationale).
        for f in old:
            with contextlib.suppress(Exception):
                f.reset()


# Cached lazy import of scipy.signal.lfilter.
# Mirrors the _get_resample_poly pattern in audio_processor.py.
_lfilter = None
_lfilter_import_error: Exception | None = None


def _get_lfilter():
    """Return scipy.signal.lfilter, importing it lazily on first call.

    Caches the function reference after the first successful import so the
    hot path (96 calls/sec on the audio worker thread) pays only a module-level
    variable lookup. If the import fails, the error is cached and re-raised on
    every subsequent call so callers see consistent behavior.
    """
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
