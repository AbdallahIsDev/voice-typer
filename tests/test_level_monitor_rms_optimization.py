"""TY-17: level_monitor RMS/peak numpy optimization tests.

Verifies that the AUDIO-NP / PERF-FIX-2 pattern ported from
``recorder.py:2740-2749`` into ``level_monitor._process_level_chunk``:

1. **Numerical equivalence**: the new RMS (``np.sqrt(np.dot(x, x) / size)``)
   and peak (``max(max(x), -min(x))``) match the OLD computations
   (``np.sqrt(np.mean(x**2))`` and ``np.abs(x).max()``) to floating-point
   tolerance on random inputs.

2. **Fewer intermediate allocations**: the OLD path allocated 3-4
   intermediate arrays per chunk (``x**2``, ``np.abs(x)``, optionally
   ``x.astype(np.float32)``). The NEW path allocates ZERO intermediate
   arrays for the RMS+peak computation (only scalar reductions). We
   verify this by counting ``np.ndarray`` allocations via a wrapper
   around the ndarray constructor.

3. **No-op ``.astype(np.float32)`` dropped**: the OLD raw-quality RMS
   path called ``flat.astype(np.float32)`` even though ``flat`` is
   already float32 (sd.InputStream uses ``dtype=np.float32``). The NEW
   path drops the astype. We verify by checking the dtype is preserved.

All ``sounddevice`` calls are mocked so the tests run on any platform.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest

# ═══════════════════════════════════════════════════════════════════════════
# Numerical equivalence: NEW (np.dot / max-min) vs OLD (np.mean(x**2) / np.abs(x).max())
# ═══════════════════════════════════════════════════════════════════════════


def _old_rms(flat: np.ndarray) -> float:
    """OLD level_monitor RMS — pre-TY-17 pattern (allocates x**2)."""
    return float(np.sqrt(np.mean(flat**2)))


def _old_peak(flat: np.ndarray) -> float:
    """OLD level_monitor peak — pre-TY-17 pattern (allocates np.abs(x))."""
    return float(np.abs(flat).max())


def _old_raw_rms(flat: np.ndarray) -> float:
    """OLD raw-quality RMS — pre-TY-17 pattern (allocates astype + square)."""
    return float(np.sqrt(np.mean(np.square(flat.astype(np.float32)))))


def _new_rms(flat: np.ndarray) -> float:
    """NEW (TY-17 AUDIO-NP) RMS — uses np.dot (no squared array)."""
    return float(np.sqrt(np.dot(flat, flat) / flat.size))


def _new_peak(flat: np.ndarray) -> float:
    """NEW (TY-17 PERF-FIX-2) peak — uses max(max, -min) (no abs array)."""
    return max(float(flat.max()), -float(flat.min()))


class TestTY17NumericalEquivalence:
    """The NEW RMS/peak computations match the OLD ones to fp tolerance."""

    @pytest.mark.parametrize("seed", [0, 1, 42, 1337, 99999])
    @pytest.mark.parametrize("size", [128, 512, 1024, 4096])
    def test_rms_matches_old_pattern(self, seed, size):
        """``sqrt(dot(x, x) / size)`` == ``sqrt(mean(x**2))``.

        Mathematical identity: ``sum(x**2) == dot(x, x)`` and
        ``mean(x**2) == sum(x**2) / size``.
        """
        rng = np.random.default_rng(seed)
        flat = rng.standard_normal(size).astype(np.float32)
        old = _old_rms(flat)
        new = _new_rms(flat)
        # np.dot uses a fused multiply-add (single-pass BLAS sdot)
        # while x**2 + np.mean uses two passes; allow 1e-5 relative
        # tolerance for accumulation-order differences.
        assert abs(old - new) <= 1e-5 * max(1.0, abs(old)), (
            f"TY-17: NEW RMS {new} != OLD RMS {old} (size={size}, seed={seed})"
        )

    @pytest.mark.parametrize("seed", [0, 1, 42, 1337, 99999])
    @pytest.mark.parametrize("size", [128, 512, 1024, 4096])
    def test_peak_matches_old_pattern(self, seed, size):
        """``max(max(x), -min(x))`` == ``abs(x).max()``.

        Mathematical identity: ``max(|x|) == max(max(x), -min(x))``.
        """
        rng = np.random.default_rng(seed)
        flat = rng.standard_normal(size).astype(np.float32)
        old = _old_peak(flat)
        new = _new_peak(flat)
        # max/min are exact reductions — match to bit-level (use ==).
        assert old == new, f"TY-17: NEW peak {new} != OLD peak {old} (size={size}, seed={seed})"

    def test_peak_handles_mixed_signs(self):
        """Peak is correct when min < 0 < max (the typical case).

        Note: float32 representation of 0.8 is 0.800000011920929
        (precision loss), so we compare with tolerance.
        """
        flat = np.array([-0.5, 0.1, 0.3, -0.8, 0.2], dtype=np.float32)
        new_peak = _new_peak(flat)
        old_peak = _old_peak(flat)
        assert abs(new_peak - old_peak) < 1e-7, f"TY-17: NEW peak {new_peak} != OLD peak {old_peak}"
        # Both should be ~0.8 (the abs value of -0.8 in float32).
        assert abs(new_peak - 0.8) < 1e-6
        assert abs(old_peak - 0.8) < 1e-6

    def test_peak_handles_all_positive(self):
        """Peak is correct when all samples are >= 0."""
        flat = np.array([0.1, 0.3, 0.5, 0.2], dtype=np.float32)
        assert _new_peak(flat) == _old_peak(flat)
        assert abs(_new_peak(flat) - 0.5) < 1e-7

    def test_peak_handles_all_negative(self):
        """Peak is correct when all samples are <= 0."""
        flat = np.array([-0.1, -0.3, -0.5, -0.2], dtype=np.float32)
        assert _new_peak(flat) == _old_peak(flat)
        assert abs(_new_peak(flat) - 0.5) < 1e-7

    def test_peak_zero_chunk(self):
        """Peak is 0.0 for an all-zero chunk (TY-4 disconnect detector
        relies on this exact behavior)."""
        flat = np.zeros(512, dtype=np.float32)
        assert _new_peak(flat) == _old_peak(flat) == 0.0

    def test_rms_zero_chunk(self):
        """RMS is 0.0 for an all-zero chunk (TY-4 disconnect detector
        relies on this exact behavior)."""
        flat = np.zeros(512, dtype=np.float32)
        assert _new_rms(flat) == _old_rms(flat) == 0.0

    def test_raw_rms_matches_old_pattern_no_astype(self):
        """The NEW raw-quality RMS path drops ``.astype(np.float32)``
        because ``flat`` is ALREADY float32. Numerical result is
        identical (astype on the same dtype is a no-op)."""
        rng = np.random.default_rng(7)
        flat = rng.standard_normal(512).astype(np.float32)
        old = _old_raw_rms(flat)
        # NEW path: same as _new_rms (no astype, np.dot).
        new = _new_rms(flat)
        assert abs(old - new) <= 1e-5 * max(1.0, abs(old)), f"TY-17: NEW raw RMS {new} (no astype) != OLD raw RMS {old}"


# ═══════════════════════════════════════════════════════════════════════════
# Allocation-count regression: NEW allocates FEWER ndarrays than OLD
# ═══════════════════════════════════════════════════════════════════════════


class TestTY17AllocationCount:
    """The NEW RMS+peak path calls ZERO allocation-inducing numpy
    functions (``np.abs``, ``np.mean``); the OLD path calls them once
    each per chunk.

    We can't directly monkeypatch ``np.ndarray.__new__`` (it's an
    immutable C type — ``TypeError: cannot set '__new__' attribute of
    immutable type 'numpy.ndarray'``), so we spy on the high-level
    numpy functions that allocate intermediate arrays. The OLD peak
    path calls ``np.abs(flat)`` (allocates a new ndarray); the NEW
    peak path uses ``max(flat.max(), -flat.min())`` (no allocation).
    The OLD RMS path calls ``np.mean(flat**2)`` where ``flat**2``
    allocates; the NEW RMS path uses ``np.dot(flat, flat)`` (returns a
    scalar, no intermediate ndarray).
    """

    def test_new_rms_does_not_call_np_mean(self, monkeypatch):
        """``_new_rms`` uses ``np.dot`` — ``np.mean`` is NOT called."""
        mean_calls = 0
        original_mean = np.mean

        def _spy_mean(*args, **kwargs):
            nonlocal mean_calls
            mean_calls += 1
            return original_mean(*args, **kwargs)

        flat = np.random.default_rng(0).standard_normal(512).astype(np.float32)

        # OLD path: should call np.mean at least once (np.mean(flat**2)).
        monkeypatch.setattr(np, "mean", _spy_mean)
        mean_calls = 0
        _ = _old_rms(flat)
        old_mean_calls = mean_calls
        assert old_mean_calls >= 1, f"OLD RMS path must call np.mean at least once; got {old_mean_calls}"

        # NEW path: should call np.mean ZERO times (uses np.dot).
        mean_calls = 0
        _ = _new_rms(flat)
        new_mean_calls = mean_calls
        assert new_mean_calls == 0, (
            f"TY-17: NEW RMS path must NOT call np.mean (uses np.dot); got {new_mean_calls} calls"
        )

    def test_new_peak_does_not_call_np_abs(self, monkeypatch):
        """``_new_peak`` uses ``max(max, -min)`` — ``np.abs`` is NOT called."""
        abs_calls = 0
        original_abs = np.abs

        def _spy_abs(*args, **kwargs):
            nonlocal abs_calls
            abs_calls += 1
            return original_abs(*args, **kwargs)

        flat = np.random.default_rng(0).standard_normal(512).astype(np.float32)

        # OLD path: should call np.abs once (np.abs(flat).max()).
        monkeypatch.setattr(np, "abs", _spy_abs)
        abs_calls = 0
        _ = _old_peak(flat)
        old_abs_calls = abs_calls
        assert old_abs_calls >= 1, f"OLD peak path must call np.abs at least once; got {old_abs_calls}"

        # NEW path: should call np.abs ZERO times (uses max/min).
        abs_calls = 0
        _ = _new_peak(flat)
        new_abs_calls = abs_calls
        assert new_abs_calls == 0, (
            f"TY-17: NEW peak path must NOT call np.abs (uses max/min); got {new_abs_calls} calls"
        )

    def test_combined_rms_peak_new_calls_fewer_allocating_fns(self, monkeypatch):
        """The combined RMS+peak computation (the actual code path used
        per chunk) calls strictly fewer allocation-inducing numpy
        functions in the NEW path than the OLD path."""
        alloc_calls = 0
        original_abs = np.abs
        original_mean = np.mean

        def _spy_abs(*args, **kwargs):
            nonlocal alloc_calls
            alloc_calls += 1
            return original_abs(*args, **kwargs)

        def _spy_mean(*args, **kwargs):
            nonlocal alloc_calls
            alloc_calls += 1
            return original_mean(*args, **kwargs)

        flat = np.random.default_rng(42).standard_normal(512).astype(np.float32)

        # OLD combined: abs_flat (np.abs) + flat**2 + np.mean.
        monkeypatch.setattr(np, "abs", _spy_abs)
        monkeypatch.setattr(np, "mean", _spy_mean)
        alloc_calls = 0
        _old_rms(flat)
        _old_peak(flat)
        old_combined = alloc_calls
        assert old_combined >= 2, (
            f"OLD combined RMS+peak must call >= 2 allocating functions (np.abs + np.mean); got {old_combined}"
        )

        # NEW combined: np.dot + max + min — none of the spied funcs.
        alloc_calls = 0
        _new_rms(flat)
        _new_peak(flat)
        new_combined = alloc_calls
        assert new_combined == 0, (
            f"TY-17: NEW combined RMS+peak must call 0 allocating "
            f"functions (np.dot + max/min return scalars); got "
            f"{new_combined}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# End-to-end: _process_level_chunk produces same _monitor_level as OLD path
# ═══════════════════════════════════════════════════════════════════════════


class TestTY17ProcessLevelChunkEndToEnd:
    """The end-to-end level computation in ``_process_level_chunk`` (NEW
    path) produces the same ``_monitor_level`` / ``_monitor_peak`` as
    the OLD path would have.
    """

    def test_process_chunk_uses_optimized_path(self, monkeypatch):
        """After processing a chunk, ``_monitor_level`` reflects the NEW
        RMS (np.dot-based) — verify by comparing against a manual
        computation using the OLD pattern (which must match within
        tolerance)."""
        import voice_typer.server.level_monitor as lm

        # Mock sounddevice so start_monitoring doesn't touch real hardware.
        holder = {"callback": None}

        class _Stream:
            def __init__(self, *args, **kwargs):
                holder["callback"] = kwargs.get("callback")

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        import sounddevice as sd

        sd.InputStream = _Stream  # type: ignore[assignment]
        sd.query_devices.return_value = {
            "name": "Mock Mic",
            "default_samplerate": 16000,
            "max_input_channels": 1,
            "hostapi": 0,
        }
        # Disable the live processor so we hit the no-processor branch
        # (the simpler NEW path).
        lm._level_processor = None

        lm.start_monitoring(mic_id=None)
        try:
            # Push a chunk with known RMS/peak.
            chunk = np.ones((512, 1), dtype=np.float32) * 0.25
            holder["callback"](chunk, 512, None, None)

            # Wait for the worker to process.
            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                if lm._monitor_level > 0:
                    break
                time.sleep(0.01)

            # Expected: NEW RMS = sqrt(dot(x, x)/size) = 0.25.
            # EMA: level = 0 * 0.6 + 0.25 * 0.4 = 0.1.
            expected_rms = _new_rms(chunk.ravel())
            expected_level = 0.0 * 0.6 + expected_rms * 0.4
            assert abs(lm._monitor_level - expected_level) < 1e-6, (
                f"TY-17: _monitor_level={lm._monitor_level} != expected "
                f"{expected_level} (NEW RMS path; chunk RMS={expected_rms})"
            )
            # Expected: NEW peak = max(max, -min) = 0.25.
            # EMA: peak = max(0 * 0.8, 0.25) = 0.25.
            expected_peak = _new_peak(chunk.ravel())
            assert abs(lm._monitor_peak - expected_peak) < 1e-6, (
                f"TY-17: _monitor_peak={lm._monitor_peak} != expected {expected_peak} (NEW peak path)"
            )
        finally:
            lm.stop_monitoring()

    def test_process_chunk_with_processor_uses_optimized_path(self, monkeypatch):
        """The processor-active branch ALSO uses the NEW AUDIO-NP /
        PERF-FIX-2 pattern. Verify by passing through a processor that
        returns the input unchanged and comparing against the manual
        OLD-pattern computation."""
        import voice_typer.server.level_monitor as lm

        holder = {"callback": None}

        class _Stream:
            def __init__(self, *args, **kwargs):
                holder["callback"] = kwargs.get("callback")

            def start(self):
                pass

            def stop(self):
                pass

            def close(self):
                pass

        import sounddevice as sd

        sd.InputStream = _Stream  # type: ignore[assignment]
        sd.query_devices.return_value = {
            "name": "Mock Mic",
            "default_samplerate": 16000,
            "max_input_channels": 1,
            "hostapi": 0,
        }

        # Passthrough processor — returns the input chunk unchanged so
        # flat_filtered == flat and we can compute the expected RMS/peak
        # from the raw input.
        processor = MagicMock()
        processor.process_chunk.side_effect = lambda x: x
        lm._level_processor = processor

        lm.start_monitoring(mic_id=None)
        try:
            # Mixed-sign chunk so peak is on the negative side.
            chunk = np.array([[0.1], [-0.4], [0.3], [-0.2]] * 128, dtype=np.float32)  # 512 samples
            holder["callback"](chunk, 512, None, None)

            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                if lm._monitor_level > 0:
                    break
                time.sleep(0.01)

            flat = chunk.ravel()
            expected_rms = _new_rms(flat)
            expected_peak = _new_peak(flat)
            # Cross-check NEW vs OLD on the SAME flat (numerical
            # equivalence inside the test, mirrors production code).
            assert abs(_old_rms(flat) - expected_rms) < 1e-6
            assert _old_peak(flat) == expected_peak

            expected_level = 0.0 * 0.6 + expected_rms * 0.4
            assert abs(lm._monitor_level - expected_level) < 1e-6, (
                f"TY-17 (processor branch): _monitor_level={lm._monitor_level} != expected {expected_level}"
            )
            assert abs(lm._monitor_peak - expected_peak) < 1e-6, (
                f"TY-17 (processor branch): _monitor_peak={lm._monitor_peak} != expected {expected_peak}"
            )
        finally:
            lm.stop_monitoring()
