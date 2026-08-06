"""Tests for the per-chunk pre-allocation optimization of the audio filters.

Verifies that the pre-allocated working buffers (gain, output, level-estimator,
band-sum, RNNoise result) produce byte-identical output to a fresh-allocation
reference implementation. The reference functions replicate the pre-optimization
algorithm exactly (``np.power``, ``np.where``, ``np.concatenate``, fresh
``.astype`` copies) so any drift introduced by the in-place ``np.copyto`` /
``out=`` pattern is caught as a byte mismatch.

Also verifies the structural contract:
  * buffers are lazy-allocated on the first ``process()`` call (start as None)
  * buffers are reused across same-size calls (identity stable)
  * buffers grow to accommodate a larger chunk
  * ``reset()`` zeros every pre-allocated working buffer (privacy pattern)
"""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server._audio_constants import RNNOISE_SAMPLE_RATE
from voice_typer.server.audio_filters.compressor import Compressor
from voice_typer.server.audio_filters.equalizer import Equalizer
from voice_typer.server.audio_filters.limiter import Limiter
from voice_typer.server.audio_filters.noise_gate import NoiseGate
from voice_typer.server.audio_filters.noise_suppressor import (
    _FLOAT_TO_INT16_MAX,
    _RNNOISE_FRAME_SIZE,
    NoiseSuppressor,
)

_SR = 16000


