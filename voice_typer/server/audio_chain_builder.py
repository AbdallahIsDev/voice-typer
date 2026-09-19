"""Audio filter-chain builder."""

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
    """Build a FilterChain from the current config."""
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
                # opt-in adaptive calibration, gate samples the first
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

    # 7. Limiter (always last, brick-wall safety net)
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
    """Build a FilterChain from a config dict (for testing)."""

    from voice_typer.server.config import Config

    cfg = Config()
    for key, value in config_dict.items():
        setattr(cfg, key, value)
    return build_chain(cfg, sample_rate=sample_rate)
