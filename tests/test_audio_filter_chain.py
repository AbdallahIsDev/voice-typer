"""F-1: regression tests for the shared AudioFilterChain component.

Verifies the component file exists, exports the expected interface,
and uses the canonical SettingRow + RangeSlider + Switch + Select
primitives (so the Settings page and Microphone page render the
same UI).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

COMPONENT_PATH = Path(
    "voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx"
)


def _read_component_source() -> str:
    """Read the component source (skip if file missing in CI)."""
    if not COMPONENT_PATH.exists():
        pytest.skip(f"{COMPONENT_PATH} not found — F-1 not implemented")
    return COMPONENT_PATH.read_text(encoding="utf-8")


class TestAudioFilterChainExists:
    """F-1: the shared component file exists at the canonical path."""

    def test_file_exists(self):
        assert COMPONENT_PATH.exists(), (
            "F-1: shared AudioFilterChain component must exist at "
            f"{COMPONENT_PATH}"
        )

    def test_exports_named_component(self):
        src = _read_component_source()
        # The file must export `export function AudioFilterChain` or
        # `export const AudioFilterChain =`.
        assert (
            "export function AudioFilterChain" in src
            or "export const AudioFilterChain" in src
        ), "F-1: AudioFilterChain must be a named export"

    def test_exports_props_interface(self):
        src = _read_component_source()
        assert "AudioFilterChainProps" in src, (
            "F-1: AudioFilterChainProps interface must be exported for type safety"
        )


class TestAudioFilterChainUsesSharedPrimitives:
    """F-1: the shared component must use SettingRow + RangeSlider +
    Switch + Select primitives (same as the Settings page), not custom
    ToggleRow/SliderRow helpers.
    """

    def test_imports_setting_row(self):
        src = _read_component_source()
        assert "SettingRow" in src, (
            "F-1: AudioFilterChain must import SettingRow from "
            "@/components/common/SettingRow for layout consistency"
        )

    def test_imports_range_slider(self):
        src = _read_component_source()
        assert "RangeSlider" in src, (
            "F-1: AudioFilterChain must import RangeSlider from "
            "@/components/common/RangeSlider"
        )

    def test_imports_switch(self):
        src = _read_component_source()
        assert "Switch" in src, (
            "F-1: AudioFilterChain must import Switch for toggle rows"
        )

    def test_imports_select(self):
        src = _read_component_source()
        assert "Select" in src, (
            "F-1: AudioFilterChain must import Select for the noise "
            "suppression method dropdown"
        )

    def test_does_not_define_local_toggle_row(self):
        src = _read_component_source()
        # The shared component must NOT define its own ToggleRow helper
        # (that was the duplication F-1 eliminates).
        assert "function ToggleRow" not in src, (
            "F-1 regression: AudioFilterChain must not define a local "
            "ToggleRow helper — use SettingRow instead"
        )

    def test_does_not_define_local_slider_row(self):
        src = _read_component_source()
        assert "function SliderRow" not in src, (
            "F-1 regression: AudioFilterChain must not define a local "
            "SliderRow helper — use SettingRow + RangeSlider instead"
        )


class TestAudioFilterChainRendersAllFilters:
    """F-1: the shared component renders all 7 filter rows that were
    previously duplicated across AudioSettingsSection and
    AudioPresetSelector.
    """

    @pytest.mark.parametrize(
        "field",
        [
            "noise_filter_highpass",
            "noise_filter_highpass_cutoff_hz",
            "noise_suppression_method",
            "noise_filter_gate",
            "noise_filter_gate_open_threshold_db",
            "noise_filter_gate_close_threshold_db",
            "noise_filter_eq",
            "noise_filter_eq_low_db",
            "noise_filter_eq_mid_db",
            "noise_filter_eq_high_db",
            "noise_filter_compressor",
            "noise_filter_compressor_threshold_db",
            "noise_filter_compressor_ratio",
            "noise_filter_limiter",
            "noise_filter_limiter_ceiling_db",
            "noise_filter_notch",
        ],
    )
    def test_renders_field(self, field):
        src = _read_component_source()
        assert field in src, (
            f"F-1: AudioFilterChain must render the {field} config field"
        )


class TestAudioFilterChainCallSitesUseIt:
    """F-1: both call sites (AudioSettingsSection and AudioPresetSelector)
    must use the shared component, not their own duplicate filter UI.
    """

    def test_audio_settings_section_uses_shared(self):
        p = Path(
            "voice_typer/client/src/renderer/src/components/settings/AudioSettingsSection.tsx"
        )
        if not p.exists():
            pytest.skip("AudioSettingsSection.tsx not found")
        src = p.read_text(encoding="utf-8")
        assert "AudioFilterChain" in src, (
            "F-1: AudioSettingsSection must import and use AudioFilterChain"
        )
        # The duplicate filter UI must be gone.
        # Heuristic: the file should NOT contain more than one
        # `noise_filter_highpass` reference (the single reference is
        # inside the shared component call). Allow some headroom for
        # the import + the call.
        count = src.count("noise_filter_highpass")
        assert count <= 2, (
            f"F-1: AudioSettingsSection still has {count} references to "
            "noise_filter_highpass — the duplicate filter UI was not removed"
        )

    def test_audio_preset_selector_uses_shared(self):
        p = Path(
            "voice_typer/client/src/renderer/src/components/microphone/AudioPresetSelector.tsx"
        )
        if not p.exists():
            pytest.skip("AudioPresetSelector.tsx not found")
        src = p.read_text(encoding="utf-8")
        assert "AudioFilterChain" in src, (
            "F-1: AudioPresetSelector must import and use AudioFilterChain"
        )
        count = src.count("noise_filter_highpass")
        assert count <= 2, (
            f"F-1: AudioPresetSelector still has {count} references to "
            "noise_filter_highpass — the duplicate filter UI was not removed"
        )


class TestAudioFilterChainIStrI18nKeys:
    """F-1: the shared component must use t() for all labels (no
    hardcoded English). This fixes the Microphone page's hardcoded-
    English issue from the task description.
    """

    def test_uses_t_function(self):
        src = _read_component_source()
        assert 'from "@/i18n/i18n"' in src or "from \"@/i18n/i18n\"" in src, (
            "F-1: AudioFilterChain must import t from @/i18n/i18n"
        )
        # Count t() calls — should be many (one per label).
        t_calls = src.count('t("settings.audioEnhancement')
        assert t_calls >= 10, (
            f"F-1: AudioFilterChain only has {t_calls} t() calls — expected "
            "at least 10 (one per label). The Microphone page must NOT use "
            "hardcoded English."
        )

    def test_no_hardcoded_english_labels(self):
        src = _read_component_source()
        # These are the labels that were previously hardcoded in
        # AudioPresetSelector. They must now be t() calls.
        forbidden_hardcoded = [
            '"High-Pass Filter"',
            '"Noise Gate"',
            '"Equalizer"',
            '"Compressor"',
            '"Limiter"',
            '"Notch Filter"',
        ]
        for forbidden in forbidden_hardcoded:
            assert forbidden not in src, (
                f"F-1 regression: AudioFilterChain must not contain the "
                f"hardcoded English label {forbidden} — use t() instead"
            )
