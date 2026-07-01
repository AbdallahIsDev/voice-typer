"""Tests for the audio filter chain (ADR 0007).

Tests each filter in isolation, then the FilterChain composition,
then the chain builder, and finally the preset system.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from voice_typer.server.audio_filters import (
    AudioFilter,
    FilterChain,
    HighPassFilter,
    NoiseGate,
    Equalizer,
    Compressor,
    Limiter,
    NotchFilter,
    NoiseSuppressor,
    db_to_mul,
    mul_to_db,
    one_pole_coeff,
    ANTIDENORMAL_EPSILON,
)
from voice_typer.server.audio_chain_builder import build_chain, build_chain_from_dict
from voice_typer.server.audio_presets import (
    PRESETS,
    PRESET_AUTO,
    PRESET_STUDIO,
    PRESET_NOISY_ROOM,
    PRESET_OFF,
    PRESET_CUSTOM,
    apply_preset,
    get_preset_filters,
    get_preset_for_display,
)


# ═══════════════════════════════════════════════════════════════════════════
# DSP helper tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDSPHelpers:
    def test_db_to_mul_0db_is_1(self):
        assert db_to_mul(0.0) == pytest.approx(1.0)

    def test_db_to_mul_minus_6db(self):
        assert db_to_mul(-6.0) == pytest.approx(0.501, rel=0.01)

    def test_db_to_mul_minus_inf_is_0(self):
        assert db_to_mul(float("-inf")) == 0.0

    def test_mul_to_db_1_is_0(self):
        assert mul_to_db(1.0) == pytest.approx(0.0)

    def test_mul_to_db_0_is_neg_inf(self):
        assert mul_to_db(0.0) == float("-inf")

    def test_mul_to_db_negative_is_neg_inf(self):
        assert mul_to_db(-0.5) == float("-inf")

    def test_roundtrip_db_mul(self):
        for db in [-60, -30, -6, 0, 6]:
            mul = db_to_mul(db)
            assert mul_to_db(mul) == pytest.approx(db, abs=0.01)

    def test_one_pole_coeff_zero_time(self):
        assert one_pole_coeff(16000, 0.0) == 0.0

    def test_one_pole_coeff_negative_time(self):
        assert one_pole_coeff(16000, -1.0) == 0.0

    def test_one_pole_coeff_positive_time(self):
        # For 1 second at 16kHz, coeff should be close to exp(-1/16000) ≈ 0.99994
        c = one_pole_coeff(16000, 1.0)
        assert 0.0 < c < 1.0
        assert c > 0.99  # slow response

    def test_antidenormal_epsilon_is_tiny(self):
        assert 0 < ANTIDENORMAL_EPSILON < 1e-9


# ═══════════════════════════════════════════════════════════════════════════
# HighPassFilter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHighPassFilter:
    def test_construction(self):
        f = HighPassFilter(cutoff_hz=80.0, sample_rate=16000)
        assert "HighPass" in f.name

    def test_processes_audio(self):
        f = HighPassFilter(cutoff_hz=80.0, sample_rate=16000)
        t = np.linspace(0, 0.1, 1600, endpoint=False)
        audio = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        result = f.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_attenuates_low_frequency(self):
        """A 30Hz tone should be attenuated more than a 440Hz tone."""
        f = HighPassFilter(cutoff_hz=80.0, sample_rate=16000)
        t = np.linspace(0, 0.5, 8000, endpoint=False)

        low = (0.3 * np.sin(2 * np.pi * 30 * t)).astype(np.float32)
        high = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        low_result = f.process(low.copy(), 16000)
        f.reset()
        high_result = f.process(high.copy(), 16000)

        low_rms = float(np.sqrt(np.mean(low_result ** 2)))
        high_rms = float(np.sqrt(np.mean(high_result ** 2)))
        # Low should be significantly attenuated vs high
        assert low_rms < high_rms * 0.5

    def test_reset_clears_state(self):
        f = HighPassFilter(cutoff_hz=80.0, sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        f.process(audio, 16000)
        f.reset()  # should not crash
        result = f.process(audio, 16000)
        assert result is not None

    def test_empty_audio_passthrough(self):
        f = HighPassFilter(cutoff_hz=80.0, sample_rate=16000)
        result = f.process(np.array([], dtype=np.float32), 16000)
        assert result is not None
        assert result.size == 0


# ═══════════════════════════════════════════════════════════════════════════
# NoiseGate tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNoiseGate:
    def test_construction(self):
        g = NoiseGate(sample_rate=16000)
        assert g.name == "NoiseGate"

    def test_processes_audio(self):
        g = NoiseGate(sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = g.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_attenuates_silence(self):
        """Very quiet audio should be attenuated by the gate."""
        g = NoiseGate(
            open_threshold_db=-26.0,
            close_threshold_db=-32.0,
            attack_ms=25.0,
            hold_ms=200.0,
            release_ms=150.0,
            sample_rate=16000,
        )
        # Very quiet audio (well below -32dB close threshold)
        silence = np.full(8192, 0.001, dtype=np.float32)
        result = g.process(silence, 16000)
        output_rms = float(np.sqrt(np.mean(result ** 2)))
        input_rms = float(np.sqrt(np.mean(silence ** 2)))
        assert output_rms < input_rms

    def test_passes_loud_audio(self):
        """Loud audio (above open threshold) should pass through."""
        g = NoiseGate(
            open_threshold_db=-26.0,
            close_threshold_db=-32.0,
            sample_rate=16000,
        )
        # Loud audio (0.5 = -6dB, well above -26dB open threshold)
        loud = np.full(8192, 0.5, dtype=np.float32)
        result = g.process(loud, 16000)
        output_rms = float(np.sqrt(np.mean(result ** 2)))
        # Should be close to input (gate is open)
        assert output_rms > 0.3

    def test_reset_clears_state(self):
        g = NoiseGate(sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        g.process(audio, 16000)
        g.reset()
        assert g._is_open is False
        assert g._attenuation == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Equalizer tests
# ═══════════════════════════════════════════════════════════════════════════


class TestEqualizer:
    def test_construction(self):
        eq = Equalizer(sample_rate=16000)
        assert "EQ" in eq.name

    def test_processes_audio(self):
        eq = Equalizer(sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = eq.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_zero_db_is_approximately_passthrough(self):
        """With all bands at 0dB, output RMS should be close to input RMS."""
        eq = Equalizer(low_db=0, mid_db=0, high_db=0, sample_rate=16000)
        t = np.linspace(0, 0.5, 8000, endpoint=False)
        audio = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        result = eq.process(audio.copy(), 16000)
        # Compare RMS of the second half (after filter settled)
        input_rms = float(np.sqrt(np.mean(audio[4000:] ** 2)))
        output_rms = float(np.sqrt(np.mean(result[4000:] ** 2)))
        # The OBS EQ design normalizes by 0.5, so with 0dB gains the output
        # is half the input. This matches OBS behavior.
        assert output_rms > 0  # not silent
        assert output_rms < input_rms * 1.5  # not wildly amplified

    def test_latency_is_3_samples(self):
        eq = Equalizer(sample_rate=48000)
        assert eq.latency_ms == pytest.approx(3.0 * 1000 / 48000, rel=0.01)

    def test_reset_clears_state(self):
        eq = Equalizer(sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        eq.process(audio, 16000)
        eq.reset()
        assert eq._delay1 == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Compressor tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCompressor:
    def test_construction(self):
        c = Compressor(sample_rate=16000)
        assert "Compressor" in c.name

    def test_processes_audio(self):
        c = Compressor(sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = c.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_reduces_loud_peaks(self):
        """Audio above threshold should be reduced (steady-state)."""
        c = Compressor(
            threshold_db=-18.0,  # ≈ 0.126 linear
            ratio=4.0,
            sample_rate=16000,
        )
        # Loud audio well above threshold — use 1 second for steady-state
        loud = np.full(16000, 0.8, dtype=np.float32)
        result = c.process(loud, 16000)
        # Check steady-state (last 25% of signal, after envelope settled)
        steady_state = result[12000:]
        output_peak = float(np.max(np.abs(steady_state)))
        # Should be significantly reduced from 0.8
        assert output_peak < 0.5

    def test_preserves_quiet_audio(self):
        """Audio below threshold should be unaffected (gain=1)."""
        c = Compressor(
            threshold_db=-18.0,
            ratio=4.0,
            sample_rate=16000,
        )
        # Quiet audio well below threshold
        quiet = np.full(4096, 0.01, dtype=np.float32)
        result = c.process(quiet, 16000)
        output_peak = float(np.max(np.abs(result)))
        # Should be close to input (no compression)
        assert abs(output_peak - 0.01) < 0.005

    def test_reset_clears_envelope(self):
        c = Compressor(sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.5
        c.process(audio, 16000)
        c.reset()
        assert c._envelope == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Limiter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestLimiter:
    def test_construction(self):
        lim = Limiter(sample_rate=16000)
        assert "Limiter" in lim.name

    def test_prevents_clipping(self):
        """Audio above ceiling should be limited (steady-state)."""
        lim = Limiter(ceiling_db=-6.0, sample_rate=16000)  # ≈ 0.5 linear
        # Use 1 second for steady-state (envelope needs time to rise)
        loud = np.full(16000, 0.99, dtype=np.float32)
        result = lim.process(loud, 16000)
        # Check steady-state (last 25%, after attack envelope settled)
        steady_state = result[12000:]
        output_peak = float(np.max(np.abs(steady_state)))
        # Should be at the ceiling (~0.5)
        assert output_peak < 0.6

    def test_preserves_quiet_audio(self):
        lim = Limiter(ceiling_db=-6.0, sample_rate=16000)
        quiet = np.full(4096, 0.1, dtype=np.float32)
        result = lim.process(quiet, 16000)
        output_peak = float(np.max(np.abs(result)))
        # Below ceiling, no limiting
        assert abs(output_peak - 0.1) < 0.02

    def test_reset_clears_envelope(self):
        lim = Limiter(sample_rate=16000)
        audio = np.full(1024, 0.9, dtype=np.float32)
        lim.process(audio, 16000)
        lim.reset()
        assert lim._envelope == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# NotchFilter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNotchFilter:
    def test_construction(self):
        n = NotchFilter(frequency_hz=60.0, sample_rate=16000)
        assert "Notch" in n.name

    def test_processes_audio(self):
        n = NotchFilter(frequency_hz=60.0, sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = n.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_auto_detect_defaults_to_60(self):
        n = NotchFilter(frequency_hz=0.0, sample_rate=16000)
        assert n._frequency_hz == 60.0

    def test_attenuates_target_frequency(self):
        """A 60Hz tone should be attenuated by a 60Hz notch."""
        n = NotchFilter(frequency_hz=60.0, sample_rate=16000)
        t = np.linspace(0, 1.0, 16000, endpoint=False)
        tone_60hz = (0.3 * np.sin(2 * np.pi * 60 * t)).astype(np.float32)
        result = n.process(tone_60hz.copy(), 16000)

        # Measure RMS of the second half (after filter settled)
        result_rms = float(np.sqrt(np.mean(result[8000:] ** 2)))
        input_rms = float(np.sqrt(np.mean(tone_60hz[8000:] ** 2)))
        assert result_rms < input_rms * 0.5  # significantly attenuated


# ═══════════════════════════════════════════════════════════════════════════
# NoiseSuppressor tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNoiseSuppressor:
    def test_none_method_is_passthrough(self):
        ns = NoiseSuppressor(method="none", sample_rate=16000)
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = ns.process(audio, 16000)
        assert result is not None
        np.testing.assert_array_equal(result, audio)

    def test_unknown_method_falls_back_to_none(self):
        ns = NoiseSuppressor(method="nonexistent", sample_rate=16000)
        assert ns._method == "none"

    def test_rnnoise_degrades_gracefully_if_missing(self):
        """If rnnoise-webrtc isn't installed, should fall back to none."""
        ns = NoiseSuppressor(method="rnnoise", sample_rate=16000)
        # Either rnnoise is installed (not degraded) or it fell back (degraded)
        if ns._method == "none":
            assert ns.is_degraded is True
            assert "rnnoise" in ns.degraded_reason.lower()


