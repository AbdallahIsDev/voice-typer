"""Tests for AudioProcessor — high-pass, noise gate, RNNoise, post-capture."""

from __future__ import annotations

import numpy as np
import pytest

from voice_typer.server.audio_processor import AudioProcessor, AudioProcessorConfig


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def default_config() -> AudioProcessorConfig:
    return AudioProcessorConfig(
        enabled=True,
        highpass=True,
        highpass_cutoff_hz=80.0,
        noise_gate=True,
        noise_gate_threshold=0.003,
        noise_gate_hold_ms=300.0,
        rnnoise=False,
        post_capture=False,  # OFF by default in tests (noisereduce may be missing)
        normalize_audio=False,  # OFF in tests so normalization doesn't undo filter effects
    )


@pytest.fixture
def processor(default_config: AudioProcessorConfig) -> AudioProcessor:
    return AudioProcessor(default_config, sample_rate=16000)


def make_sine(freq: float, duration_s: float, sr: int = 16000, amp: float = 0.5) -> np.ndarray:
    """Generate a sine wave at the given frequency."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# ═══════════════════════════════════════════════════════════════════════════
# Config tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAudioProcessorConfig:
    def test_defaults(self) -> None:
        cfg = AudioProcessorConfig()
        assert cfg.enabled is True
        assert cfg.highpass is True
        assert cfg.highpass_cutoff_hz == 80.0
        assert cfg.noise_gate is True
        assert cfg.noise_gate_threshold == 0.003
        assert cfg.noise_gate_hold_ms == 300.0
        assert cfg.rnnoise is False
        assert cfg.post_capture is True

    def test_from_config_with_object(self) -> None:
        class FakeConfig:
            noise_filter_enabled = False
            noise_filter_highpass = False
            noise_filter_highpass_cutoff_hz = 100.0
            noise_filter_gate = False
            noise_filter_gate_threshold = 0.02
            noise_filter_gate_hold_ms = 200.0
            noise_filter_rnnoise = True
            noise_filter_post_capture = False

        cfg = AudioProcessorConfig.from_config(FakeConfig())
        assert cfg.enabled is False
        assert cfg.highpass is False
        assert cfg.highpass_cutoff_hz == 100.0
        assert cfg.noise_gate is False
        assert cfg.noise_gate_threshold == 0.02
        assert cfg.noise_gate_hold_ms == 200.0
        assert cfg.rnnoise is True
        assert cfg.post_capture is False

    def test_from_config_with_missing_attrs_uses_defaults(self) -> None:
        class EmptyConfig:
            pass

        cfg = AudioProcessorConfig.from_config(EmptyConfig())
        assert cfg.enabled is True
        assert cfg.highpass is True


# ═══════════════════════════════════════════════════════════════════════════
# Disabled processor tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDisabledProcessor:
    def test_disabled_is_passthrough(self) -> None:
        cfg = AudioProcessorConfig(enabled=False)
        proc = AudioProcessor(cfg, sample_rate=16000)
        chunk = np.random.randn(1024).astype(np.float32) * 0.1
        out = proc.process_chunk(chunk)
        assert np.array_equal(out, chunk)

    def test_empty_chunk_is_passthrough(self, processor: AudioProcessor) -> None:
        empty = np.array([], dtype=np.float32)
        out = processor.process_chunk(empty)
        assert out.size == 0


# ═══════════════════════════════════════════════════════════════════════════
# High-pass filter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestHighPassFilter:
    def test_attenuates_low_frequency(self, processor: AudioProcessor) -> None:
        """A 30 Hz sine (below the 80 Hz cutoff) should be attenuated."""
        low = make_sine(freq=30, duration_s=0.5, amp=0.5)
        original_rms = float(np.sqrt(np.mean(np.square(low))))

        out = processor.process_chunk(low.copy())
        filtered_rms = float(np.sqrt(np.mean(np.square(out))))

        assert filtered_rms < original_rms * 0.5, \
            f"Low-freq should be attenuated: {filtered_rms} vs {original_rms}"

    def test_passes_high_frequency(self, processor: AudioProcessor) -> None:
        """A 500 Hz sine (above the 80 Hz cutoff) should pass through."""
        high = make_sine(freq=500, duration_s=0.5, amp=0.5)
        original_rms = float(np.sqrt(np.mean(np.square(high))))

        out = processor.process_chunk(high.copy())
        filtered_rms = float(np.sqrt(np.mean(np.square(out))))

        assert filtered_rms > original_rms * 0.8, \
            f"High-freq should pass: {filtered_rms} vs {original_rms}"

    def test_state_continuity_across_chunks(self) -> None:
        """Filter state should carry over between chunks (no discontinuity)."""
        cfg = AudioProcessorConfig(enabled=True, highpass=True, noise_gate=False, post_capture=False)
        proc = AudioProcessor(cfg, sample_rate=16000)

        chunk1 = make_sine(freq=50, duration_s=0.1, amp=0.5)
        chunk2 = make_sine(freq=50, duration_s=0.1, amp=0.5)

        out1 = proc.process_chunk(chunk1.copy())
        out2 = proc.process_chunk(chunk2.copy())

        # The second chunk should not have a transient spike from
        # filter state reset.
        assert np.max(np.abs(out2)) < 1.0

    def test_reset_clears_state(self) -> None:
        cfg = AudioProcessorConfig(enabled=True, highpass=True, noise_gate=False, post_capture=False)
        proc = AudioProcessor(cfg, sample_rate=16000)

        chunk = make_sine(freq=50, duration_s=0.1, amp=0.5)
        proc.process_chunk(chunk.copy())
        proc.reset()

        # After reset, processing should not crash and state should be fresh
        out = proc.process_chunk(chunk.copy())
        assert out.dtype == np.float32


# ═══════════════════════════════════════════════════════════════════════════
# Noise gate tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNoiseGate:
    def test_attenuates_below_threshold(self) -> None:
        """Audio below threshold should be heavily gain-reduced
        (expander), not necessarily all zeros.  The expander applies
        gain proportional to (rms/threshold)^2, so 0.01 RMS with
        0.05 threshold yields ~0.04 gain → output ~0.0004."""
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=True,
            noise_gate_threshold=0.05, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        quiet = np.full(1024, 0.01, dtype=np.float32)  # below 0.05
        out = proc.process_chunk(quiet.copy())
        assert np.max(np.abs(out)) < 0.005, \
            "Audio below threshold should be heavily attenuated (expander gain ~0.04)"

    def test_passes_above_threshold(self) -> None:
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=True,
            noise_gate_threshold=0.005, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        loud = np.full(1024, 0.1, dtype=np.float32)  # above 0.005
        out = proc.process_chunk(loud.copy())
        assert not np.all(out == 0.0), "Audio above threshold should pass"

    def test_gate_preserves_loud_audio_amplitude(self) -> None:
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=True,
            noise_gate_threshold=0.005, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        loud = make_sine(freq=440, duration_s=0.1, amp=0.3)
        out = proc.process_chunk(loud.copy())
        assert np.max(np.abs(out)) > 0.2  # amplitude preserved

    def test_hold_keeps_gate_open_across_short_gaps(self) -> None:
        """The gate should stay open across brief drops below threshold
        (e.g., syllable gaps ~20-80 ms) when the hold period is active."""
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=True,
            noise_gate_threshold=0.05, noise_gate_hold_ms=31.25,  # 500 samples at 16kHz
            post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        # First chunk: loud (above 0.05) — opens the gate, hold = 500
        loud = np.full(512, 0.3, dtype=np.float32)
        out1 = proc.process_chunk(loud.copy())
        assert not np.all(out1 == 0.0), "First loud chunk should pass"

        # Second chunk: quiet but within hold window (consumes 500 of 512 hold samples)
        quiet = np.full(512, 0.01, dtype=np.float32)
        out2 = proc.process_chunk(quiet.copy())
        assert not np.all(out2 == 0.0), \
            "Quiet chunk within hold window should still pass"

        # Third chunk: quiet — hold fully consumed, expander should
        # heavily attenuate (gain ~0.04, output ~0.0004)
        out3 = proc.process_chunk(quiet.copy())
        assert np.max(np.abs(out3)) < 0.005, \
            "Quiet chunk after hold expiry should be heavily attenuated (expander gain ~0.04)"

    def test_gate_without_hold_silences_immediately(self) -> None:
        """With hold disabled (0 ms), a quiet chunk after a loud chunk
        should be silenced immediately — the hold period is zero, so
        the gate closes on the first below-threshold chunk."""
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=True,
            noise_gate_threshold=0.05, noise_gate_hold_ms=0.0,
            post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        # Single loud chunk to open the gate
        loud = np.full(512, 0.3, dtype=np.float32)
        proc.process_chunk(loud.copy())

        # Quiet chunk — gate has no hold, should heavily attenuate
        quiet = np.full(512, 0.01, dtype=np.float32)
        out = proc.process_chunk(quiet.copy())
        assert np.max(np.abs(out)) < 0.005, \
            "Gate with no hold should heavily attenuate quiet chunk (expander gain ~0.04)"


# ═══════════════════════════════════════════════════════════════════════════
# Quality callback tests
# ═══════════════════════════════════════════════════════════════════════════


class TestQualityCallback:
    def test_callback_receives_rms_and_peak(self) -> None:
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=False, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        received: list[tuple[float, float]] = []
        proc.set_quality_callback(lambda rms, peak: received.append((rms, peak)))

        chunk = make_sine(freq=440, duration_s=0.05, amp=0.5)
        proc.process_chunk(chunk.copy())

        assert len(received) == 1
        rms, peak = received[0]
        assert 0.0 < rms < 1.0
        assert 0.0 < peak <= 1.0

    def test_callback_exception_is_swallowed(self) -> None:
        cfg = AudioProcessorConfig(
            enabled=True, highpass=False, noise_gate=False, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        def bad_cb(rms: float, peak: float) -> None:
            raise RuntimeError("boom")

        proc.set_quality_callback(bad_cb)
        chunk = make_sine(freq=440, duration_s=0.05, amp=0.5)
        # Should not raise
        proc.process_chunk(chunk.copy())


# ═══════════════════════════════════════════════════════════════════════════
# Post-capture tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPostCapture:
    def test_disabled_returns_input_unchanged(self) -> None:
        cfg = AudioProcessorConfig(enabled=True, post_capture=False)
        proc = AudioProcessor(cfg, sample_rate=16000)
        audio = make_sine(freq=440, duration_s=1.0, amp=0.3)
        out = proc.process_full_audio(audio)
        assert np.array_equal(out, audio)

    def test_short_audio_skipped(self) -> None:
        """Audio < 0.5s should skip post-capture (not enough for noise profile)."""
        cfg = AudioProcessorConfig(enabled=True, post_capture=True)
        proc = AudioProcessor(cfg, sample_rate=16000)
        short = make_sine(freq=440, duration_s=0.2, amp=0.3)
        out = proc.process_full_audio(short.copy())
        assert np.array_equal(out, short)

    def test_empty_audio_passthrough(self) -> None:
        cfg = AudioProcessorConfig(enabled=True, post_capture=True)
        proc = AudioProcessor(cfg, sample_rate=16000)
        empty = np.array([], dtype=np.float32)
        out = proc.process_full_audio(empty)
        assert out.size == 0


# ═══════════════════════════════════════════════════════════════════════════
# Introspection tests
# ═══════════════════════════════════════════════════════════════════════════


class TestIntrospection:
    def test_is_enabled(self, processor: AudioProcessor) -> None:
        assert processor.is_enabled is True

    def test_has_highpass(self, processor: AudioProcessor) -> None:
        assert processor.has_highpass is True

    def test_has_rnnoise_false_by_default(self, processor: AudioProcessor) -> None:
        assert processor.has_rnnoise is False

    def test_disabled_processor_has_no_highpass(self) -> None:
        cfg = AudioProcessorConfig(enabled=True, highpass=False)
        proc = AudioProcessor(cfg, sample_rate=16000)
        assert proc.has_highpass is False


# ═══════════════════════════════════════════════════════════════════════════
# Integration: full chain
# ═══════════════════════════════════════════════════════════════════════════


class TestFullChain:
    def test_combined_highpass_and_gate(self) -> None:
        """A chunk with low-freq rumble + low amplitude should be heavily attenuated."""
        cfg = AudioProcessorConfig(
            enabled=True, highpass=True, highpass_cutoff_hz=80.0,
            noise_gate=True, noise_gate_threshold=0.02,
            rnnoise=False, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        # 40 Hz rumble at low amplitude — should be attenuated by HPF
        # and then silenced by the gate.
        chunk = make_sine(freq=40, duration_s=0.1, amp=0.01)
        out = proc.process_chunk(chunk.copy())

        assert np.max(np.abs(out)) < 0.005, \
            "Low-freq + low-amplitude audio should be heavily attenuated by HPF + gate"

    def test_speech_frequency_passes_through(self) -> None:
        """A 200 Hz tone (speech range) at normal amplitude should pass."""
        cfg = AudioProcessorConfig(
            enabled=True, highpass=True, highpass_cutoff_hz=80.0,
            noise_gate=True, noise_gate_threshold=0.005,
            rnnoise=False, post_capture=False,
        )
        proc = AudioProcessor(cfg, sample_rate=16000)

        chunk = make_sine(freq=200, duration_s=0.1, amp=0.3)
        out = proc.process_chunk(chunk.copy())

        assert np.max(np.abs(out)) > 0.1, \
            "Speech-range audio should pass through the filter chain"

    def test_dtype_preserved(self, processor: AudioProcessor) -> None:
        chunk = np.random.randn(1024).astype(np.float32) * 0.1
        out = processor.process_chunk(chunk)
        assert out.dtype == np.float32
