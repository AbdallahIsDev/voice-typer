"""Audio preset definitions — single source of truth.

This module eliminates the previous 3-way duplication of preset → filter
mappings (service.py, Microphone.tsx, AudioPresetSelector.tsx). All
preset logic lives here; the frontend fetches presets via IPC.

Presets (ADR 0007 §5.5):
    auto       — Best for 90% of users. All filters ON, RNNoise.
    studio     — Quiet room, good mic. Minimal processing.
    noisy_room — Keyboard/fan/HVAC. Aggressive, DeepFilterNet.
    off        — Raw audio, no filtering.
    custom     — User controls each filter individually.
"""

from __future__ import annotations

from typing import Any

# Preset name constants
PRESET_AUTO = "auto"
PRESET_STUDIO = "studio"
PRESET_NOISY_ROOM = "noisy_room"
PRESET_OFF = "off"
PRESET_CUSTOM = "custom"

ALL_PRESETS: list[str] = [PRESET_AUTO, PRESET_STUDIO, PRESET_NOISY_ROOM, PRESET_OFF, PRESET_CUSTOM]

# Preset → filter settings mapping.
# Only includes the boolean on/off toggles + method selection.
# Individual parameter tuning (threshold, ratio, etc.) uses config defaults.
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
        "noise_suppression_method": "deepfilternet",  # best quality
        "noise_filter_gate": True,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": True,
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
    # PRESET_CUSTOM is not in this dict — it means "use individual field values"
}

# Display info for the UI
PRESET_INFO: dict[str, dict[str, str]] = {
    PRESET_AUTO: {
        "label": "Auto",
        "description": "Recommended for most users. All filters enabled with RNNoise.",
    },
    PRESET_STUDIO: {
        "label": "Studio",
        "description": "Quiet room with a good mic. Minimal processing.",
    },
    PRESET_NOISY_ROOM: {
        "label": "Noisy Room",
        "description": "Keyboard, fan, or HVAC noise. Aggressive filtering with DeepFilterNet.",
    },
    PRESET_OFF: {
        "label": "Off",
        "description": "Raw audio, no filtering.",
    },
    PRESET_CUSTOM: {
        "label": "Custom",
        "description": "Advanced. Configure each filter individually.",
    },
}


def apply_preset(preset: str, config: Any) -> None:
    """Apply a named preset to a config object in-place.

    For "custom", does nothing (user controls individual fields).
    For other presets, sets the filter toggles from PRESETS.

    Args:
        preset: one of ALL_PRESETS.
        config: a Config-like object with noise_filter_* attributes.
    """
    if preset == PRESET_CUSTOM:
        return  # no automatic changes
    if preset not in PRESETS:
        return
    for key, value in PRESETS[preset].items():
        setattr(config, key, value)


def get_preset_filters(preset: str) -> dict[str, Any]:
    """Return the filter settings for a named preset.

    Returns an empty dict for "custom" (no automatic changes).
    Returns an empty dict for unknown presets.
    """
    return dict(PRESETS.get(preset, {}))


def get_preset_for_display() -> list[dict[str, str]]:
    """Return preset list for UI display (label + description)."""
    return [
        {"value": p, "label": PRESET_INFO[p]["label"], "description": PRESET_INFO[p]["description"]}
        for p in ALL_PRESETS
    ]