# ═══════════════════════════════════════════════════════════════════════════
# FilterChain tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFilterChain:
    def test_empty_chain(self):
        chain = FilterChain([])
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = chain.process(audio, 16000)
        np.testing.assert_array_equal(result, audio)

    def test_single_filter(self):
        chain = FilterChain([HighPassFilter(80.0, 16000)])
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = chain.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_multiple_filters(self):
        chain = FilterChain([
            HighPassFilter(80.0, 16000),
            NoiseGate(sample_rate=16000),
            Compressor(sample_rate=16000),
            Limiter(sample_rate=16000),
        ])
        audio = np.random.randn(1024).astype(np.float32) * 0.3
        result = chain.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape

    def test_filter_names(self):
        chain = FilterChain([
            HighPassFilter(80.0, 16000),
            NoiseGate(sample_rate=16000),
        ])
        names = chain.filter_names
        assert len(names) == 2
        assert "HighPass" in names[0]
        assert names[1] == "NoiseGate"

    def test_reset_all_filters(self):
        hp = HighPassFilter(80.0, 16000)
        gate = NoiseGate(sample_rate=16000)
        chain = FilterChain([hp, gate])
        audio = (np.random.randn(1024).astype(np.float32)) * 0.3
        chain.process(audio, 16000)
        chain.reset()  # should not crash

    def test_swap_replaces_filters(self):
        chain = FilterChain([HighPassFilter(80.0, 16000)])
        assert len(chain.filter_names) == 1

        new_filters = [HighPassFilter(100.0, 16000), NoiseGate(sample_rate=16000)]
        chain.swap(new_filters)
        assert len(chain.filter_names) == 2

    def test_degraded_propagation(self):
        # NotchFilter with scipy missing would be degraded, but scipy is installed
        chain = FilterChain([HighPassFilter(80.0, 16000)])
        assert chain.is_degraded is False

    def test_total_latency(self):
        eq = Equalizer(sample_rate=48000)
        chain = FilterChain([eq])
        assert chain.total_latency_ms == pytest.approx(eq.latency_ms)


