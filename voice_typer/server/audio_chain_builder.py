"""Filter chain builder — constructs a FilterChain from config."""

from __future__ import annotations

import logging
from typing import Any

from voice_typer.server.audio_filters import (
    FilterChain,
    HighPassFilter,
    NoiseSuppressor,
    NoiseGate,
    Equalizer,
    Compressor,
    Limiter,
    NotchFilter,
)
from voice_typer.server.audio_filters.base import AudioFilter

log = logging.getLogger(__name__)


def build_chain(config: Any, sample_rate: int = 16000) -> FilterChain:
    """Build a FilterChain from the current config.

    Chain order (ADR 0007 §2.1):
        HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter
    (NotchFilter added after HighPass if enabled)

    Each filter is only included if its enable flag is True. Filters
    whose library is missing will set is_degraded=True on the chain.

    Args:
        config: Config-like object with noise_filter_* attributes.
        sample_rate: audio sample rate in Hz.

    Returns:
        A FilterChain ready to process audio.
    """
    filters: list[AudioFilter] = []

    # 1. Notch filter (optional, before high-pass to remove hum early)
    if getattr(config, "noise_filter_notch", False):
        notch_freq = getattr(config, "noise_filter_notch_frequency_hz", 0.0)
        filters.append(NotchFilter(
            frequency_hz=notch_freq,
            sample_rate=sample_rate,
        ))

    # 2. High-pass filter
    if getattr(config, "noise_filter_highpass", True):
        cutoff = getattr(config, "noise_filter_highpass_cutoff_hz", 80.0)
        filters.append(HighPassFilter(
            cutoff_hz=cutoff,
            sample_rate=sample_rate,
        ))

    # 3. Noise suppressor (RNNoise / DeepFilterNet / Speex)
    method = getattr(config, "noise_suppression_method", "rnnoise")
    if method != "none":
        filters.append(NoiseSuppressor(
            method=method,
            sample_rate=sample_rate,
        ))

    # 4. Noise gate
    if getattr(config, "noise_filter_gate", True):
        filters.append(NoiseGate(
            open_threshold_db=getattr(config, "noise_filter_gate_open_threshold_db", -26.0),
            close_threshold_db=getattr(config, "noise_filter_gate_close_threshold_db", -32.0),
            attack_ms=getattr(config, "noise_filter_gate_attack_ms", 25.0),
            hold_ms=getattr(config, "noise_filter_gate_hold_ms", 200.0),
            release_ms=getattr(config, "noise_filter_gate_release_ms", 150.0),
            sample_rate=sample_rate,
        ))

    # 5. Equalizer
    if getattr(config, "noise_filter_eq", True):
        filters.append(Equalizer(
            low_db=getattr(config, "noise_filter_eq_low_db", -3.0),
            mid_db=getattr(config, "noise_filter_eq_mid_db", 3.0),
            high_db=getattr(config, "noise_filter_eq_high_db", 2.0),
            sample_rate=sample_rate,
        ))

    # 6. Compressor
    if getattr(config, "noise_filter_compressor", True):
        filters.append(Compressor(
            threshold_db=getattr(config, "noise_filter_compressor_threshold_db", -18.0),
            ratio=getattr(config, "noise_filter_compressor_ratio", 3.0),
            attack_ms=getattr(config, "noise_filter_compressor_attack_ms", 6.0),
            release_ms=getattr(config, "noise_filter_compressor_release_ms", 60.0),
            output_gain_db=getattr(config, "noise_filter_compressor_output_gain_db", 0.0),
            sample_rate=sample_rate,
        ))

    # 7. Limiter (always last — brick-wall safety net)
    if getattr(config, "noise_filter_limiter", True):
        filters.append(Limiter(
            ceiling_db=getattr(config, "noise_filter_limiter_ceiling_db", -6.0),
            release_ms=getattr(config, "noise_filter_limiter_release_ms", 60.0),
            sample_rate=sample_rate,
        ))

    chain = FilterChain(filters)
    log.info(
        "[AUDIO-CHAIN] Built chain: %s (latency=%.1fms, degraded=%s)",
        chain.filter_names,
        chain.total_latency_ms,
        chain.is_degraded,
    )
    return chain


def build_chain_from_dict(config_dict: dict, sample_rate: int = 16000) -> FilterChain:
    """Build a FilterChain from a config dict (for testing).

    Like :func:`build_chain` but accepts a plain dict instead of a
    Config object. Missing keys use the same defaults as :func:`build_chain`.
    """
    class _DictConfig:
        def __getattr__(self, name: str):
            return config_dict.get(name, _DEFAULTS.get(name))
    return build_chain(_DictConfig(), sample_rate=sample_rate)


# Default values matching the Config class defaults (ADR 0007 §5)
_DEFAULTS: dict[str, object] = {
    "noise_filter_highpass": True,
    "noise_filter_highpass_cutoff_hz": 80.0,
    "noise_suppression_method": "rnnoise",
    "noise_filter_gate": True,
    "noise_filter_gate_open_threshold_db": -26.0,
    "noise_filter_gate_close_threshold_db": -32.0,
    "noise_filter_gate_attack_ms": 25.0,
    "noise_filter_gate_hold_ms": 200.0,
    "noise_filter_gate_release_ms": 150.0,
    "noise_filter_eq": True,
    "noise_filter_eq_low_db": -3.0,
    "noise_filter_eq_mid_db": 3.0,
    "noise_filter_eq_high_db": 2.0,
    "noise_filter_compressor": True,
    "noise_filter_compressor_threshold_db": -18.0,
    "noise_filter_compressor_ratio": 3.0,
    "noise_filter_compressor_attack_ms": 6.0,
    "noise_filter_compressor_release_ms": 60.0,
    "noise_filter_compressor_output_gain_db": 0.0,
    "noise_filter_limiter": True,
    "noise_filter_limiter_ceiling_db": -6.0,
    "noise_filter_limiter_release_ms": 60.0,
    "noise_filter_notch": False,
    "noise_filter_notch_frequency_hz": 0.0,
}
