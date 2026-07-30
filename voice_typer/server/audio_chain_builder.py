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


def build_chain(config: Any, sample_rate: int = WHISPER_SAMPLE_RATE) -> FilterChain:
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

    # 3. Noise suppressor (RNNoise / DeepFilterNet / Speex)
    method = config.noise_suppression_method
    if method != "none":
        filters.append(
            NoiseSuppressor(
                method=method,
                sample_rate=sample_rate,
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
                # ER-10: opt-in adaptive calibration — gate samples the first
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
    log.info(
        "[AUDIO-CHAIN] Built chain: %s (latency=%.1fms, degraded=%s)",
        chain.filter_names,
        chain.total_latency_ms,
        chain.is_degraded,
    )
    return chain


def build_chain_from_dict(config_dict: dict, sample_rate: int = WHISPER_SAMPLE_RATE) -> FilterChain:
    """Build a FilterChain from a config dict (for testing).

    Like :func:`build_chain` but accepts a plain dict instead of a
    Config object. Missing keys use the canonical defaults declared on
    :class:`voice_typer.server.config.Config` — FZ-55: previously this
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


# FZ-55: the previous ``_DEFAULTS`` dict is intentionally retained (now
# unused by ``build_chain_from_dict``) for backward-compatibility imports
# in case external scripts/tests reference it. It is no longer the
# source of truth — ``Config()`` defaults are. New code should not
# reference ``_DEFAULTS``; instead construct a ``Config()`` instance or
# import the canonical default directly from the dataclass field.
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
