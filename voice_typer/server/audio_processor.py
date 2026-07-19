"""Real-time audio cleaning for the dictation pipeline.

ADR 0007: This module is now a thin wrapper around the
:class:`~voice_typer.server.audio_filters.FilterChain`. The monolithic
filter methods (high-pass, noise gate, RNNoise, peak normalizer) have
been moved to individual filter classes in
:mod:`voice_typer.server.audio_filters`. The duplicate AGC
(``_apply_normalization`` / ``_agc_update``) has been replaced by the
:class:`~voice_typer.server.audio_filters.Compressor` filter.

The filter chain is rebuilt on every config change via
:meth:`VoiceTyperApp._rebuild_audio_processor` (see ``service.py``),
so Settings UI changes take effect immediately in dictation — no
restart required.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from voice_typer.server.audio_chain_builder import build_chain
from voice_typer.server.audio_filters import FilterChain

# CRIT-6 / AUDIO-CHAIN-1: lazy-imported resampler to avoid pulling scipy
# into every test that constructs an AudioProcessor.  The first
# ``process_chunk`` call that actually needs to resample will import it.
_resample_poly = None
_resample_poly_import_error: Exception | None = None


def _get_resample_poly():
    """Lazy import of scipy.signal.resample_poly.

    The chain is built at ``config.sample_rate`` (16 kHz) but the
    PortAudio stream may run at the device's native rate (48 kHz on
    most mics).  When the rates differ we resample the chunk to the
    chain's rate before filtering so the filter coefficients are
    applied at the correct frequency.
    """
    global _resample_poly, _resample_poly_import_error
    if _resample_poly is not None:
        return _resample_poly
    if _resample_poly_import_error is not None:
        raise _resample_poly_import_error
    try:
        from scipy.signal import resample_poly as _rp  # type: ignore[import-untyped]

        _resample_poly = _rp
        return _resample_poly
    except Exception as exc:  # pragma: no cover - exercised only when scipy missing
        _resample_poly_import_error = exc
        raise


log = logging.getLogger(__name__)

QualityCallback = Callable[[float, float], None]


class AudioProcessor:
    """Real-time audio cleaning via a filter chain.

    Wraps a :class:`FilterChain` built from the current config. The
    chain is rebuilt by :meth:`rebuild_from_config` when settings change.

    The processor is stateful: each filter in the chain maintains its
    own state (IIR filter state, envelope followers, gate openness)
    across ``process_chunk()`` calls. Call :meth:`reset` at the start
    of each recording session.

    Quality callback: :meth:`set_quality_callback` wires a per-chunk
    ``(rms, peak)`` callback for clipping/noise/SNR reporting (feeds
    :class:`~voice_typer.server.audio_quality.AudioQualityAnalyzer`).
    """

    def __init__(self, config: object, sample_rate: int = 16000) -> None:
        self._config = config
        self._sample_rate = int(sample_rate)
        self._chain: FilterChain = build_chain(config, sample_rate)
        self._quality_callback: QualityCallback | None = None
        log.info(
            "[AUDIO-PROC] chain built: %s (latency=%.1fms, degraded=%s)",
            self._chain.filter_names,
            self._chain.total_latency_ms,
            self._chain.is_degraded,
        )

    # ── Lifecycle ───────────────────────────────────────────────────

    def rebuild_from_config(self, config: object) -> None:
        """Rebuild the filter chain from a new config.

        Called by :meth:`VoiceTyperApp._rebuild_audio_processor` when
        any ``noise_filter_*`` config field changes. Atomically swaps
        the chain and resets the old chain's state.
        """
        self._config = config
        new_chain = build_chain(config, self._sample_rate)
        self._chain.swap(new_chain._filters)
        log.info(
            "[AUDIO-PROC] chain rebuilt: %s (degraded=%s)",
            self._chain.filter_names,
            self._chain.is_degraded,
        )

    def reset(self) -> None:
        """Reset all filter states for a new recording session."""
        self._chain.reset()

    def set_quality_callback(self, cb: QualityCallback) -> None:
        """Wire a quality detector callback."""
        self._quality_callback = cb

    # ── Real-time processing (called from PortAudio callback) ───────

    def process_chunk(self, chunk: np.ndarray, input_sample_rate: int | None = None) -> np.ndarray | None:
        """Apply the filter chain to a single audio chunk.

        Returns the filtered chunk (same shape/dtype). If the chain
        returns ``None`` because a downstream filter is buffering (e.g.
        RNNoise needs a full 480-sample frame), the ORIGINAL unfiltered
        chunk is returned so callers that don't propagate ``None``
        (e.g. ``level_monitor``'s live level bar, and the recording
        callback's RMS / silence-detection path) keep running without
        special-casing. The trade-off is a brief temporal misalignment
        of buffered samples during the warm-up window — acceptable for
        a few startup chunks (~30 ms at 16 kHz).

        Callers that need strict ``None`` propagation (none today — the
        only buffering filter is RNNoise, which is opt-in) should
        inspect :attr:`chain` directly via ``chain.process(...)``.

        **Must be non-blocking.** Only pre-allocated buffers and fast
        numpy/scipy operations are used.

        CRIT-6 / AUDIO-CHAIN-1: if ``input_sample_rate`` is provided
        and differs from the chain's construction sample rate, the
        chunk is resampled to the chain's rate before processing.
        This fixes the bug where filters built at 16 kHz were being
        fed 48 kHz audio (the device's native rate), causing a
        nominal 80 Hz high-pass to actually cut at 240 Hz — removing
        male speech fundamentals.  When resampling fails (scipy
        missing, integer ratio not available), we fall back to
        passing the original chunk and log at debug level.
        """
        if chunk.size == 0:
            return chunk

        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        # CRIT-6 / AUDIO-CHAIN-1: resample to the chain's rate if the
        # input rate differs.  Filters were built at ``self._sample_rate``
        # (16 kHz) — feeding them audio at a different rate silently
        # mistunes every coefficient (high-pass, notch, EQ crossovers,
        # compressor attack/release).
        if input_sample_rate is not None and int(input_sample_rate) != self._sample_rate:
            try:
                resample_poly = _get_resample_poly()
                # scipy.signal.resample_poly uses integer up/down ratios.
                # Compute the greatest common divisor to keep the ratio
                # in reduced form (smaller FFT sizes, faster).
                from math import gcd

                up = self._sample_rate
                down = int(input_sample_rate)
                g = gcd(up, down)
                up //= g
                down //= g
                chunk = resample_poly(chunk, up, down).astype(np.float32, copy=False)
            except Exception:
                # Fall back to the original chunk — better to filter at
                # the wrong rate than to drop the chunk entirely.
                log.debug(
                    "[AUDIO-PROC] resample failed (input_sr=%d, chain_sr=%d); filtering at wrong rate",
                    input_sample_rate,
                    self._sample_rate,
                    exc_info=True,
                )

        result = self._chain.process(chunk, self._sample_rate)

        # Quality detection runs on whatever audio we have (filtered or
        # passthrough). Skipped if chain returned None (buffering).
        if result is not None and self._quality_callback is not None:
            self._run_quality_check(result)

        # Option B (E.1 fix, Round 0 forward-port): propagate the filtered
        # result when present, otherwise fall back to the original chunk.
        # True ``None`` propagation would break level_monitor (out of this
        # module's scope) and the recording callback's downstream RMS /
        # silence math, which assume a concrete ndarray. Documented here so
        # future callers know the contract.
        return result if result is not None else chunk

    def _run_quality_check(self, chunk: np.ndarray) -> None:
        """Compute lightweight quality metrics and fire the callback.

        Uses allocation-free reductions: np.max/min for peak (no np.abs
        allocation) and np.dot for RMS (no np.square allocation).
        """
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

    # ── Introspection ───────────────────────────────────────────────

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
        return self._chain.is_degraded

    @property
    def degraded_reasons(self) -> list[str]:
        """List of degradation reasons from all filters."""
        return self._chain.degraded_reasons

    @property
    def total_latency_ms(self) -> float:
        """Total added latency from all filters."""
        return self._chain.total_latency_ms
