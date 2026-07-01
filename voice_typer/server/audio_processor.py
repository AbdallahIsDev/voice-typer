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
from typing import Callable, Optional

import numpy as np

from voice_typer.server.audio_chain_builder import build_chain
from voice_typer.server.audio_filters import FilterChain

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
        self._quality_callback: Optional[QualityCallback] = None
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

    def process_chunk(self, chunk: np.ndarray) -> Optional[np.ndarray]:
        """Apply the filter chain to a single audio chunk.

        Returns the filtered chunk (same shape/dtype), or ``None`` if
        a filter is buffering (e.g. RNNoise needs a full 480-sample
        frame). Callers should propagate ``None`` by skipping the chunk.

        **Must be non-blocking.** Only pre-allocated buffers and fast
        numpy/scipy operations are used.
        """
        if chunk.size == 0:
            return chunk

        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        result = self._chain.process(chunk, self._sample_rate)

        # Quality detection runs on whatever audio we have (filtered or
        # passthrough). Skipped if chain returned None (buffering).
        if result is not None and self._quality_callback is not None:
            self._run_quality_check(result)

        return result if result is not None else chunk

    def _run_quality_check(self, chunk: np.ndarray) -> None:
        """Compute lightweight quality metrics and fire the callback."""
        if chunk.size == 0:
            return
        peak = float(np.max(np.abs(chunk)))
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
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
