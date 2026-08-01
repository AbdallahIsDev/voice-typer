"""Regression tests: audio filter reset() must zero pre-allocated working buffers.

Before the fix, each filter's ``reset()`` zeroed the IIR ``zi`` state arrays
and the RNNoise ``_carry`` buffer but left the pre-allocated *working* buffers
populated with raw-audio-derived data from the last ``process()`` call:

- ``Compressor._env_db_buf``        -- dB-domain envelope of last chunk
- ``Limiter._env_db_buf``           -- dB-domain envelope of last chunk
- ``Equalizer._delay_buf``          -- 3-sample delay line (raw input samples)
- ``NoiseSuppressor._frame_f32_buf``-- per-frame float32 conversion buffer
- ``NoiseSuppressor._frame_i16_buf``-- per-frame int16 conversion buffer
- ``_StreamingResampler._x_up_buf`` -- upsampled raw audio samples

Those buffers linger in process memory until the numpy allocator reuses the
block -- a privacy regression that ``FilterChain.swap`` /
``AudioProcessor.reset`` / ``secure_clear_caches`` were supposed to prevent.

These tests pin the fix: after ``reset()``, every pre-allocated working
buffer must be all-zeros (``np.all(buf == 0)``).
"""

from __future__ import annotations

import numpy as np
from voice_typer.server.audio_filters.compressor import Compressor
from voice_typer.server.audio_filters.equalizer import Equalizer
from voice_typer.server.audio_filters.limiter import Limiter
from voice_typer.server.audio_filters.noise_suppressor import (
    NoiseSuppressor,
    _StreamingResampler,
)

_SR = 16000


def _nonzero_chunk(n: int = 1024) -> np.ndarray:
    """Return a non-zero float32 audio chunk (0.5 amplitude 440 Hz sine)."""
    t = np.linspace(0, n / _SR, n, endpoint=False)
    return (0.5 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)


# ─── Compressor ───────────────────────────────────────────────────────────


def test_compressor_reset_zeros_env_db_buf():
    c = Compressor(sample_rate=_SR)
    c.process(_nonzero_chunk(), _SR)
    assert c._env_db_buf is not None
    # process() wrote non-zero gain_db values into _env_db_buf[:n]
    assert not np.all(c._env_db_buf == 0)
    c.reset()
    assert c._env_db_buf is not None
    assert np.all(c._env_db_buf == 0)


def test_compressor_reset_before_process_does_not_crash():
    """reset() before the lazy buffer is allocated must not crash (buf is None)."""
    c = Compressor(sample_rate=_SR)
    assert c._env_db_buf is None
    c.reset()  # no-op on the None buffer
    assert c._env_db_buf is None


# ─── Limiter ──────────────────────────────────────────────────────────────


def test_limiter_reset_zeros_env_db_buf():
    lim = Limiter(sample_rate=_SR)
    lim.process(_nonzero_chunk(), _SR)
    assert lim._env_db_buf is not None
    assert not np.all(lim._env_db_buf == 0)
    lim.reset()
    assert lim._env_db_buf is not None
    assert np.all(lim._env_db_buf == 0)


def test_limiter_reset_before_process_does_not_crash():
    lim = Limiter(sample_rate=_SR)
    assert lim._env_db_buf is None
    lim.reset()
    assert lim._env_db_buf is None


# ─── Equalizer ────────────────────────────────────────────────────────────


def test_equalizer_reset_zeros_delay_buf():
    eq = Equalizer(sample_rate=_SR)
    eq.process(_nonzero_chunk(), _SR)
    assert eq._delay_buf is not None
    # process() wrote the input samples (non-zero sine) into _delay_buf[3:n]
    assert not np.all(eq._delay_buf == 0)
    eq.reset()
    assert eq._delay_buf is not None
    assert np.all(eq._delay_buf == 0)


def test_equalizer_reset_before_process_does_not_crash():
    eq = Equalizer(sample_rate=_SR)
    assert eq._delay_buf is None
    eq.reset()
    assert eq._delay_buf is None


# ─── NoiseSuppressor (per-frame conversion buffers) ───────────────────────


def test_noise_suppressor_reset_zeros_frame_buffers():
    """reset() must zero _frame_f32_buf and _frame_i16_buf.

    These buffers hold raw-audio-derived samples passed to the RNNoise
    backend. If pyrnnoise isn't installed the backend falls back to
    ``"none"`` and ``process()`` is a passthrough -- in that case we
    manually populate the buffers (simulating what ``_process_rnnoise``
    does) so the reset() zeroing is exercised regardless of backend
    availability.
    """
    ns = NoiseSuppressor(method="rnnoise", sample_rate=_SR)
    ns.process(_nonzero_chunk(), _SR)
    # Ensure the buffers are non-zero before reset -- either process()
    # populated them via the rnnoise backend, or we simulate it.
    ns._frame_f32_buf[:] = 0.5
    ns._frame_i16_buf[:] = 12345
    assert not np.all(ns._frame_f32_buf == 0)
    assert not np.all(ns._frame_i16_buf == 0)
    ns.reset()
    assert np.all(ns._frame_f32_buf == 0)
    assert np.all(ns._frame_i16_buf == 0)


def test_noise_suppressor_reset_zeros_resampler_x_up_buf():
    """NoiseSuppressor.reset() must cascade to _StreamingResampler._x_up_buf.

    Instantiated at 16 kHz so the streaming resamplers are created
    (16k -> 48k -> 16k round-trip). Processing a chunk populates
    ``_upsampler._x_up_buf``; reset() must zero it via the cascaded
    ``_upsampler.reset()`` call.
    """
    ns = NoiseSuppressor(method="none", sample_rate=_SR)
    # Force the resamplers to exist by calling _ensure_resamplers
    # (process() with method="none" returns early, so the resamplers
    # would otherwise never be created).
    ns._ensure_resamplers(_SR)
    assert ns._upsampler is not None
    ns._upsampler.process(_nonzero_chunk(64))
    assert ns._upsampler._x_up_buf is not None
    assert not np.all(ns._upsampler._x_up_buf == 0)
    ns.reset()
    assert ns._upsampler._x_up_buf is not None
    assert np.all(ns._upsampler._x_up_buf == 0)


# ─── _StreamingResampler (upsample buffer, direct unit test) ──────────────


def test_streaming_resampler_reset_zeros_x_up_buf():
    # 16k -> 48k upsampler (up=3, down=1)
    rs = _StreamingResampler(up=3, down=1)
    rs.process(_nonzero_chunk(64))
    assert rs._x_up_buf is not None
    # x_up[::up] = x64 writes non-zero values at strided positions
    assert not np.all(rs._x_up_buf == 0)
    rs.reset()
    assert rs._x_up_buf is not None
    assert np.all(rs._x_up_buf == 0)


def test_streaming_resampler_reset_before_process_does_not_crash():
    rs = _StreamingResampler(up=3, down=1)
    assert rs._x_up_buf is None
    rs.reset()
    assert rs._x_up_buf is None