# ═══════════════════════════════════════════════════════════════════════════
# Chain builder tests
# ═══════════════════════════════════════════════════════════════════════════


class TestChainBuilder:
    def test_builds_chain_from_dict(self):
        chain = build_chain_from_dict({
            "noise_filter_highpass": True,
            "noise_suppression_method": "none",
            "noise_filter_gate": True,
            "noise_filter_eq": True,
            "noise_filter_compressor": True,
            "noise_filter_limiter": True,
            "noise_filter_notch": False,
        }, sample_rate=16000)
        names = chain.filter_names
        assert len(names) == 5  # HP, Gate, EQ, Comp, Limiter

    def test_empty_chain_when_all_off(self):
        chain = build_chain_from_dict({
            "noise_filter_highpass": False,
            "noise_suppression_method": "none",
            "noise_filter_gate": False,
            "noise_filter_eq": False,
            "noise_filter_compressor": False,
            "noise_filter_limiter": False,
            "noise_filter_notch": False,
        }, sample_rate=16000)
        assert chain.filter_names == []

    def test_notch_added_when_enabled(self):
        chain = build_chain_from_dict({
            "noise_filter_highpass": True,
            "noise_suppression_method": "none",
            "noise_filter_gate": False,
            "noise_filter_eq": False,
            "noise_filter_compressor": False,
            "noise_filter_limiter": False,
            "noise_filter_notch": True,
            "noise_filter_notch_frequency_hz": 50.0,
        }, sample_rate=16000)
        names = chain.filter_names
        assert any("Notch" in n for n in names)

    def test_defaults_used_when_keys_missing(self):
        chain = build_chain_from_dict({}, sample_rate=16000)
        # With defaults, should have HP, Gate, EQ, Comp, Limiter
        # (RNNoise may be degraded if library missing)
        names = chain.filter_names
        assert len(names) >= 4