def _sine_chunk(n: int, freq: float = 440.0, amp: float = 0.5) -> np.ndarray:
    t = np.linspace(0, n / _SR, n, endpoint=False)
    return (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


# ═══════════════════════════════════════════════════════════════════════════
# Compressor: byte-identical to fresh-allocation reference
# ═══════════════════════════════════════════════════════════════════════════


def _compressor_reference_process(
    comp: Compressor, audio: np.ndarray, sample_rate: int
) -> np.ndarray:
    """Fresh-allocation reference (mirrors the pre-optimization gain stage).

    Uses ``np.power(10.0, gain_db / 20.0) * output_gain`` (3 fresh arrays),
    ``np.where(above_floor, gain, output_gain)`` (1 fresh array), and
    ``(samples.astype(float64) * gain).astype(float32)`` (3 fresh arrays).
    """
    samples = np.ravel(audio).astype(np.float32, copy=False)
    n = len(samples)
    if n == 0:
        return audio

    abs_x = np.abs(samples).astype(np.float64)

    attack_b = comp._attack_b
    attack_a = comp._attack_a
    release_b = comp._release_b
    release_a = comp._release_a
    zi = np.array([comp._envelope], dtype=np.float64)
    attack_env, _ = pytest.importorskip("scipy.signal").lfilter(
        attack_b, attack_a, abs_x, zi=zi
    )
    zi = np.array([comp._envelope], dtype=np.float64)
    release_env, _ = pytest.importorskip("scipy.signal").lfilter(
        release_b, release_a, abs_x, zi=zi
    )
    env = np.maximum(attack_env, release_env)

    above_floor = env > 1e-10
    env_db = np.where(above_floor, env, 1.0)
    env_db = np.log10(env_db)
    env_db = env_db * 20.0
    gain_db = -comp._slope * env_db + comp._slope * comp._threshold_db
    gain_db = np.minimum(gain_db, 0.0)
    gain = np.power(10.0, gain_db / 20.0) * comp._output_gain
    gain = np.where(above_floor, gain, comp._output_gain)

    output = (samples.astype(np.float64) * gain).astype(np.float32)
    comp._envelope = float(env[-1])
    return output.reshape(audio.shape)


class TestCompressorByteIdentical:
    """Pre-allocated gain/output buffers must not change the output."""

    def test_single_chunk_byte_identical(self) -> None:
        sr = _SR
        c_opt = Compressor(threshold_db=-18.0, ratio=4.0, output_gain_db=-2.0, sample_rate=sr)
        c_ref = Compressor(threshold_db=-18.0, ratio=4.0, output_gain_db=-2.0, sample_rate=sr)
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal(2048) * 0.4).astype(np.float32)

        out_opt = c_opt.process(audio, sr)
        out_ref = _compressor_reference_process(c_ref, audio, sr)

        assert out_opt is not None
        assert out_opt.dtype == np.float32
        assert out_opt.shape == audio.shape
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_multi_chunk_byte_identical_with_state_carry(self) -> None:
        sr = _SR
        c_opt = Compressor(threshold_db=-20.0, ratio=3.0, sample_rate=sr)
        c_ref = Compressor(threshold_db=-20.0, ratio=3.0, sample_rate=sr)
        rng = np.random.default_rng(123)
        audio = (rng.standard_normal(8192) * 0.5).astype(np.float32)

        chunk_sizes = [512, 1024, 2048, 4096, 1024, 512]
        offset = 0
        for cs in chunk_sizes:
            chunk = audio[offset : offset + cs]
            offset += cs
            out_opt = c_opt.process(chunk, sr)
            out_ref = _compressor_reference_process(c_ref, chunk, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            c_ref._envelope = c_opt._envelope

    def test_quiet_audio_byte_identical(self) -> None:
        """Below-threshold audio (gain=1) must also match byte-for-byte."""
        sr = _SR
        c_opt = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        c_ref = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        quiet = np.full(4096, 0.01, dtype=np.float32)

        out_opt = c_opt.process(quiet, sr)
        out_ref = _compressor_reference_process(c_ref, quiet, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_loud_audio_byte_identical(self) -> None:
        """Above-threshold audio (gain < 1) must also match byte-for-byte."""
        sr = _SR
        c_opt = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        c_ref = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        loud = np.full(16000, 0.8, dtype=np.float32)

        out_opt = c_opt.process(loud, sr)
        out_ref = _compressor_reference_process(c_ref, loud, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_buffer_reuse_across_calls_byte_identical(self) -> None:
        """Same-size chunks reuse the buffer — output still byte-identical."""
        sr = _SR
        c_opt = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        c_ref = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        rng = np.random.default_rng(31337)
        cs = 1024

        for _ in range(5):
            chunk = (rng.standard_normal(cs) * 0.4).astype(np.float32)
            out_opt = c_opt.process(chunk, sr)
            out_ref = _compressor_reference_process(c_ref, chunk, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            c_ref._envelope = c_opt._envelope


# ═══════════════════════════════════════════════════════════════════════════
# Limiter: byte-identical to fresh-allocation reference
# ═══════════════════════════════════════════════════════════════════════════


def _limiter_reference_process(
    lim: Limiter, audio: np.ndarray, sample_rate: int
) -> np.ndarray:
    """Fresh-allocation reference (mirrors the pre-optimization gain stage)."""
    samples = np.ravel(audio).astype(np.float32, copy=False)
    n = len(samples)
    if n == 0:
        return audio

    abs_x = np.abs(samples).astype(np.float64)

    zi = np.array([lim._envelope], dtype=np.float64)
    attack_env, _ = pytest.importorskip("scipy.signal").lfilter(
        lim._attack_b, lim._attack_a, abs_x, zi=zi
    )
    zi = np.array([lim._envelope], dtype=np.float64)
    release_env, _ = pytest.importorskip("scipy.signal").lfilter(
        lim._release_b, lim._release_a, abs_x, zi=zi
    )
    env = np.maximum(attack_env, release_env)

    above_floor = env > 1e-10
    env_db = np.where(above_floor, env, 1.0)
    env_db = np.log10(env_db)
    env_db = env_db * 20.0
    gain_db = -lim._slope * env_db + lim._slope * lim._threshold_db
    gain_db = np.minimum(gain_db, 0.0)
    gain = np.power(10.0, gain_db / 20.0)
    gain = np.where(above_floor, gain, 1.0)

    output = (samples.astype(np.float64) * gain).astype(np.float32)
    lim._envelope = float(env[-1])
    return output.reshape(audio.shape)


class TestLimiterByteIdentical:
    """Pre-allocated gain/output buffers must not change the output."""

    def test_single_chunk_byte_identical(self) -> None:
        sr = _SR
        lim_opt = Limiter(ceiling_db=-6.0, sample_rate=sr)
        lim_ref = Limiter(ceiling_db=-6.0, sample_rate=sr)
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal(2048) * 0.6).astype(np.float32)

        out_opt = lim_opt.process(audio, sr)
        out_ref = _limiter_reference_process(lim_ref, audio, sr)

        assert out_opt is not None
        assert out_opt.dtype == np.float32
        assert out_opt.shape == audio.shape
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_multi_chunk_byte_identical_with_state_carry(self) -> None:
        sr = _SR
        lim_opt = Limiter(ceiling_db=-6.0, sample_rate=sr)
        lim_ref = Limiter(ceiling_db=-6.0, sample_rate=sr)
        rng = np.random.default_rng(123)
        audio = (rng.standard_normal(8192) * 0.7).astype(np.float32)

        chunk_sizes = [512, 1024, 2048, 4096, 1024, 512]
        offset = 0
        for cs in chunk_sizes:
            chunk = audio[offset : offset + cs]
            offset += cs
            out_opt = lim_opt.process(chunk, sr)
            out_ref = _limiter_reference_process(lim_ref, chunk, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            lim_ref._envelope = lim_opt._envelope

    def test_loud_audio_byte_identical(self) -> None:
        """Above-ceiling audio (gain < 1) must match byte-for-byte."""
        sr = _SR
        lim_opt = Limiter(ceiling_db=-6.0, sample_rate=sr)
        lim_ref = Limiter(ceiling_db=-6.0, sample_rate=sr)
        loud = np.full(16000, 0.99, dtype=np.float32)

        out_opt = lim_opt.process(loud, sr)
        out_ref = _limiter_reference_process(lim_ref, loud, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_quiet_audio_byte_identical(self) -> None:
        """Below-ceiling audio (gain=1) must match byte-for-byte."""
        sr = _SR
        lim_opt = Limiter(ceiling_db=-6.0, sample_rate=sr)
        lim_ref = Limiter(ceiling_db=-6.0, sample_rate=sr)
        quiet = np.full(4096, 0.1, dtype=np.float32)

        out_opt = lim_opt.process(quiet, sr)
        out_ref = _limiter_reference_process(lim_ref, quiet, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)


# ═══════════════════════════════════════════════════════════════════════════
# Structural: buffers are lazy-allocated, reused, grown, zeroed on reset
# ═══════════════════════════════════════════════════════════════════════════


class TestCompressorBufferLifecycle:
    def test_buffers_start_none(self) -> None:
        c = Compressor(sample_rate=_SR)
        assert c._gain_buf is None
        assert c._output_f64_buf is None
        assert c._output_f32_buf is None

    def test_buffers_allocated_on_first_process(self) -> None:
        c = Compressor(sample_rate=_SR)
        c.process(_sine_chunk(1024), _SR)
        assert c._gain_buf is not None
        assert c._output_f64_buf is not None
        assert c._output_f32_buf is not None
        assert c._gain_buf.dtype == np.float64
        assert c._output_f64_buf.dtype == np.float64
        assert c._output_f32_buf.dtype == np.float32

    def test_buffers_reused_across_same_size_calls(self) -> None:
        c = Compressor(sample_rate=_SR)
        c.process(_sine_chunk(1024), _SR)
        g1 = c._gain_buf
        c.process(_sine_chunk(1024), _SR)
        assert c._gain_buf is g1

    def test_buffers_grow_for_larger_chunk(self) -> None:
        c = Compressor(sample_rate=_SR)
        c.process(_sine_chunk(512), _SR)
        g_small = c._gain_buf
        assert g_small.shape[0] >= 512
        c.process(_sine_chunk(4096), _SR)
        g_large = c._gain_buf
        assert g_large.shape[0] >= 4096
        assert g_large is not g_small

    def test_reset_zeros_buffers(self) -> None:
        c = Compressor(sample_rate=_SR)
        c.process(_sine_chunk(1024), _SR)
        assert not np.all(c._gain_buf == 0)
        assert not np.all(c._output_f64_buf == 0)
        assert not np.all(c._output_f32_buf == 0)
        c.reset()
        assert np.all(c._gain_buf == 0)
        assert np.all(c._output_f64_buf == 0)
        assert np.all(c._output_f32_buf == 0)

    def test_reset_before_process_does_not_crash(self) -> None:
        c = Compressor(sample_rate=_SR)
        assert c._gain_buf is None
        c.reset()
        assert c._gain_buf is None


class TestLimiterBufferLifecycle:
    def test_buffers_start_none(self) -> None:
        lim = Limiter(sample_rate=_SR)
        assert lim._gain_buf is None
        assert lim._output_f64_buf is None
        assert lim._output_f32_buf is None

    def test_buffers_allocated_on_first_process(self) -> None:
        lim = Limiter(sample_rate=_SR)
        lim.process(_sine_chunk(1024), _SR)
        assert lim._gain_buf is not None
        assert lim._output_f64_buf is not None
        assert lim._output_f32_buf is not None

    def test_reset_zeros_buffers(self) -> None:
        lim = Limiter(sample_rate=_SR)
        lim.process(_sine_chunk(1024), _SR)
        assert not np.all(lim._gain_buf == 0)
        assert not np.all(lim._output_f32_buf == 0)
        lim.reset()
        assert np.all(lim._gain_buf == 0)
        assert np.all(lim._output_f64_buf == 0)
        assert np.all(lim._output_f32_buf == 0)


class TestEqualizerBufferLifecycle:
    def test_zi_buffers_eagerly_allocated(self) -> None:
        eq = Equalizer(sample_rate=_SR)
        # 1-element zi buffers are eagerly allocated in __init__
        # (mirror compressor._zi_buf at line 76).
        assert eq._low_zi_buf is not None
        assert eq._high_zi_buf is not None
        assert eq._low_zi_buf.shape == (1,)
        assert eq._high_zi_buf.shape == (1,)
        assert eq._low_zi_buf.dtype == np.float64

    def test_lazy_buffers_start_none(self) -> None:
        eq = Equalizer(sample_rate=_SR)
        assert eq._x_f64_buf is None
        assert eq._output_buf is None
        assert eq._tmp_buf is None

    def test_lazy_buffers_allocated_on_first_process(self) -> None:
        eq = Equalizer(sample_rate=_SR)
        eq.process(_sine_chunk(1024), _SR)
        assert eq._x_f64_buf is not None
        assert eq._output_buf is not None
        assert eq._tmp_buf is not None
        assert eq._x_f64_buf.dtype == np.float64

    def test_reset_zeros_lazy_buffers(self) -> None:
        eq = Equalizer(sample_rate=_SR)
        eq.process(_sine_chunk(1024), _SR)
        assert not np.all(eq._x_f64_buf == 0)
        assert not np.all(eq._output_buf == 0)
        eq.reset()
        assert np.all(eq._x_f64_buf == 0)
        assert np.all(eq._output_buf == 0)
        assert np.all(eq._tmp_buf == 0)
        assert np.all(eq._low_zi_buf == 0)
        assert np.all(eq._high_zi_buf == 0)


class TestNoiseGateBufferLifecycle:
    def test_buffers_start_none(self) -> None:
        g = NoiseGate(sample_rate=_SR)
        assert g._abs_buf is None
        assert g._i_arr_buf is None
        assert g._y_buf is None
        assert g._level_arr_buf is None
        assert g._attenuation_buf is None
        assert g._output_f64_buf is None
        assert g._output_f32_buf is None

    def test_buffers_allocated_on_first_process(self) -> None:
        g = NoiseGate(sample_rate=_SR)
        g.process(_sine_chunk(1024), _SR)
        assert g._abs_buf is not None
        assert g._i_arr_buf is not None
        assert g._y_buf is not None
        assert g._level_arr_buf is not None
        assert g._attenuation_buf is not None
        assert g._output_f64_buf is not None
        assert g._output_f32_buf is not None
        assert g._abs_buf.dtype == np.float64
        assert g._output_f32_buf.dtype == np.float32

    def test_i_arr_cached_and_sliced(self) -> None:
        """_i_arr_buf caches np.arange(cap) and is sliced to [:n]."""
        g = NoiseGate(sample_rate=_SR)
        g.process(_sine_chunk(1024), _SR)
        # After a 1024-sample chunk, _i_arr_buf has >= 1024 elements.
        buf = g._i_arr_buf
        assert buf is not None
        # The first 1024 values must be [0, 1, 2, ..., 1023].
        np.testing.assert_array_equal(buf[:1024], np.arange(1024, dtype=np.float64))

    def test_buffers_reused_across_same_size_calls(self) -> None:
        g = NoiseGate(sample_rate=_SR)
        g.process(_sine_chunk(1024), _SR)
        abs1 = g._abs_buf
        g.process(_sine_chunk(1024), _SR)
        assert g._abs_buf is abs1

    def test_buffers_grow_for_larger_chunk(self) -> None:
        g = NoiseGate(sample_rate=_SR)
        g.process(_sine_chunk(512), _SR)
        small = g._abs_buf
        g.process(_sine_chunk(4096), _SR)
        large = g._abs_buf
        assert large.shape[0] >= 4096
        assert large is not small

    def test_reset_zeros_buffers(self) -> None:
        g = NoiseGate(sample_rate=_SR)
        g.process(_sine_chunk(1024), _SR)
        assert not np.all(g._abs_buf == 0)
        assert not np.all(g._attenuation_buf == 0)
        assert not np.all(g._output_f32_buf == 0)
        g.reset()
        assert np.all(g._abs_buf == 0)
        assert np.all(g._y_buf == 0)
        assert np.all(g._level_arr_buf == 0)
        assert np.all(g._attenuation_buf == 0)
        assert np.all(g._output_f64_buf == 0)
        assert np.all(g._output_f32_buf == 0)
        assert np.all(g._i_arr_buf == 0)

    def test_reset_before_process_does_not_crash(self) -> None:
        g = NoiseGate(sample_rate=_SR)
        assert g._abs_buf is None
        g.reset()
        assert g._abs_buf is None


# ═══════════════════════════════════════════════════════════════════════════
# NoiseSuppressor: byte-identical to fresh-allocation reference (stub backend)
# ═══════════════════════════════════════════════════════════════════════════


def _make_stub_ns(sample_rate: int = RNNOISE_SAMPLE_RATE):
    """Build a NoiseSuppressor with a stub RNNoise backend.

    The stub returns the input int16 frame unchanged so we can verify the
    output-conversion path (int16 -> float64 -> /32767 -> resample) is
    byte-identical between the optimized in-place path and a fresh-allocation
    reference. Uses the native RNNoise rate (48kHz) so no resampling is
    involved — the test isolates the ``_process_rnnoise`` frame loop.
    """
    ns = NoiseSuppressor(method="none", sample_rate=sample_rate)

    class _StubBackend:
        channels = 1

        def denoise_frame(self, frame_i16):
            return (0.0, frame_i16)

    ns._backend = _StubBackend()
    ns._method = "rnnoise"
    ns._source_sample_rate = sample_rate
    return ns


def _ns_reference_process_rnnoise(
    ns: NoiseSuppressor, samples: np.ndarray, sample_rate: int
) -> np.ndarray | None:
    """Fresh-allocation reference for ``_process_rnnoise`` (pre-optimization).

    Uses ``output_frames.append(cleaned_i16[0].astype(np.float32) /
    _FLOAT_TO_INT16_MAX)`` (2 fresh arrays per frame) and
    ``np.concatenate(output_frames)`` (1 fresh array per call).
    """
    ns._ensure_resamplers(sample_rate)
    up = ns._upsampler.process(samples) if ns._upsampler is not None else samples
    combined = np.concatenate([ns._carry, up])
    n_full = len(combined) // _RNNOISE_FRAME_SIZE
    remainder = len(combined) - n_full * _RNNOISE_FRAME_SIZE
    if n_full == 0:
        ns._carry = combined
        return None
    ns._backend.channels = 1
    output_frames = []
    for i in range(n_full):
        start = i * _RNNOISE_FRAME_SIZE
        frame = combined[start : start + _RNNOISE_FRAME_SIZE]
        frame_f32 = np.clip(frame, -1.0, 1.0)
        frame_f32 = frame_f32 * _FLOAT_TO_INT16_MAX
        frame_i16 = frame_f32.astype(np.int16)
        _, cleaned_i16 = ns._backend.denoise_frame(frame_i16[np.newaxis, :])
        output_frames.append(cleaned_i16[0].astype(np.float32) / _FLOAT_TO_INT16_MAX)
    if remainder > 0:
        ns._carry = combined[n_full * _RNNOISE_FRAME_SIZE :]
    else:
        ns._carry = np.array([], dtype=np.float32)
    result_48k = np.concatenate(output_frames)
    result = ns._downsampler.process(result_48k) if ns._downsampler is not None else result_48k
    target_len = len(samples)
    if len(result) >= target_len:
        result = result[:target_len]
    else:
        padded = np.zeros(target_len, dtype=np.float32)
        padded[: len(result)] = result
        result = padded
    return result.astype(np.float32, copy=False).reshape(samples.shape)


class TestNoiseSuppressorByteIdentical:
    """The pre-allocated ``_result_48k_buf`` path must match fresh-allocation."""

    def test_single_frame_byte_identical(self) -> None:
        sr = RNNOISE_SAMPLE_RATE
        ns_opt = _make_stub_ns(sr)
        ns_ref = _make_stub_ns(sr)
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal(_RNNOISE_FRAME_SIZE) * 0.3).astype(np.float32)

        out_opt = ns_opt.process(audio, sr)
        out_ref = _ns_reference_process_rnnoise(ns_ref, audio, sr)
        assert out_opt is not None
        assert out_ref is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_multi_frame_byte_identical(self) -> None:
        sr = RNNOISE_SAMPLE_RATE
        ns_opt = _make_stub_ns(sr)
        ns_ref = _make_stub_ns(sr)
        rng = np.random.default_rng(123)
        audio = (rng.standard_normal(_RNNOISE_FRAME_SIZE * 5) * 0.4).astype(np.float32)

        out_opt = ns_opt.process(audio, sr)
        out_ref = _ns_reference_process_rnnoise(ns_ref, audio, sr)
        assert out_opt is not None
        assert out_ref is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_multi_chunk_byte_identical_with_carry(self) -> None:
        """Multiple sub-frame chunks: carry buffers must stay in sync."""
        sr = RNNOISE_SAMPLE_RATE
        ns_opt = _make_stub_ns(sr)
        ns_ref = _make_stub_ns(sr)
        rng = np.random.default_rng(256)
        audio = (rng.standard_normal(_RNNOISE_FRAME_SIZE * 3 + 100) * 0.4).astype(np.float32)

        chunk_sizes = [480, 480, 480, 460]  # sub-frame chunks force carry
        offset = 0
        for cs in chunk_sizes:
            chunk = audio[offset : offset + cs]
            offset += cs
            out_opt = ns_opt.process(chunk, sr)
            out_ref = _ns_reference_process_rnnoise(ns_ref, chunk, sr)
            # Both may be None (buffering) or both non-None — must match.
            if out_opt is None:
                assert out_ref is None
            else:
                assert out_ref is not None
                np.testing.assert_array_equal(out_opt, out_ref)
            # Sync carry state.
            ns_ref._carry = ns_opt._carry.copy()

    def test_in_range_audio_byte_identical(self) -> None:
        """In-range float32 audio must convert to int16 and back identically."""
        sr = RNNOISE_SAMPLE_RATE
        ns_opt = _make_stub_ns(sr)
        ns_ref = _make_stub_ns(sr)
        t = np.linspace(0, 1.0, _RNNOISE_FRAME_SIZE, endpoint=False, dtype=np.float32)
        audio = (0.7 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

        out_opt = ns_opt.process(audio, sr)
        out_ref = _ns_reference_process_rnnoise(ns_ref, audio, sr)
        assert out_opt is not None
        assert out_ref is not None
        np.testing.assert_array_equal(out_opt, out_ref)


class TestNoiseSuppressorBufferLifecycle:
    def test_result_buf_start_none(self) -> None:
        ns = _make_stub_ns(RNNOISE_SAMPLE_RATE)
        assert ns._result_48k_buf is None
        assert ns._padded_buf is None

    def test_result_buf_allocated_on_first_process(self) -> None:
        ns = _make_stub_ns(RNNOISE_SAMPLE_RATE)
        ns.process(_sine_chunk(_RNNOISE_FRAME_SIZE, amp=0.3), RNNOISE_SAMPLE_RATE)
        assert ns._result_48k_buf is not None
        assert ns._result_48k_buf.dtype == np.float64

    def test_reset_zeros_result_and_padded_bufs(self) -> None:
        ns = _make_stub_ns(RNNOISE_SAMPLE_RATE)
        ns.process(_sine_chunk(_RNNOISE_FRAME_SIZE, amp=0.5), RNNOISE_SAMPLE_RATE)
        assert ns._result_48k_buf is not None
        assert not np.all(ns._result_48k_buf == 0)
        ns.reset()
        assert np.all(ns._result_48k_buf == 0)
        # _padded_buf may still be None if the padding path wasn't hit.
        if ns._padded_buf is not None:
            assert np.all(ns._padded_buf == 0)


# ═══════════════════════════════════════════════════════════════════════════
# Cross-filter: chunk-size variation doesn't break byte-identical output
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossFilterChunkSizeVariation:
    """A larger chunk after a smaller one must reallocate and stay correct."""

    def test_compressor_grow_then_shrink(self) -> None:
        sr = _SR
        c_opt = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        c_ref = Compressor(threshold_db=-18.0, ratio=4.0, sample_rate=sr)
        rng = np.random.default_rng(7)
        for cs in [256, 1024, 4096, 128, 2048]:
            audio = (rng.standard_normal(cs) * 0.4).astype(np.float32)
            out_opt = c_opt.process(audio, sr)
            out_ref = _compressor_reference_process(c_ref, audio, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            c_ref._envelope = c_opt._envelope

    def test_noise_gate_grow_then_shrink(self) -> None:
        sr = _SR
        g_opt = NoiseGate(sample_rate=sr)
        g_ref = NoiseGate(sample_rate=sr)
        rng = np.random.default_rng(7)
        for cs in [256, 1024, 4096, 128, 2048]:
            audio = (rng.standard_normal(cs) * 0.4).astype(np.float32)
            out_opt = g_opt.process(audio, sr)
            # Build reference using the same algorithm but fresh allocations.
            # Reuse the gate's internal state by running a parallel instance
            # and syncing state after each call.
            from tests.test_audio_filters_lazy_imports import _noise_gate_reference_process

            out_ref = _noise_gate_reference_process(g_ref, audio, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            g_ref._level = g_opt._level
            g_ref._is_open = g_opt._is_open
            g_ref._attenuation = g_opt._attenuation
            g_ref._held_time = g_opt._held_time
