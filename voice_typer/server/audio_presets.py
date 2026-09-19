"""Microphone quality preset definitions."""

from __future__ import annotations

from typing import Any

# Preset name constants
PRESET_AUTO = "auto"
PRESET_STUDIO = "studio"
PRESET_NOISY_ROOM = "noisy_room"
PRESET_OFF = "off"
PRESET_CUSTOM = "custom"

# Preset → filter settings + optional per-preset parameter overrides.
PRESETS: dict[str, dict[str, Any]] = {
    PRESET_AUTO: {
        "noise_filter_highpass": True,
        "noise_suppression_method": "rnnoise",
        "noise_filter_gate": True,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": False,
    },
    PRESET_STUDIO: {
        "noise_filter_highpass": True,
        "noise_suppression_method": "none",  # quiet room, good mic
        "noise_filter_gate": False,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": False,
    },
    PRESET_NOISY_ROOM: {
        "noise_filter_highpass": True,
        # GTCRN, the bundled ONNX streaming denoiser (higher quality
        "noise_suppression_method": "gtcrn",  # best quality
        "noise_filter_gate": True,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": True,
        "noise_filter_highpass_cutoff_hz": 100.0,  # default 80; strip HVAC rumble
        "noise_filter_gate_open_threshold_db": -22.0,  # default -26; less sensitive
        "noise_filter_compressor_ratio": 4.0,  # default 3.0; stiffer for transients
    },
    PRESET_OFF: {
        "noise_filter_highpass": False,
        "noise_suppression_method": "none",
        "noise_filter_gate": False,
        "noise_filter_eq": False,
        "noise_filter_compressor": False,
        "noise_filter_limiter": False,
        "noise_filter_notch": False,
    },
    # PRESET_CUSTOM is not in this dict, it means "use individual field values"
}


def apply_preset(preset: str, config: Any) -> None:
    """Apply a named preset to a config object in-place."""
    if preset == PRESET_CUSTOM:
        return  # no automatic changes
    if preset not in PRESETS:
        return
    for key, value in PRESETS[preset].items():
        setattr(config, key, value)


def get_preset_filters(preset: str) -> dict[str, Any]:
    """Return the filter settings for a named preset.

    Returns an empty dict for "custom" (no automatic changes).
    """
    return dict(PRESETS.get(preset, {}))
