"""Tests for AudioProcessor — filter chain wrapper (ADR 0007).

The old monolithic AudioProcessor with AudioProcessorConfig has been
replaced by a thin wrapper around FilterChain. These tests verify the
new architecture: chain building, process_chunk, reset, rebuild, and
quality callback wiring.
"""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server.audio_processor import AudioProcessor

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


class FakeConfig:
    """Minimal config object for testing — has noise_filter_* fields."""

    def __init__(self, **kwargs):
        # ADR 0007 defaults
        self.audio_preset = "custom"
        self.noise_filter_enabled = True
        self.noise_filter_highpass = True
        self.noise_filter_highpass_cutoff_hz = 80.0
        self.noise_filter_gate = True
        self.noise_filter_gate_threshold = 0.003
        self.noise_filter_gate_hold_ms = 200.0
        self.noise_filter_gate_open_threshold_db = -26.0
        self.noise_filter_gate_close_threshold_db = -32.0
        self.noise_filter_gate_attack_ms = 25.0
        self.noise_filter_gate_release_ms = 150.0
        self.noise_filter_rnnoise = True
        self.noise_filter_post_capture = False
        self.noise_suppression_method = "none"  # skip RNNoise in tests
        self.noise_filter_eq = True
        self.noise_filter_eq_low_db = -3.0
        self.noise_filter_eq_mid_db = 3.0
        self.noise_filter_eq_high_db = 2.0
        self.noise_filter_compressor = True
        self.noise_filter_compressor_threshold_db = -18.0
        self.noise_filter_compressor_ratio = 3.0
        self.noise_filter_compressor_attack_ms = 6.0
        self.noise_filter_compressor_release_ms = 60.0
        self.noise_filter_compressor_output_gain_db = 0.0
        self.noise_filter_limiter = True
        self.noise_filter_limiter_ceiling_db = -6.0
        self.noise_filter_limiter_release_ms = 60.0
        self.noise_filter_notch = False
        self.noise_filter_notch_frequency_hz = 0.0
        self.sample_rate = 16000
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.fixture
def default_config():
    return FakeConfig()


@pytest.fixture
def processor(default_config):
    return AudioProcessor(default_config, sample_rate=16000)


