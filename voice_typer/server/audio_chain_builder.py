"""Filter chain builder — constructs a FilterChain from config."""

from __future__ import annotations

import logging
from typing import Any

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.audio_filters import (
    Compressor,
    Equalizer,
    FilterChain,
    HighPassFilter,
    Limiter,
    NoiseGate,
    NoiseSuppressor,
    NotchFilter,
)
from voice_typer.server.audio_filters.base import AudioFilter

log = logging.getLogger(__name__)


def build_chain(
    config: Any,
    sample_rate: int = WHISPER_SAMPLE_RATE,
    *,
    quiet: bool = False,
) -> FilterChain:
    """Build a FilterChain from the current config.

    Chain order (ADR 0007 §2.1):
        HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter
    (NotchFilter added after HighPass if enabled)

    Each filter is only included if its enable flag is True. Filters
    whose library is missing will set is_degraded=True on the chain.

    Args:
        config: Config-like object with noise_filter_* attributes.
        sample_rate: audio sample rate in Hz.
        quiet: when True, suppress the ``[AUDIO-CHAIN] Built chain``
            INFO line and the NoiseSuppressor backend-init lines
            (passed through to :class:`NoiseSuppressor`). Used when
            the chain is built for a SECONDARY consumer (the
            level-monitor processor) — the primary dictation chain
            already logged the same build for the same config, so a
            second build would otherwise repeat every line.

    Returns:
        A FilterChain ready to process audio.
    """
    filters: list[AudioFilter] = []

    # 1. Notch filter (optional, before high-pass to remove hum early)
    if config.noise_filter_notch:
        notch_freq = config.noise_filter_notch_frequency_hz
        filters.append(
            NotchFilter(
                frequency_hz=notch_freq,
                sample_rate=sample_rate,
            )
        )

    # 2. High-pass filter
    if config.noise_filter_highpass:
        cutoff = config.noise_filter_highpass_cutoff_hz
        filters.append(
            HighPassFilter(
                cutoff_hz=cutoff,
                sample_rate=sample_rate,
            )
        )

    # 3. Noise suppressor (RNNoise / GTCRN)
    method = config.noise_suppression_method
    if method != "none":
        filters.append(
            NoiseSuppressor(
                method=method,
                sample_rate=sample_rate,
                quiet=quiet,
            )
        )

    # 4. Noise gate
    if config.noise_filter_gate:
        filters.append(
            NoiseGate(
                open_threshold_db=config.noise_filter_gate_open_threshold_db,
                close_threshold_db=config.noise_filter_gate_close_threshold_db,
                attack_ms=config.noise_filter_gate_attack_ms,
                hold_ms=config.noise_filter_gate_hold_ms,
                release_ms=config.noise_filter_gate_release_ms,
                sample_rate=sample_rate,
                # opt-in adaptive calibration — gate samples the first
                # ~500ms of audio to estimate noise floor and derives thresholds.
                adaptive=getattr(config, "noise_filter_gate_adaptive", False),
            )
        )

    # 5. Equalizer
    if config.noise_filter_eq:
        filters.append(
            Equalizer(
                low_db=config.noise_filter_eq_low_db,
                mid_db=config.noise_filter_eq_mid_db,
                high_db=config.noise_filter_eq_high_db,
                sample_rate=sample_rate,
            )
        )

    # 6. Compressor
    if config.noise_filter_compressor:
        filters.append(
            Compressor(
                threshold_db=config.noise_filter_compressor_threshold_db,
                ratio=config.noise_filter_compressor_ratio,
                attack_ms=config.noise_filter_compressor_attack_ms,
                release_ms=config.noise_filter_compressor_release_ms,
                output_gain_db=config.noise_filter_compressor_output_gain_db,
                sample_rate=sample_rate,
            )
        )

    # 7. Limiter (always last — brick-wall safety net)
    if config.noise_filter_limiter:
        filters.append(
            Limiter(
                ceiling_db=config.noise_filter_limiter_ceiling_db,
                release_ms=config.noise_filter_limiter_release_ms,
                sample_rate=sample_rate,
            )
        )

    chain = FilterChain(filters)
    if not quiet:
        log.info(
            "[AUDIO-CHAIN] Built chain: %s (latency=%.1fms, degraded=%s)",
            chain.filter_names or "none",
            chain.total_latency_ms,
            chain.is_degraded,
        )
    return chain


def build_chain_from_dict(config_dict: dict, sample_rate: int = WHISPER_SAMPLE_RATE) -> FilterChain:
    """Build a FilterChain from a config dict (for testing).

    Like :func:`build_chain` but accepts a plain dict instead of a
    Config object. Missing keys use the canonical defaults declared on
    class:`voice_typer.server.config.Config` — previously this
    function shadowed ``Config`` defaults with a parallel ``_DEFAULTS``
    dict that drifted whenever a default was bumped on ``Config`` (e.g.
    ``noise_filter_gate_hold_ms`` 150 → 200 in ADR 0007 §5). The dict
    path now constructs a real ``Config()`` and applies the overrides
    via ``setattr`` so there is exactly one source of truth for each
    default.
    """

    from voice_typer.server.config import Config

    cfg = Config()
    for key, value in config_dict.items():
        setattr(cfg, key, value)
    return build_chain(cfg, sample_rate=sample_rate)


def set_filter_enabled(chain: FilterChain, name: str, enabled: bool) -> bool:
    """Toggle a filter's ``enabled`` flag on an existing chain at runtime.

    Thin wrapper around :meth:`FilterChain.set_filter_enabled` so the
    IPC handler layer can toggle filters at runtime without a full
    chain rebuild (which would reload RNNoise and reset all filter
    state). The toggle preserves filter state (IIR zi, envelope
    follower, gate openness) across the bypass window — a
    momentarily-disabled filter re-engages with its prior state
    intact (no transient click from a cold IIR re-initialization).

    The per-filter ``enabled`` flag (see
    :attr:`voice_typer.server.audio_filters.AudioFilter.enabled`) is
    consulted by :meth:`FilterChain.process` — when False, the filter
    is skipped without calling its ``process`` method. The
    architectural enabler existed on the ABC but had no runtime
    toggle path; this function + :meth:`FilterChain.set_filter_enabled`
    expose the server-side API surface for the IPC layer.

    NOTE: the IPC command that exposes this to the renderer (e.g. a
    ``set_audio_filter_enabled`` command in the IPC handler layer) is
    NOT wired in this change — the IPC handler files are owned by a
    different sub-agent. The IPC handler can call this function
    directly with the chain from the active AudioProcessor, or call
    :meth:`AudioProcessor.set_filter_enabled` for convenience.

    Args:
        chain: the live FilterChain (from
            :attr:`AudioProcessor.chain`).
        name: filter display name (e.g. ``"HighPass(80Hz)"``,
            ``"NoiseSuppressor(rnnoise)"``, ``"Compressor"``).
            Matches ``filter.name`` on each filter in the chain.
        enabled: True to enable, False to bypass.

    Returns:
        True if at least one filter matched ``name`` and was
        toggled, False otherwise (so callers can detect a no-op
        / typo).
    """
    return chain.set_filter_enabled(name, enabled)
