"""Audio processor pipeline entry points."""

from __future__ import annotations

import logging
from collections.abc import Callable
from math import gcd

# PERF-COLDSTART-001: lazy numpy; PEP 563 annotations stay strings.
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.audio_chain_builder import AUDIO_CHAIN_CONFIG_FIELDS as _CONFIG_SIGNATURE_FIELDS, build_chain
from voice_typer.server.audio_filters import FilterChain
from voice_typer.server.audio_filters.noise_suppressor import _RNNOISE_FRAME_SIZE

np = lazy_module("numpy")

# CRIT-6: lazy-imported resampler to avoid pulling scipy into every

# Cached lazy import of scipy.signal.upfirdn. The hot path is a single
_upfirdn = None
_upfirdn_import_error: Exception | None = None


def _get_upfirdn():
    """Lazy import of scipy.signal.upfirdn."""
    global _upfirdn, _upfirdn_import_error
    if _upfirdn is not None:
        return _upfirdn
    if _upfirdn_import_error is not None:
        raise _upfirdn_import_error
    try:
        from scipy.signal import upfirdn as _uf

        _upfirdn = _uf
        return _uf
    except ImportError as exc:
        _upfirdn_import_error = exc
        raise


# Cached lazy lookup of ``_get_resample_fir_taps`` from
_resample_fir_taps_fn = None


def _get_resample_fir_taps_fn():
    """Lazy accessor for ``_get_resample_fir_taps`` from resampling.py."""
    global _resample_fir_taps_fn
    if _resample_fir_taps_fn is None:
        from voice_typer.server.recording.resampling import _get_resample_fir_taps

        _resample_fir_taps_fn = _get_resample_fir_taps
    return _resample_fir_taps_fn


# Cached lazy lookup of ``_get_resample_poly`` from
_resample_poly_fn = None


def _get_resample_poly_fn():
    """numpy) until the first ``process_chunk`` call that actually needs"""
    global _resample_poly_fn
    if _resample_poly_fn is None:
        from voice_typer.server.recording.resampling import _get_resample_poly

        _resample_poly_fn = _get_resample_poly
    return _resample_poly_fn


# Cached lazy lookup of ``_resample_via_cached_taps``, the shared
_resample_via_cached_taps_fn = None


def _get_resample_via_cached_taps_fn():
    """Lazy accessor for ``_resample_via_cached_taps`` from resampling.py."""
    global _resample_via_cached_taps_fn
    if _resample_via_cached_taps_fn is None:
        from voice_typer.server.recording.resampling import _resample_via_cached_taps

        _resample_via_cached_taps_fn = _resample_via_cached_taps
    return _resample_via_cached_taps_fn


log = logging.getLogger(__name__)

QualityCallback = Callable[[float, float], None]

# every noise_filter_* / noise_suppression_* / audio_preset
# field the chain consumes. Canonical list lives in audio_chain_builder
# (AUDIO_CHAIN_CONFIG_FIELDS, imported above); this alias is the same
# tuple object, so the rebuild short-circuit can never drift.


def _config_signature(config: object, sample_rate: int) -> tuple:
    """compute a stable signature tuple for ``config``."""
    return (sample_rate,) + tuple(getattr(config, name, None) for name in _CONFIG_SIGNATURE_FIELDS)


