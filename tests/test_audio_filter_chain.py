"""F-1: regression tests for the shared AudioFilterChain component.

Verifies the component file exists, exports the expected interface,
and uses the canonical SettingRow + RangeSlider + Switch + Select
primitives (so the Settings page and Microphone page render the
same UI).
"""

from __future__ import annotations

from pathlib import Path

import pytest

COMPONENT_PATH = Path("voice_typer/client/src/renderer/src/components/audio/AudioFilterChain.tsx")


def _read_component_source() -> str:
    """Read the component source (skip if file missing in CI)."""
    if not COMPONENT_PATH.exists():
        pytest.skip(f"{COMPONENT_PATH} not found — F-1 not implemented")
    return COMPONENT_PATH.read_text(encoding="utf-8")


REGISTRY_PATH = Path("voice_typer/client/src/renderer/src/components/audio/audioFilterRowDescriptors.ts")


def _read_registry_source() -> str:
    """Read the descriptor render-spec source (skip if missing in CI)."""
    if not REGISTRY_PATH.exists():
        pytest.skip(f"{REGISTRY_PATH} not found — F-1 not implemented")
    return REGISTRY_PATH.read_text(encoding="utf-8")


class TestAudioFilterChainExists:
    """F-1: the shared component file exists at the canonical path."""

    def test_file_exists(self):
        assert COMPONENT_PATH.exists(), f"F-1: shared AudioFilterChain component must exist at {COMPONENT_PATH}"

    def test_exports_named_component(self):
        src = _read_component_source()
        # The file must export `export function AudioFilterChain` or
        # `export const AudioFilterChain =`.
        assert "export function AudioFilterChain" in src or "export const AudioFilterChain" in src, (
            "F-1: AudioFilterChain must be a named export"
        )

    def test_exports_props_interface(self):
        src = _read_component_source()
        assert "AudioFilterChainProps" in src, "F-1: AudioFilterChainProps interface must be exported for type safety"


class TestAudioFilterChainUsesSharedPrimitives:
    """F-1: the shared component must use SettingRow + RangeSlider +
    Switch + Select primitives (same as the Settings page), not custom
    ToggleRow/SliderRow helpers.
    """

    def test_imports_setting_row(self):
        src = _read_component_source()
        assert "SettingRow" in src, (
            "F-1: AudioFilterChain must import SettingRow from @/components/common/SettingRow for layout consistency"
        )

    def test_imports_range_slider(self):
        src = _read_component_source()
        assert "RangeSlider" in src, (
            "F-1: AudioFilterChain must import RangeSlider from @/components/common/RangeSlider"
        )

    def test_imports_switch(self):
        src = _read_component_source()
        assert "Switch" in src, "F-1: AudioFilterChain must import Switch for toggle rows"

    def test_imports_select(self):
        src = _read_component_source()
        assert "Select" in src, "F-1: AudioFilterChain must import Select for the noise suppression method dropdown"

    def test_does_not_define_local_toggle_row(self):
        src = _read_component_source()
        # The shared component must NOT define its own ToggleRow helper
        # (that was the duplication F-1 eliminates).
        assert "function ToggleRow" not in src, (
            "F-1 regression: AudioFilterChain must not define a local ToggleRow helper — use SettingRow instead"
        )

    def test_does_not_define_local_slider_row(self):
        src = _read_component_source()
        assert "function SliderRow" not in src, (
            "F-1 regression: AudioFilterChain must not define a local "
            "SliderRow helper — use SettingRow + RangeSlider instead"
        )


class TestAudioFilterChainRendersAllFilters:
    """F-1: the shared component renders all filter rows that were
    previously duplicated across AudioSettingsSection and
    AudioPresetSelector.

    Since the F-1 refactor the component is data-driven: the per-row
    render spec (configKey, control kind, min/max/step, i18n keys)
    lives in the `audioFilterRowDescriptors` registry and the
    component maps over the registry unconditionally (the `isVisible`
    default is permissive `() => true`). A field is therefore rendered
    iff it appears in the registry, so these tests assert against the
    registry AND that the component consumes the registry as its
    render source.
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
        registry = _read_registry_source()
        assert f'configKey: "{field}"' in registry, (
            f"F-1: AudioFilterChain must render the {field} config field — "
            f"missing from the {self.REGISTRY_PATH.name} render spec"
        )
        # The component must iterate the registry so every descriptor
        # (including the field above) is actually rendered.
        assert "audioFilterRowDescriptors.map" in src, (
            "F-1: AudioFilterChain must iterate audioFilterRowDescriptors — the registry is the render spec"
        )


class TestAudioFilterChainCallSitesUseIt:
    """F-1: both call sites (AudioSettingsSection and AudioPresetSelector)
    must use the shared component, not their own duplicate filter UI.
    """

    def test_audio_settings_section_uses_shared(self):
        p = Path("voice_typer/client/src/renderer/src/components/settings/AudioSettingsSection.tsx")
        if not p.exists():
            pytest.skip("AudioSettingsSection.tsx not found")
        src = p.read_text(encoding="utf-8")
        assert "AudioFilterChain" in src, "F-1: AudioSettingsSection must import and use AudioFilterChain"
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
        p = Path("voice_typer/client/src/renderer/src/components/microphone/AudioPresetSelector.tsx")
        if not p.exists():
            pytest.skip("AudioPresetSelector.tsx not found")
        src = p.read_text(encoding="utf-8")
        assert "AudioFilterChain" in src, "F-1: AudioPresetSelector must import and use AudioFilterChain"
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
        # F-1 refactor: the component consumes the `buildAudioFilterLabels`
        # builder, which resolves every descriptor i18n key through `t()`
        # (no hardcoded English). Verify the wiring AND that the builder
        # actually invokes `t()`.
        assert "buildAudioFilterLabels" in src, (
            "F-1: AudioFilterChain must use buildAudioFilterLabels (the "
            "t()-driven label builder) — no hardcoded English"
        )
        labels_path = Path("voice_typer/client/src/renderer/src/components/audio/audioFilterLabels.ts")
        if not labels_path.exists():
            pytest.skip(f"{labels_path} not found — F-1 not implemented")
        labels_src = labels_path.read_text(encoding="utf-8")
        # The builder must call t() to resolve each key.
        assert "t(key)" in labels_src, "F-1: audioFilterLabels must resolve descriptor i18n keys via t()"
        # The registry must carry a substantial set of settings.audioEnhancement
        # keys (the pre-refactor inline useMemo had 40+ t() calls).
        registry = _read_registry_source()
        i18n_keys = {
            line.split('"')[1] for line in registry.splitlines() if "settings.audioEnhancement" in line and '"' in line
        }
        assert len(i18n_keys) >= 10, (
            f"F-1: audioFilterRowDescriptors only has {len(i18n_keys)} "
            "settings.audioEnhancement keys — expected at least 10 (one per "
            "label). The Microphone page must NOT use hardcoded English."
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