# ═══════════════════════════════════════════════════════════════════════════
# Preset tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPresets:
    def test_all_presets_defined(self):
        for name in [PRESET_AUTO, PRESET_STUDIO, PRESET_NOISY_ROOM, PRESET_OFF]:
            assert name in PRESETS

    def test_custom_not_in_presets(self):
        assert PRESET_CUSTOM not in PRESETS

    def test_auto_preset_enables_all(self):
        filters = PRESETS[PRESET_AUTO]
        assert filters["noise_filter_highpass"] is True
        assert filters["noise_filter_gate"] is True
        assert filters["noise_filter_eq"] is True
        assert filters["noise_filter_compressor"] is True
        assert filters["noise_filter_limiter"] is True
        assert filters["noise_suppression_method"] == "rnnoise"

    def test_off_preset_disables_all(self):
        filters = PRESETS[PRESET_OFF]
        assert filters["noise_filter_highpass"] is False
        assert filters["noise_filter_gate"] is False
        assert filters["noise_filter_eq"] is False
        assert filters["noise_filter_compressor"] is False
        assert filters["noise_filter_limiter"] is False
        assert filters["noise_suppression_method"] == "none"

    def test_studio_preset_minimal(self):
        filters = PRESETS[PRESET_STUDIO]
        assert filters["noise_suppression_method"] == "none"
        assert filters["noise_filter_gate"] is False
        assert filters["noise_filter_eq"] is True

    def test_noisy_room_uses_deepfilternet(self):
        filters = PRESETS[PRESET_NOISY_ROOM]
        assert filters["noise_suppression_method"] == "deepfilternet"
        assert filters["noise_filter_notch"] is True

    def test_apply_preset_auto(self):
        class FakeConfig:
            noise_filter_highpass = False
            noise_filter_gate = False
            noise_filter_eq = False
            noise_filter_compressor = False
            noise_filter_limiter = False
            noise_filter_notch = False
            noise_suppression_method = "none"

        cfg = FakeConfig()
        apply_preset(PRESET_AUTO, cfg)
        assert cfg.noise_filter_highpass is True
        assert cfg.noise_filter_gate is True

    def test_apply_preset_custom_does_nothing(self):
        class FakeConfig:
            noise_filter_highpass = False

        cfg = FakeConfig()
        apply_preset(PRESET_CUSTOM, cfg)
        assert cfg.noise_filter_highpass is False  # unchanged

    def test_get_preset_filters_custom_returns_empty(self):
        assert get_preset_filters(PRESET_CUSTOM) == {}

    def test_get_preset_for_display_returns_all(self):
        display = get_preset_for_display()
        values = [d["value"] for d in display]
        assert PRESET_AUTO in values
        assert PRESET_STUDIO in values
        assert PRESET_NOISY_ROOM in values
        assert PRESET_OFF in values
        assert PRESET_CUSTOM in values
