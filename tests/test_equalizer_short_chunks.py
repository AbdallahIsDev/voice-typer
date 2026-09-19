"""Regression tests for the Equalizer filter with very short input chunks."""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server.audio_filters import Equalizer


class TestEqualizerShortChunks:
    """Drive the ``n < 3`` code path directly to lock in the fix."""

    def test_one_sample_does_not_raise(self):
        eq = Equalizer(sample_rate=16000)
        audio = np.array([0.5], dtype=np.float32)
        result = eq.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape
        assert result.dtype == np.float32
        assert np.all(np.isfinite(result))

    def test_two_samples_do_not_raise(self):
        eq = Equalizer(sample_rate=16000)
        audio = np.array([0.5, -0.3], dtype=np.float32)
        result = eq.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape
        assert result.dtype == np.float32
        assert np.all(np.isfinite(result))

    def test_three_samples_boundary(self):
        """n=3 is the boundary that selects the optimized delay-buffer path."""
        eq = Equalizer(sample_rate=16000)
        audio = np.array([0.5, -0.3, 0.7], dtype=np.float32)
        result = eq.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape
        assert np.all(np.isfinite(result))

    def test_one_sample_carries_delay_correctly(self):
        """After a 1-sample chunk, delay1 must equal that sample."""
        eq = Equalizer(sample_rate=16000)
        eq.process(np.array([0.42], dtype=np.float32), 16000)
        assert eq._delay1 == pytest.approx(0.42, abs=1e-6)
        assert eq._delay2 == pytest.approx(0.0, abs=1e-12)
        assert eq._delay3 == pytest.approx(0.0, abs=1e-12)

    def test_two_samples_carry_delays_correctly(self):
        """After a 2-sample chunk, delay1=last, delay2=first."""
        eq = Equalizer(sample_rate=16000)
        eq.process(np.array([0.42, -0.17], dtype=np.float32), 16000)
        assert eq._delay1 == pytest.approx(-0.17, abs=1e-6)
        assert eq._delay2 == pytest.approx(0.42, abs=1e-6)
        assert eq._delay3 == pytest.approx(0.0, abs=1e-12)

    def test_three_samples_carry_delays_correctly(self):
        """After a 3-sample chunk, all three delays are populated from x."""
        eq = Equalizer(sample_rate=16000)
        eq.process(np.array([0.42, -0.17, 0.91], dtype=np.float32), 16000)
        assert eq._delay1 == pytest.approx(0.91, abs=1e-6)
        assert eq._delay2 == pytest.approx(-0.17, abs=1e-6)
        assert eq._delay3 == pytest.approx(0.42, abs=1e-6)

    def test_short_then_long_chunk_chain(self):
        """A 1-sample chunk followed by a normal chunk must not crash."""
        eq = Equalizer(sample_rate=16000)
        r1 = eq.process(np.array([0.5], dtype=np.float32), 16000)
        r2 = eq.process(np.array([0.5, 0.3], dtype=np.float32), 16000)
        r3 = eq.process((np.random.randn(1024) * 0.3).astype(np.float32), 16000)
        assert r1.shape == (1,)
        assert r2.shape == (2,)
        assert r3.shape == (1024,)
        assert np.all(np.isfinite(r3))

    def test_short_chunks_with_nonzero_carried_state(self):
        """Pre-populate delay state, then send a 1-sample chunk."""
        eq = Equalizer(sample_rate=16000)
        # Seed the delay state with distinctive values.
        eq._delay1 = 0.11
        eq._delay2 = 0.22
        eq._delay3 = 0.33
        eq.process(np.array([0.99], dtype=np.float32), 16000)
        # After a 1-sample chunk, extended = [0.33, 0.22, 0.11, 0.99].
        assert eq._delay1 == pytest.approx(0.99, abs=1e-6)
        assert eq._delay2 == pytest.approx(0.11, abs=1e-6)
        assert eq._delay3 == pytest.approx(0.22, abs=1e-6)

    def test_repeated_short_chunks_stay_finite(self):
        """Many back-to-back 1-sample chunks must not accumulate NaN/Inf."""
        eq = Equalizer(sample_rate=16000)
        rng = np.random.default_rng(seed=0)
        for _ in range(64):
            r = eq.process(rng.standard_normal(1).astype(np.float32) * 0.3, 16000)
            assert r is not None
            assert r.shape == (1,)
            assert np.all(np.isfinite(r)), "EQ output diverged on short chunks"

    def test_2d_input_with_short_first_axis(self):
        """A 1xN or Nx1 input must round-trip through ravel/reshape."""
        eq = Equalizer(sample_rate=16000)
        audio = np.array([[0.5], [-0.3]], dtype=np.float32)
        result = eq.process(audio, 16000)
        assert result is not None
        assert result.shape == audio.shape
        assert np.all(np.isfinite(result))