def make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    """Generate a sine wave at the given frequency."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ═══════════════════════════════════════════════════════════════════════════
# Construction tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAudioProcessorConstruction:
    def test_constructs_with_config(self, default_config):
        p = AudioProcessor(default_config, sample_rate=16000)
        assert p is not None
        assert p.chain is not None

    def test_filter_names_populated(self, processor):
        names = processor.filter_names
        assert "HighPass(80Hz)" in names
        assert "NoiseGate" in names
        assert "EQ" in names or "EQ(" in " ".join(names)
        assert "Compressor" in " ".join(names) or "Compressor(" in " ".join(names)
        assert "Limiter" in " ".join(names) or "Limiter(" in " ".join(names)

    def test_degraded_false_when_scipy_available(self, processor):
        # scipy is installed in test env, so not degraded
        assert processor.is_degraded is False

    def test_latency_positive(self, processor):
        # EQ adds 3 samples of latency
        assert processor.total_latency_ms > 0


# ═══════════════════════════════════════════════════════════════════════════
# process_chunk tests
# ═══════════════════════════════════════════════════════════════════════════


class TestProcessChunk:
    def test_processes_sine_wave(self, processor):
        audio = make_sine(440, 0.1, amp=0.3)
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.shape == audio.shape
        assert result.dtype == np.float32

    def test_empty_chunk_returns_empty(self, processor):
        audio = np.array([], dtype=np.float32)
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.size == 0

    def test_preserves_shape(self, processor):
        audio = make_sine(440, 0.05, amp=0.2).reshape(-1, 1)
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.shape == audio.shape

    def test_converts_dtype_to_float32(self, processor):
        audio = make_sine(440, 0.05, amp=0.2).astype(np.float64)
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.dtype == np.float32

    def test_does_not_clip_normal_audio(self, processor):
        audio = make_sine(440, 0.1, amp=0.3)
        result = processor.process_chunk(audio)
        assert np.max(np.abs(result)) <= 1.0

    def test_limiter_prevents_clipping(self, processor):
        # Very loud audio should be limited below 1.0
        audio = np.ones(1024, dtype=np.float32) * 0.99
        result = processor.process_chunk(audio)
        # Limiter ceiling is -6dB ≈ 0.5, so output should be well below 1.0
        assert np.max(np.abs(result)) < 0.95


# ═══════════════════════════════════════════════════════════════════════════
# Reset tests
# ═══════════════════════════════════════════════════════════════════════════


class TestReset:
    def test_reset_does_not_crash(self, processor):
        audio = make_sine(440, 0.1, amp=0.3)
        processor.process_chunk(audio)
        processor.reset()  # should not raise

    def test_reset_allows_continuation(self, processor):
        audio = make_sine(440, 0.1, amp=0.3)
        processor.process_chunk(audio)
        processor.reset()
        result = processor.process_chunk(audio)
        assert result is not None
        assert result.shape == audio.shape


# ═══════════════════════════════════════════════════════════════════════════
# Rebuild tests (ADR 0007 §6.1 — live config rebuild)
# ═══════════════════════════════════════════════════════════════════════════


class TestRebuildFromConfig:
    def test_rebuild_changes_filters(self, default_config):
        processor = AudioProcessor(default_config, sample_rate=16000)
        initial_names = processor.filter_names
        assert len(initial_names) > 0

        # Disable all filters
        new_config = FakeConfig(
            noise_filter_highpass=False,
            noise_filter_gate=False,
            noise_filter_eq=False,
            noise_filter_compressor=False,
            noise_filter_limiter=False,
        )
        processor.rebuild_from_config(new_config)
        assert processor.filter_names == []

    def test_rebuild_enables_notch(self, default_config):
        processor = AudioProcessor(default_config, sample_rate=16000)
        assert "Notch" not in " ".join(processor.filter_names)

        new_config = FakeConfig(noise_filter_notch=True, noise_filter_notch_frequency_hz=60.0)
        processor.rebuild_from_config(new_config)
        assert "Notch" in " ".join(processor.filter_names)

    def test_rebuild_preserves_quality_callback(self, default_config):
        processor = AudioProcessor(default_config, sample_rate=16000)
        cb_calls = []
        processor.set_quality_callback(lambda rms, peak: cb_calls.append((rms, peak)))

        processor.rebuild_from_config(default_config)

        audio = make_sine(440, 0.05, amp=0.3)
        processor.process_chunk(audio)
        assert len(cb_calls) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Quality callback tests
# ═══════════════════════════════════════════════════════════════════════════


class TestQualityCallback:
    def test_callback_receives_rms_and_peak(self, processor):
        calls = []
        processor.set_quality_callback(lambda rms, peak: calls.append((rms, peak)))

        audio = make_sine(440, 0.05, amp=0.3)
        processor.process_chunk(audio)

        assert len(calls) > 0
        rms, peak = calls[0]
        assert 0.0 <= rms <= 1.0
        assert 0.0 <= peak <= 1.0
        assert rms <= peak  # RMS is always <= peak

    def test_callback_not_called_on_empty_chunk(self, processor):
        calls = []
        processor.set_quality_callback(lambda rms, peak: calls.append((rms, peak)))

        audio = np.array([], dtype=np.float32)
        processor.process_chunk(audio)

        assert len(calls) == 0

    def test_callback_exception_does_not_crash(self, processor):
        def bad_callback(rms, peak):
            raise RuntimeError("callback exploded")

        processor.set_quality_callback(bad_callback)

        audio = make_sine(440, 0.05, amp=0.3)
        # Should not raise
        result = processor.process_chunk(audio)
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
# Introspection tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntrospection:
    def test_filter_names_returns_list(self, processor):
        names = processor.filter_names
        assert isinstance(names, list)
        assert len(names) > 0

    def test_degraded_reasons_returns_list(self, processor):
        reasons = processor.degraded_reasons
        assert isinstance(reasons, list)

    def test_total_latency_ms_is_float(self, processor):
        assert isinstance(processor.total_latency_ms, float)
        assert processor.total_latency_ms >= 0.0
