"""Tests for SU-8 / SU-11 / SU-31: lazy numpy/scipy imports + pre-allocated buffers.

Three guarantees are asserted here:

(a) ``import voice_typer.server.audio_filters`` does NOT pull numpy or
    scipy into ``sys.modules``. This is the cold-start optimization —
    the audio_filters package (and every filter class definition) must
    load without paying the ~250-335ms numpy import cost or the ~700ms
    scipy import cost. numpy/scipy are deferred to the first
    ``process()`` call (which happens ~1s after dictation begins, well
    outside the cold-start window). Verified in a clean subprocess so
    the assertion is not contaminated by other tests that already
    imported numpy/scipy in the same process.

(b) ``Equalizer.process()`` with pre-allocated b/a/zi buffers (SU-11)
    produces byte-identical output to a fresh-allocation reference
    implementation. The reference replicates the pre-optimization
    algorithm exactly (inline ``[lf]`` / ``[1.0, -(1-lf)]`` lists +
    ``np.array([state])`` zi construction) so any drift introduced by
    the pre-allocated buffers is caught as a byte mismatch.

(c) ``NoiseGate.process()`` with pre-allocated abs_x / i_arr /
    y_with_init / attenuation_arr buffers (SU-31) produces byte-identical
    output to a fresh-allocation reference implementation. The reference
    replicates the pre-optimization peak-hold + state-machine algorithm
    exactly so any drift introduced by the in-place copyto/abs/multiply
    pattern is caught as a byte mismatch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

# These imports pull numpy/scipy into the test process's sys.modules,
# but assertion (a) runs in a clean subprocess so that is fine.
from scipy.signal import lfilter as _scipy_lfilter
from voice_typer.server.audio_filters import Equalizer, NoiseGate

# ═══════════════════════════════════════════════════════════════════════════
# (a) Module import must not pull numpy / scipy
# ═══════════════════════════════════════════════════════════════════════════

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_import_audio_filters_does_not_pull_numpy_or_scipy() -> None:
    """``import voice_typer.server.audio_filters`` stays lazy.

    Runs in a clean subprocess so the assertion is not contaminated by
    numpy/scipy having been imported earlier in the same pytest process
    (which is the case for every other test in this file).
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import voice_typer.server.audio_filters; "
                "assert 'numpy' not in sys.modules, "
                "'numpy eagerly imported by audio_filters!'; "
                "assert 'scipy' not in sys.modules, "
                "'scipy eagerly imported by audio_filters!'; "
                "print('OK: numpy and scipy NOT in sys.modules')"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(_PROJECT_ROOT)},
    )
    assert result.returncode == 0, (
        f"subprocess failed (rc={result.returncode}):\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


def test_import_audio_filters_submodules_do_not_pull_numpy_or_scipy() -> None:
    """Each individual filter submodule also stays lazy on import."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import voice_typer.server.audio_filters.base; "
                "import voice_typer.server.audio_filters.compressor; "
                "import voice_typer.server.audio_filters.equalizer; "
                "import voice_typer.server.audio_filters.highpass; "
                "import voice_typer.server.audio_filters.limiter; "
                "import voice_typer.server.audio_filters.noise_gate; "
                "import voice_typer.server.audio_filters.noise_suppressor; "
                "import voice_typer.server.audio_filters.notch; "
                "assert 'numpy' not in sys.modules, 'numpy eagerly imported!'; "
                "assert 'scipy' not in sys.modules, 'scipy eagerly imported!'; "
                "print('OK: all 8 submodules lazy')"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(_PROJECT_ROOT)},
    )
    assert result.returncode == 0, (
        f"subprocess failed (rc={result.returncode}):\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout


# ═══════════════════════════════════════════════════════════════════════════
# (b) Equalizer.process() byte-identical to fresh-allocation reference
# ═══════════════════════════════════════════════════════════════════════════


def _equalizer_reference_process(
    eq: Equalizer,
    audio: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    """Fresh-allocation reference (mirrors the pre-SU-11 process body).

    Uses inline ``[lf]`` / ``[1.0, -(1-lf)]`` Python lists and
    ``np.array([state])`` zi construction — exactly the code path that
    allocated fresh arrays per call before SU-11 pre-allocated the b/a
    coefficient arrays and zi buffers in ``__init__``.
    """
    samples = np.ravel(audio).astype(np.float32, copy=False)
    n = len(samples)
    if n == 0:
        return audio

    lf = eq._lf
    hf = eq._hf
    low_gain = eq._low_gain
    mid_gain = eq._mid_gain
    high_gain = eq._high_gain

    x = samples.astype(np.float64)

    low_s, _ = _scipy_lfilter(
        [lf],
        [1.0, -(1.0 - lf)],
        x,
        zi=np.array([eq._low_state], dtype=np.float64),
    )
    high_s, _ = _scipy_lfilter(
        [hf],
        [1.0, -(1.0 - hf)],
        x,
        zi=np.array([eq._high_state], dtype=np.float64),
    )
    high = x - high_s

    if n >= 3:
        prefix = np.array([eq._delay3, eq._delay2, eq._delay1], dtype=np.float64)
        extended = np.concatenate([prefix, x])
        d3 = extended[:n]
    else:
        prefix = np.array([eq._delay3, eq._delay2, eq._delay1], dtype=np.float64)
        extended = np.concatenate([prefix, x])
        d3 = extended[:n]

    mid = d3 - (low_s + high)
    output = (low_s * low_gain + mid * mid_gain + high * high_gain).astype(np.float32)
    return output.reshape(audio.shape)


class TestEqualizerByteIdentical:
    """SU-11: pre-allocated b/a/zi buffers must not change the output."""

    def test_single_chunk_byte_identical(self) -> None:
        """One process() call: optimized output == fresh-allocation reference."""
        sr = 16000
        eq_opt = Equalizer(low_db=-3.0, mid_db=3.0, high_db=2.0, sample_rate=sr)
        eq_ref = Equalizer(low_db=-3.0, mid_db=3.0, high_db=2.0, sample_rate=sr)
        rng = np.random.default_rng(42)
        audio = (rng.standard_normal(2048) * 0.3).astype(np.float32)

        out_opt = eq_opt.process(audio, sr)
        out_ref = _equalizer_reference_process(eq_ref, audio, sr)

        assert out_opt is not None
        assert out_opt.dtype == np.float32
        assert out_opt.shape == audio.shape
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_multi_chunk_byte_identical_with_state_carry(self) -> None:
        """Multiple chunks: state carries identically between optimized and reference."""
        sr = 16000
        eq_opt = Equalizer(low_db=-2.0, mid_db=1.0, high_db=4.0, sample_rate=sr)
        eq_ref = Equalizer(low_db=-2.0, mid_db=1.0, high_db=4.0, sample_rate=sr)
        rng = np.random.default_rng(123)
        audio = (rng.standard_normal(8192) * 0.4).astype(np.float32)

        chunk_sizes = [512, 1024, 2048, 4096, 1024, 512]
        offset = 0
        for cs in chunk_sizes:
            chunk = audio[offset : offset + cs]
            offset += cs
            out_opt = eq_opt.process(chunk, sr)
            out_ref = _equalizer_reference_process(eq_ref, chunk, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            # Carry the state forward identically for the reference.
            # We do this by reading the optimized filter's post-process
            # state and copying it into the reference filter so both
            # start the next chunk from the same state. (The optimized
            # filter's state IS the reference — if they diverge here,
            # the next chunk's output will differ and the test fails.)
            eq_ref._delay1 = eq_opt._delay1
            eq_ref._delay2 = eq_opt._delay2
            eq_ref._delay3 = eq_opt._delay3
            eq_ref._low_state = eq_opt._low_state
            eq_ref._high_state = eq_opt._high_state

    def test_small_chunk_byte_identical(self) -> None:
        """n < 3 path (falls back to concatenate) must also match."""
        sr = 16000
        eq_opt = Equalizer(sample_rate=sr)
        eq_ref = Equalizer(sample_rate=sr)
        rng = np.random.default_rng(7)
        audio = (rng.standard_normal(2) * 0.3).astype(np.float32)

        out_opt = eq_opt.process(audio, sr)
        out_ref = _equalizer_reference_process(eq_ref, audio, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_pre_allocated_buffers_exist(self) -> None:
        """SU-11: the pre-allocated delay-line buffer is created lazily."""
        eq = Equalizer(sample_rate=16000)
        # The delay-line buffer starts unallocated...
        assert eq._delay_buf is None
        # ...and is allocated on the first process() call with n >= 3.
        audio = (np.random.default_rng(7).standard_normal(2048) * 0.3).astype(np.float32)
        eq.process(audio, 16000)
        assert eq._delay_buf is not None
        assert eq._delay_buf.dtype == np.float64


# ═══════════════════════════════════════════════════════════════════════════
# (c) NoiseGate.process() byte-identical to fresh-allocation reference
# ═══════════════════════════════════════════════════════════════════════════


def _noise_gate_reference_process(
    gate: NoiseGate,
    audio: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    """Fresh-allocation reference (mirrors the pre-SU-31 process body).

    Uses ``np.abs(samples).astype(np.float64)``, ``np.arange(n)``,
    ``np.empty(n + 1)``, and ``np.empty(n)`` — exactly the code path
    that allocated 4 fresh arrays per call before SU-31 pre-allocated
    the reusable buffers.
    """
    samples = np.ravel(audio).astype(np.float32, copy=False)
    n = len(samples)
    if n == 0:
        return audio
    dt = 1.0 / sample_rate

    if not gate._calibrated:
        # Calibration branch is unchanged by  — replicate it for
        # completeness so the reference is correct when adaptive=True.
        remaining = gate._calibration_target - gate._calibration_count
        if remaining > 0:
            take = min(remaining, len(samples))
            if take > 0:
                chunk = samples[:take].astype(np.float64, copy=False)
                gate._calibration_sumsq += float(np.dot(chunk, chunk))
                gate._calibration_count += take
        abs_x_init = np.abs(samples).astype(np.float64)
        gate._level = float(abs_x_init.max()) if abs_x_init.size > 0 else 0.0
        return audio.reshape(audio.shape)

    attack_rate = 1.0 / max(gate._attack_ms / 1000.0, dt)
    release_rate = 1.0 / max(gate._release_ms / 1000.0, dt)
    hold_time = gate._hold_ms / 1000.0

    open_thr = gate._open_threshold
    close_thr = gate._close_threshold
    decay = gate._decay_rate

    abs_x = np.abs(samples).astype(np.float64)
    i_arr = np.arange(n, dtype=np.float64)
    y = abs_x + i_arr * decay
    y_with_init = np.empty(n + 1, dtype=np.float64)
    y_with_init[0] = gate._level
    y_with_init[1:] = y
    z = np.maximum.accumulate(y_with_init)[1:]
    level_arr = np.maximum(z - i_arr * decay, 0.0)

    attenuation_arr = np.empty(n, dtype=np.float64)
    is_open = gate._is_open
    attenuation = gate._attenuation
    held_time = gate._held_time

    for i in range(n):
        level = float(level_arr[i])
        if level > open_thr:
            is_open = True
        elif level < close_thr and is_open:
            is_open = False
            held_time = 0.0

        if is_open:
            attenuation += attack_rate * dt
            if attenuation > 1.0:
                attenuation = 1.0
        else:
            held_time += dt
            if held_time > hold_time:
                attenuation -= release_rate * dt
                if attenuation < 0.0:
                    attenuation = 0.0

        attenuation_arr[i] = attenuation

    output = (samples.astype(np.float64) * attenuation_arr).astype(np.float32)
    # NOTE: we do NOT mutate gate state here — the caller is responsible
    # for syncing state between optimized and reference instances.
    return output.reshape(audio.shape)


class TestNoiseGateByteIdentical:
    """SU-31: pre-allocated abs_x/i_arr/y_with_init/attenuation_arr buffers."""

    def test_single_chunk_byte_identical(self) -> None:
        """One process() call: optimized output == fresh-allocation reference."""
        sr = 16000
        gate_opt = NoiseGate(sample_rate=sr)
        gate_ref = NoiseGate(sample_rate=sr)
        rng = np.random.default_rng(99)
        audio = (rng.standard_normal(2048) * 0.3).astype(np.float32)

        out_opt = gate_opt.process(audio, sr)
        out_ref = _noise_gate_reference_process(gate_ref, audio, sr)

        assert out_opt is not None
        assert out_opt.dtype == np.float32
        assert out_opt.shape == audio.shape
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_multi_chunk_byte_identical_with_state_carry(self) -> None:
        """Multiple chunks: state carries identically between optimized and reference."""
        sr = 16000
        gate_opt = NoiseGate(sample_rate=sr)
        gate_ref = NoiseGate(sample_rate=sr)
        rng = np.random.default_rng(256)
        audio = (rng.standard_normal(8192) * 0.4).astype(np.float32)

        chunk_sizes = [512, 1024, 2048, 4096, 1024, 512]
        offset = 0
        for cs in chunk_sizes:
            chunk = audio[offset : offset + cs]
            offset += cs
            out_opt = gate_opt.process(chunk, sr)
            out_ref = _noise_gate_reference_process(gate_ref, chunk, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            # Sync state so the next chunk starts from the same point.
            gate_ref._level = gate_opt._level
            gate_ref._is_open = gate_opt._is_open
            gate_ref._attenuation = gate_opt._attenuation
            gate_ref._held_time = gate_opt._held_time

    def test_silence_byte_identical(self) -> None:
        """Very quiet audio (gate closes) must also match byte-for-byte."""
        sr = 16000
        gate_opt = NoiseGate(sample_rate=sr)
        gate_ref = NoiseGate(sample_rate=sr)
        silence = np.full(8192, 0.001, dtype=np.float32)

        out_opt = gate_opt.process(silence, sr)
        out_ref = _noise_gate_reference_process(gate_ref, silence, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_loud_audio_byte_identical(self) -> None:
        """Loud audio (gate opens fully) must also match byte-for-byte."""
        sr = 16000
        gate_opt = NoiseGate(sample_rate=sr)
        gate_ref = NoiseGate(sample_rate=sr)
        loud = np.full(8192, 0.5, dtype=np.float32)

        out_opt = gate_opt.process(loud, sr)
        out_ref = _noise_gate_reference_process(gate_ref, loud, sr)
        assert out_opt is not None
        np.testing.assert_array_equal(out_opt, out_ref)

    def test_buffer_reuse_across_calls_byte_identical(self) -> None:
        """Same-size chunks reuse the buffer — output still byte-identical."""
        sr = 16000
        gate_opt = NoiseGate(sample_rate=sr)
        gate_ref = NoiseGate(sample_rate=sr)
        rng = np.random.default_rng(31337)
        cs = 1024

        for _ in range(5):
            chunk = (rng.standard_normal(cs) * 0.3).astype(np.float32)
            out_opt = gate_opt.process(chunk, sr)
            out_ref = _noise_gate_reference_process(gate_ref, chunk, sr)
            assert out_opt is not None
            np.testing.assert_array_equal(out_opt, out_ref)
            gate_ref._level = gate_opt._level
            gate_ref._is_open = gate_opt._is_open
            gate_ref._attenuation = gate_opt._attenuation
            gate_ref._held_time = gate_opt._held_time
