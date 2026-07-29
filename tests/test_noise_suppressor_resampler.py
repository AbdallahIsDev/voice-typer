"""XV-32 / XV-33 / XV-38: tests for the noise_suppressor streaming resampler.

These tests exercise the ``_StreamingResampler`` helper directly. They do
NOT require ``pyrnnoise`` (the RNNoise backend) — the resampler is a pure
DSP primitive. They DO require ``scipy`` for the FIR filter design and
``lfilter``; tests are skipped if scipy is unavailable (matches the
existing ``NotchFilter`` / ``HighPassFilter`` test convention).

Coverage:
- XV-32: the FIR filter is designed ONCE at construction (verified by
  counting ``scipy.signal.firwin`` invocations) and reused across calls.
- XV-33: the cumulative output length matches the cumulative input length
  (after the up/down ratio) — verified for the 16k↔48k round-trip the
  RNNoise path uses, including across chunks of varying sizes.
- XV-33: chunked processing produces output identical to one-shot
  processing (no edge artifacts at chunk boundaries).
- XV-38: ``_process_rnnoise`` clips float32 input to ±1.0 before casting
  to int16. (Tested via a stubbed backend so pyrnnoise isn't required.)
"""

from __future__ import annotations

import numpy as np
import pytest

scipy = pytest.importorskip("scipy.signal")  # skips the whole module if missing

from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.noise_suppressor import (  # noqa: E402
    _INT16_SCALE,
    _RNNOISE_FRAME_SIZE,
    NoiseSuppressor,
    _StreamingResampler,
)