class AudioProcessor:
    """across ``process_chunk()`` calls. Call :meth:`reset` at the start"""

    def __init__(
        self,
        config: object,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        *,
        quiet: bool = False,
    ) -> None:
        # ``quiet`` suppresses the ``[AUDIO-CHAIN] Built chain`` /
        self._config = config
        self._sample_rate = int(sample_rate)
        self._chain: FilterChain = build_chain(config, sample_rate, quiet=quiet)
        self._quality_callback: QualityCallback | None = None
        # cache of the config signature that produced the current
        self._config_signature: tuple = _config_signature(config, self._sample_rate)
        # track (input_sr, chain_sr) pairs already logged at
        self._resample_warned_pairs: set[tuple[int, int]] = set()
        self._resample_degraded: bool = False
        self._resample_degraded_reason: str = ""
        # Zero-frame prewarm: run a short silence buffer through the
        self._prewarm_chain()
        if not quiet:
            # Single choke-point is ``build_chain``'s ``[AUDIO-CHAIN]``
            log.debug(
                "[AUDIO-PROC] chain built: %s (latency=%.1fms, degraded=%s)",
                self._chain.filter_names or "none",
                self._chain.total_latency_ms,
                self._chain.is_degraded,
            )

    def _prewarm_chain(self) -> None:
        """Feed one RNNoise frame of silence through the chain."""
        try:
            names = self._chain.filter_names
            if not any("NoiseSuppressor" in n for n in names):
                return
            silence = np.zeros(_RNNOISE_FRAME_SIZE, dtype=np.float32)
            self._chain.process(silence, self._sample_rate)
        except Exception:
            log.debug(
                "[AUDIO-PROC] prewarm failed (non-fatal, chain still usable)",
                exc_info=True,
            )

    def rebuild_from_config(self, config: object) -> None:
        """Rebuild the filter chain from a new config."""
        new_sig = _config_signature(config, self._sample_rate)
        if new_sig == self._config_signature:
            # No-op rebuild -- keep the existing chain (with its live
            self._config = config
            log.debug("[AUDIO-PROC] rebuild skipped -- config signature unchanged")
            return
        self._config = config
        self._config_signature = new_sig
        new_chain = build_chain(config, self._sample_rate)
        # ``filters`` is FilterChain's sanctioned public accessor (a
        self._chain.swap(new_chain.filters)
        # ``build_chain`` above already emitted the single
        log.debug(
            "[AUDIO-PROC] chain rebuilt: %s (degraded=%s)",
            self._chain.filter_names or "none",
            self._chain.is_degraded,
        )

    def reset(self) -> None:
        """Reset all filter states for a new recording session."""
        self._chain.reset()
        # Clear the latched resample-degraded flag, a new
        self._resample_degraded = False
        self._resample_degraded_reason = ""
        self._resample_warned_pairs.clear()

    def set_sample_rate(self, sr: int) -> None:
        """Update the chain's sample rate and rebuild the filter chain."""
        new_sr = int(sr)
        self._sample_rate = new_sr
        self._config_signature = _config_signature(self._config, new_sr)
        new_chain = build_chain(self._config, new_sr)
        # Sanctioned public accessor: see ``rebuild_from_config``.
        self._chain.swap(new_chain.filters)
        # The chain is now tuned to ``new_sr``; if the next chunk
        self._resample_degraded = False
        self._resample_degraded_reason = ""
        self._resample_warned_pairs.clear()
        # Same single-choke-point rule as ``rebuild_from_config``:
        log.debug(
            "[AUDIO-PROC] chain rebuilt on rate change: %s (sr=%d, degraded=%s)",
            self._chain.filter_names or "none",
            new_sr,
            self._chain.is_degraded,
        )

    def set_quality_callback(self, cb: QualityCallback) -> None:
        """Wire a quality detector callback."""
        self._quality_callback = cb

    def process_chunk(self, chunk: np.ndarray, input_sample_rate: int | None = None) -> np.ndarray | None:
        """Apply the filter chain to a single audio chunk.

        Returns the filtered chunk (same shape/dtype). If the chain
        """
        # wrap the entire RT-thread body in a try/except so a
        try:
            return self._process_chunk_impl(chunk, input_sample_rate)
        except Exception:
            log.exception(
                "[AUDIO-PROC] process_chunk raised on RT thread -- returning original chunk unfiltered (input_sr=%s)",
                input_sample_rate,
            )
            try:
                if chunk.dtype != np.float32:
                    return chunk.astype(np.float32)
                return chunk
            except Exception:
                return chunk

    def _process_chunk_impl(self, chunk: np.ndarray, input_sample_rate: int | None) -> np.ndarray | None:
        """Actual chunk processing -- see :meth:`process_chunk` for the contract."""
        if chunk.size == 0:
            return chunk

        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        # CRIT-6: resample to the chain's rate if the
        if input_sample_rate is not None and int(input_sample_rate) != self._sample_rate:
            # resample_poly allocates per call and runs on the RT
            try:
                # Use the shared ``_get_resample_poly`` from
                resample_poly = _get_resample_poly_fn()()
                # scipy.signal.resample_poly uses integer up/down ratios.
                up = self._sample_rate
                down = int(input_sample_rate)
                g = gcd(up, down)
                up //= g
                down //= g
                # use cached FIR taps + upfirdn instead of
                try:
                    taps = _get_resample_fir_taps_fn()(up, down)
                    # ``taps`` is the ``(h_padded, n_pre_remove,
                    chunk = _get_resample_via_cached_taps_fn()(taps, chunk, up, down, _get_upfirdn())
                except Exception:
                    chunk = resample_poly(chunk, up, down).astype(np.float32, copy=False)
                # one-shot WARNING (rate-limited) so operators can
                self._log_resample_once(int(input_sample_rate))
            except Exception:
                # Fall back to the original chunk -- better to filter at
                log.warning(
                    "[AUDIO-PROC] resample failed (input_sr=%d, chain_sr=%d); "
                    "filtering at wrong rate -- call set_sample_rate to retune",
                    input_sample_rate,
                    self._sample_rate,
                    exc_info=True,
                )
                self._resample_warned_pairs.add((int(input_sample_rate), int(self._sample_rate)))
                # Latch the resample-degraded flag so the UI can
                if not self._resample_degraded:
                    self._resample_degraded = True
                    self._resample_degraded_reason = (
                        f"resample failed (input_sr={int(input_sample_rate)}, "
                        f"chain_sr={self._sample_rate}), filtering at wrong rate; "
                        "call set_sample_rate(input_sr) to retune"
                    )

        # run the quality check on the PRE-filter chunk (the
        if self._quality_callback is not None:
            self._run_quality_check(chunk)

        result = self._chain.process(chunk, self._sample_rate)

        # Option B (E.1 fix, Round 0 forward-port): propagate the filtered
        return result if result is not None else chunk

    def _run_quality_check(self, chunk: np.ndarray) -> None:
        """Compute lightweight quality metrics and fire the callback."""
        if chunk.size == 0:
            return
        flat = chunk.ravel()
        peak = max(float(flat.max()), -float(flat.min()))
        rms = float(np.sqrt(np.dot(flat, flat) / flat.size))
        try:
            if self._quality_callback is not None:
                self._quality_callback(rms, peak)
        except Exception:
            log.debug("[AUDIO-PROC] quality callback raised", exc_info=True)

    def _log_resample_once(self, input_sr: int) -> None:
        """log the resample fallback at WARNING once per (input_sr, chain_sr) pair."""
        pair = (int(input_sr), int(self._sample_rate))
        if pair in self._resample_warned_pairs:
            log.debug(
                "[AUDIO-PROC] resample ongoing (input_sr=%d, chain_sr=%d)",
                input_sr,
                self._sample_rate,
            )
            return
        self._resample_warned_pairs.add(pair)
        log.warning(
            "[AUDIO-PROC] resampling on RT thread (input_sr=%d, chain_sr=%d) -- "
            "call set_sample_rate(input_sr) to retune the chain and skip resampling",
            input_sr,
            self._sample_rate,
        )

    @property
    def sample_rate(self) -> int:
        """The chain's current sample rate (Hz)."""
        return self._sample_rate

    @property
    def chain(self) -> FilterChain:
        """The current filter chain."""
        return self._chain

    @property
    def filter_names(self) -> list[str]:
        """Display names of active filters."""
        return self._chain.filter_names

    @property
    def is_degraded(self) -> bool:
        """True if any filter is in degraded mode (missing library, etc.)."""
        return self._chain.is_degraded or self._resample_degraded

    @property
    def degraded_reasons(self) -> list[str]:
        """List of degradation reasons from all filters and the resample fallback."""
        reasons = list(self._chain.degraded_reasons)
        # Append the resample-degraded reason LAST so the UI
        if self._resample_degraded and self._resample_degraded_reason:
            reasons.append(self._resample_degraded_reason)
        return reasons

    @property
    def total_latency_ms(self) -> float:
        """Total added latency from all filters."""
        return self._chain.total_latency_ms