class TestStreamingResamplerLength:
    """XV-33: cumulative output length invariant."""

    def test_upsample_length_16k_to_48k(self):
        r = _StreamingResampler(3, 1)
        x = np.random.randn(100).astype(np.float32) * 0.1
        y = r.process(x)
        assert y.size == 300

    def test_downsample_length_48k_to_16k(self):
        r = _StreamingResampler(1, 3)
        x = np.random.randn(300).astype(np.float32) * 0.1
        y = r.process(x)
        assert y.size == 100

    def test_roundtrip_preserves_total_length(self):
        """16k→48k→16k: total output length matches total input length."""
        up = _StreamingResampler(3, 1)
        down = _StreamingResampler(1, 3)
        chunk_sizes = [50, 80, 100, 200, 13, 100, 50, 250, 17, 100, 50, 80, 100, 200, 13, 100, 50, 250, 17, 100]
        total_in = 0
        total_out = 0
        for n in chunk_sizes:
            x = np.random.randn(n).astype(np.float32) * 0.1
            total_in += n
            y = down.process(up.process(x))
            total_out += y.size
        assert total_in == total_out, f"length mismatch: in={total_in}, out={total_out}"

    def test_roundtrip_no_chunk_size_dependent_artifacts(self):
        """Chunked output is identical to one-shot output (no edge artifacts)."""
        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
        x_full = (0.1 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
        up1 = _StreamingResampler(3, 1)
        down1 = _StreamingResampler(1, 3)
        y_oneshot = down1.process(up1.process(x_full))
        up2 = _StreamingResampler(3, 1)
        down2 = _StreamingResampler(1, 3)
        chunk_size = 100
        y_chunks = []
        for i in range(0, x_full.size, chunk_size):
            chunk = x_full[i : i + chunk_size]
            y_chunks.append(down2.process(up2.process(chunk)))
        y_chunked = np.concatenate(y_chunks) if y_chunks else np.zeros(0, dtype=np.float32)
        assert y_oneshot.size == y_chunked.size
        np.testing.assert_array_almost_equal(y_oneshot, y_chunked, decimal=5)


class TestStreamingResamplerFilterCaching:
    """XV-32: the FIR filter is designed ONCE, not per-call."""

    def test_filter_designed_once_at_construction(self, monkeypatch):
        from scipy.signal import firwin

        call_count = {"n": 0}
        original_firwin = firwin

        def counting_firwin(*args, **kwargs):
            call_count["n"] += 1
            return original_firwin(*args, **kwargs)

        import scipy.signal as sig

        monkeypatch.setattr(sig, "firwin", counting_firwin)

        r = _StreamingResampler(3, 1)
        assert call_count["n"] == 1, "firwin must be called exactly once at construction"
        for _ in range(20):
            x = np.random.randn(100).astype(np.float32) * 0.1
            r.process(x)
        assert call_count["n"] == 1, "process() must not re-design the filter"

    def test_filter_reused_across_calls(self):
        r = _StreamingResampler(3, 1)
        h_before = r._h
        for _ in range(10):
            x = np.random.randn(50).astype(np.float32) * 0.1
            r.process(x)
        assert r._h is h_before, "filter array identity must be stable across calls"


class TestStreamingResamplerReset:
    def test_reset_clears_state(self):
        r = _StreamingResampler(3, 1)
        x = np.random.randn(100).astype(np.float32) * 0.1
        r.process(x)
        assert r._zi.size > 0
        assert r._in_total > 0
        assert r._out_total > 0
        r.reset()
        assert r._in_total == 0
        assert r._out_total == 0
        assert r._phase == 0
        assert np.all(r._zi == 0)

    def test_reset_zeros_state_buffers(self):
        r = _StreamingResampler(3, 1)
        x = np.random.randn(100).astype(np.float32) * 0.1
        r.process(x)
        old_zi = r._zi
        old_zi.fill(0.5)
        r.reset()
        assert np.all(old_zi == 0), "reset() must zero the old state array in place"


class TestStreamingResamplerEdgeCases:
    def test_empty_input_returns_empty(self):
        r = _StreamingResampler(3, 1)
        y = r.process(np.zeros(0, dtype=np.float32))
        assert y.size == 0

    def test_single_sample_no_crash(self):
        r = _StreamingResampler(3, 1)
        x = np.array([0.5], dtype=np.float32)
        y = r.process(x)
        assert y.size == 3

    def test_very_small_chunks_roundtrip(self):
        up = _StreamingResampler(3, 1)
        down = _StreamingResampler(1, 3)
        total_in = 0
        total_out = 0
        for n in [1, 2, 3, 1, 2, 1, 3, 2, 1, 1]:
            x = np.random.randn(n).astype(np.float32) * 0.1
            total_in += n
            total_out += down.process(up.process(x)).size
        assert abs(total_in - total_out) <= 1, f"tiny-chunk roundtrip drift: {total_in} vs {total_out}"

    def test_invalid_ratio_raises(self):
        with pytest.raises(ValueError):
            _StreamingResampler(0, 1)
        with pytest.raises(ValueError):
            _StreamingResampler(1, 0)
        with pytest.raises(ValueError):
            _StreamingResampler(-1, 1)


class TestNoiseSuppressorClipBeforeInt16:
    """XV-38: clip float32 input to ±1.0 before scaling to int16."""

    def _make_stub_ns(self):
        ns = NoiseSuppressor(method="none", sample_rate=RNNOISE_SAMPLE_RATE)
        received_frames: list[np.ndarray] = []

        class _StubBackend:
            channels = 1

            def denoise_frame(self, frame_i16):
                received_frames.append(np.asarray(frame_i16[0]).copy())
                return (0.0, frame_i16)

        ns._backend = _StubBackend()
        ns._method = "rnnoise"
        ns._source_sample_rate = RNNOISE_SAMPLE_RATE
        return ns, received_frames

    def test_clip_applied_before_int16_cast(self):
        ns, received_frames = self._make_stub_ns()
        frame = np.full(_RNNOISE_FRAME_SIZE, 1.5, dtype=np.float32)
        result = ns.process(frame, RNNOISE_SAMPLE_RATE)
        assert len(received_frames) == 1
        i16 = received_frames[0]
        assert np.all(i16 == _INT16_SCALE), f"clip not applied: max={i16.max()}, min={i16.min()}"
        assert result is not None
        assert np.allclose(np.ravel(result), 1.0, atol=1e-3)

    def test_negative_peak_clipped(self):
        ns, received_frames = self._make_stub_ns()
        frame = np.full(_RNNOISE_FRAME_SIZE, -1.5, dtype=np.float32)
        ns.process(frame, RNNOISE_SAMPLE_RATE)
        i16 = received_frames[0]
        assert np.all(i16 == -_INT16_SCALE), f"negative clip not applied: max={i16.max()}, min={i16.min()}"

    def test_in_range_audio_unchanged_by_clip(self):
        ns, received_frames = self._make_stub_ns()
        t = np.linspace(0, 1.0, _RNNOISE_FRAME_SIZE, endpoint=False, dtype=np.float32)
        frame = (0.9 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        ns.process(frame, RNNOISE_SAMPLE_RATE)
        i16 = received_frames[0]
        expected = (frame * _INT16_SCALE).astype(np.int16)
        np.testing.assert_array_equal(i16, expected)


class TestNoiseSuppressorResamplerIntegration:
    def test_resamplers_created_lazily(self):
        ns = NoiseSuppressor(method="none", sample_rate=16000)
        assert ns._upsampler is None
        assert ns._downsampler is None
        ns._ensure_resamplers(16000)
        assert ns._upsampler is not None
        assert ns._downsampler is not None

    def test_resamplers_skipped_at_native_rate(self):
        ns = NoiseSuppressor(method="none", sample_rate=48000)
        ns._ensure_resamplers(48000)
        assert ns._upsampler is None
        assert ns._downsampler is None

    def test_resamplers_reused_across_calls(self):
        ns = NoiseSuppressor(method="none", sample_rate=16000)
        ns._ensure_resamplers(16000)
        up1 = ns._upsampler
        down1 = ns._downsampler
        ns._ensure_resamplers(16000)
        assert ns._upsampler is up1
        assert ns._downsampler is down1

    def test_resamplers_recreated_on_rate_change(self):
        ns = NoiseSuppressor(method="none", sample_rate=16000)
        ns._ensure_resamplers(16000)
        up1 = ns._upsampler
        ns._ensure_resamplers(22050)
        assert ns._upsampler is not up1
        assert ns._resampler_rate == 22050

    def test_reset_clears_resampler_state(self):
        ns = NoiseSuppressor(method="none", sample_rate=16000)
        ns._ensure_resamplers(16000)
        ns._upsampler.process(np.random.randn(100).astype(np.float32) * 0.1)
        assert ns._upsampler._in_total > 0
        ns.reset()
        assert ns._upsampler._in_total == 0
        assert ns._upsampler._out_total == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
