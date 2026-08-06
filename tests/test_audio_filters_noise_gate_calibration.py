"""Tests for :meth:`NoiseGate._consume_calibration_chunk`.

When constructed with ``adaptive=True``, the noise gate samples the
first ``_ADAPTIVE_CALIBRATION_MS`` milliseconds of audio after each
``reset()`` / construction to estimate the ambient noise floor (RMS),
then derives:

- ``open_threshold  = noise_floor_db + 6 dB``  (gate opens 6 dB
  above the noise floor so speech passes but background hiss doesn't).
- ``close_threshold = noise_floor_db + 0 dB``  (gate closes at the
  noise floor itself).

During calibration the gate is OPEN (full pass-through) so the first
words aren't dropped. Once calibrated, the state machine uses the
derived thresholds (overriding the hardcoded ``-26 / -32 dBFS``
defaults).

The tests exercise the calibration logic directly by calling
:meth:`_consume_calibration_chunk` with crafted numpy arrays whose
RMS is known analytically. No real audio I/O is touched.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from voice_typer.server.audio_filters.base import db_to_mul, mul_to_db
from voice_typer.server.audio_filters.noise_gate import (
    _ADAPTIVE_CALIBRATION_MS,
    _ADAPTIVE_CLOSE_OFFSET_DB,
    _ADAPTIVE_MAX_THRESHOLD_DB,
    _ADAPTIVE_MIN_THRESHOLD_DB,
    _ADAPTIVE_OPEN_OFFSET_DB,
    NoiseGate,
)

_SR = 16000
# At 16 kHz the calibration target is ``int(16000 * 500 / 1000) = 8000``
# samples (500 ms of audio).
_CAL_TARGET = int(_SR * _ADAPTIVE_CALIBRATION_MS / 1000.0)


def _constant_rms_chunk(n: int, rms: float) -> np.ndarray:
    """Return an ``n``-sample float32 array with the requested RMS.

    A constant-amplitude signal ``[rms, -rms, rms, -rms, ...]`` has
    RMS = ``rms`` exactly (mean of squares == ``rms**2``), so the
    noise gate's calibration math
    (``sqrt(sumsq / count)`` == ``rms``) is analytically predictable.
    """
    arr = np.full(n, rms, dtype=np.float32)
    arr[1::2] = -rms
    return arr


# ── 1. calibration sets open_threshold = noise_floor + offset ───────


class TestCalibrationSetsOpenThreshold:
    """After ``_calibration_target`` samples of varying RMS, the gate
    must set ``_open_threshold`` to ``db_to_mul(noise_floor_db +
    _ADAPTIVE_OPEN_OFFSET_DB)``, clamped to
    ``[_ADAPTIVE_MIN_THRESHOLD_DB, _ADAPTIVE_MAX_THRESHOLD_DB]``."""

    def test_calibration_sets_open_threshold_to_noise_floor_plus_offset(
        self,
    ) -> None:
        """Feed N chunks of varying RMS (each chunk a different
        constant amplitude so the aggregate RMS is the RMS of the
        concatenated signal). Assert the derived open_threshold
        equals ``db_to_mul(noise_floor_db + 6)``, clamped."""
        # Build 4 chunks of 2000 samples each = 8000 total = target.
        # Varying RMS per chunk: 0.005, 0.010, 0.015, 0.020.
        # The aggregate RMS is sqrt(mean(all^2)) — with equal-length
        # chunks that's the quadratic mean of the per-chunk RMS
        # values.
        per_chunk_rms = [0.005, 0.010, 0.015, 0.020]
        chunk_size = _CAL_TARGET // len(per_chunk_rms)
        chunks = [_constant_rms_chunk(chunk_size, r) for r in per_chunk_rms]
        # Aggregate RMS: sqrt(mean(square of all samples)).
        all_samples = np.concatenate(chunks)
        expected_rms = float(np.sqrt(np.mean(all_samples**2)))
        expected_noise_floor_db = mul_to_db(expected_rms)
        expected_open_db = max(
            _ADAPTIVE_MIN_THRESHOLD_DB,
            min(_ADAPTIVE_MAX_THRESHOLD_DB, expected_noise_floor_db + _ADAPTIVE_OPEN_OFFSET_DB),
        )
        expected_close_db = max(
            _ADAPTIVE_MIN_THRESHOLD_DB,
            min(_ADAPTIVE_MAX_THRESHOLD_DB, expected_noise_floor_db + _ADAPTIVE_CLOSE_OFFSET_DB),
        )
        # The production code enforces open_db > close_db (else
        # open_db = close_db + 1). With offset 6 vs 0 they're always
        # 6 dB apart, so the clamp won't trigger the +1 fallback
        # here — but include it for parity with the production math.
        if expected_open_db <= expected_close_db:
            expected_open_db = expected_close_db + 1.0
        expected_open_mul = db_to_mul(expected_open_db)
        expected_close_mul = db_to_mul(expected_close_db)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        assert gate._calibrated is False, "gate must start uncalibrated"
        assert gate._calibration_target == _CAL_TARGET

        for chunk in chunks:
            gate._consume_calibration_chunk(chunk)

        assert gate._calibrated is True, "gate must be calibrated after target samples"
        assert gate._calibration_count == _CAL_TARGET
        assert gate._open_threshold == pytest.approx(expected_open_mul, rel=1e-5), (
            f"open_threshold: expected {expected_open_mul} "
            f"({expected_open_db:.3f} dB), got {gate._open_threshold}"
        )
        assert gate._close_threshold == pytest.approx(expected_close_mul, rel=1e-5), (
            f"close_threshold: expected {expected_close_mul} "
            f"({expected_close_db:.3f} dB), got {gate._close_threshold}"
        )

    def test_calibration_uses_quadratic_mean_of_concatenated_rms(
        self,
    ) -> None:
        """Two chunks of equal length with RMS values 0.01 and 0.03
        must yield a noise floor of ``mul_to_db(sqrt((0.01**2 +
        0.03**2) / 2))`` (quadratic mean), NOT the arithmetic mean
        ``mul_to_db((0.01 + 0.03) / 2)``.

        This pins that the calibration accumulates ``sumsq`` (not
        per-chunk RMS), so a single loud chunk in a quiet window
        dominates the floor (matches the production intent — a loud
        transient during calibration raises the floor, preventing
        the gate from opening on noise)."""
        chunk_size = _CAL_TARGET // 2
        c1 = _constant_rms_chunk(chunk_size, 0.01)
        c2 = _constant_rms_chunk(chunk_size, 0.03)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(c1)
        gate._consume_calibration_chunk(c2)

        # Quadratic mean of the two RMS values (equal-length chunks).
        quad_mean = math.sqrt((0.01**2 + 0.03**2) / 2.0)
        expected_noise_floor_db = mul_to_db(quad_mean)
        expected_open_db = expected_noise_floor_db + _ADAPTIVE_OPEN_OFFSET_DB

        assert gate._calibrated is True
        assert gate._open_threshold == pytest.approx(
            db_to_mul(expected_open_db), rel=1e-5
        )

    def test_calibration_clamps_to_max_when_noise_floor_near_zero_db(
        self,
    ) -> None:
        """If the calibration audio is at full-scale (0 dBFS), the
        derived open_threshold would be +6 dB — clamp to 0 dB
        (``_ADAPTIVE_MAX_THRESHOLD_DB``)."""
        # Full-scale constant amplitude (RMS = 1.0 == 0 dBFS).
        chunk = _constant_rms_chunk(_CAL_TARGET, 1.0)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(chunk)

        # noise_floor_db = 0; open_db = 0 + 6 = 6, clamped to 0.
        assert gate._calibrated is True
        # close_db = 0 + 0 = 0, clamped to 0.
        # open_db (clamped to 0) <= close_db (0) → open_db = close_db + 1 = 1.
        # So _open_threshold = db_to_mul(1.0) ≈ 1.122.
        expected_open_db = 1.0  # close_db (0) + 1.0 (fallback)
        assert gate._open_threshold == pytest.approx(
            db_to_mul(expected_open_db), rel=1e-5
        )

    def test_calibration_clamps_to_min_when_noise_floor_very_low(
        self,
    ) -> None:
        """If the calibration audio is extremely quiet (noise floor
        below -96 dBFS), the derived open_threshold would be below
        -90 dB — clamp to -90 dB (``_ADAPTIVE_MIN_THRESHOLD_DB``)."""
        # 1e-6 amplitude == -120 dBFS, well below the -90 min.
        chunk = _constant_rms_chunk(_CAL_TARGET, 1e-6)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(chunk)

        # noise_floor_db ≈ -120; open_db = -120 + 6 = -114, clamped to -90.
        # close_db = -120 + 0 = -120, clamped to -90.
        # open_db (-90) <= close_db (-90) → open_db = -90 + 1 = -89.
        assert gate._calibrated is True
        assert gate._open_threshold == pytest.approx(
            db_to_mul(-89.0), rel=1e-5
        )
        assert gate._close_threshold == pytest.approx(
            db_to_mul(-90.0), rel=1e-5
        )


# ── 2. silent chunks fall back to initial open threshold ────────────


class TestSilentChunksFallbackToInitial:
    """When the calibration audio is entirely silent (sumsq == 0),
    ``mul_to_db(0)`` would be -inf — the gate must fall back to
    using ``_initial_open_threshold`` as the noise floor (in dB),
    so the derived thresholds are sensible defaults rather than
    ``-inf + 6``."""

    def test_silent_chunks_fallback_to_initial_open_threshold(self) -> None:
        """Feed ``_calibration_target`` silent samples. The fallback
        path uses ``mul_to_db(self._initial_open_threshold)`` as the
        noise floor, so:

        - ``open_db  = initial_open_db + 6``
        - ``close_db = initial_open_db + 0``

        With the default ``open_threshold_db = -26``, this yields
        ``open_db = -20`` and ``close_db = -26``.
        """
        silent_chunk = np.zeros(_CAL_TARGET, dtype=np.float32)

        gate = NoiseGate(
            adaptive=True,
            open_threshold_db=-26.0,
            close_threshold_db=-32.0,
            sample_rate=_SR,
        )
        # Sanity: capture the initial multiplier for the assertion.
        initial_open_db = mul_to_db(gate._initial_open_threshold)
        expected_open_db = initial_open_db + _ADAPTIVE_OPEN_OFFSET_DB
        expected_close_db = initial_open_db + _ADAPTIVE_CLOSE_OFFSET_DB

        gate._consume_calibration_chunk(silent_chunk)

        assert gate._calibrated is True
        assert gate._calibration_sumsq == 0.0, "silent chunks must keep sumsq at 0"
        # The fallback uses the initial-open-threshold's dB as the
        # noise floor — NOT -inf from mul_to_db(0).
        assert gate._open_threshold == pytest.approx(
            db_to_mul(expected_open_db), rel=1e-5
        )
        assert gate._close_threshold == pytest.approx(
            db_to_mul(expected_close_db), rel=1e-5
        )

    def test_silent_chunks_do_not_produce_inf_threshold(self) -> None:
        """Regression: a literal ``mul_to_db(0)`` returns -inf, and
        ``db_to_mul(-inf + 6)`` returns 0.0 — a gate with
        ``_open_threshold == 0`` would NEVER open, silencing all
        audio. The fallback path must avoid this."""
        silent_chunk = np.zeros(_CAL_TARGET, dtype=np.float32)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(silent_chunk)

        # open_threshold must be a positive, finite number — not 0
        # and not NaN/inf.
        assert math.isfinite(gate._open_threshold)
        assert gate._open_threshold > 0.0, (
            "open_threshold must be > 0 (a 0 threshold would silence "
            "all audio)."
        )


# ── 3. calibration completes once and is idempotent ─────────────────


class TestCalibrationCompletesOnceAndIsIdempotent:
    """Once ``_calibration_count >= _calibration_target``, subsequent
    calls to ``_consume_calibration_chunk`` must early-return without
    re-running the threshold-derivation math. The calibration is a
    one-shot — re-feeding audio after calibration must NOT change the
    derived thresholds or the calibration counter."""

    def test_calibration_completes_once_and_is_idempotent(self) -> None:
        """Feed > ``_calibration_target`` samples (in two batches).
        After the first batch completes calibration, the second batch
        must NOT re-run the derivation: counter stays at target,
        ``_calibrated`` stays True, thresholds unchanged."""
        # First batch — exactly enough to complete calibration.
        first_batch = _constant_rms_chunk(_CAL_TARGET, 0.01)
        # Second batch — would re-derive with a different RMS if the
        # idempotence guard were broken.
        second_batch = _constant_rms_chunk(_CAL_TARGET, 0.05)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(first_batch)

        # Sanity: calibration completed on the first batch.
        assert gate._calibrated is True
        assert gate._calibration_count == _CAL_TARGET
        first_open_threshold = gate._open_threshold
        first_close_threshold = gate._close_threshold
        first_sumsq = gate._calibration_sumsq

        # Feed a second batch — the early-return guard
        # (``remaining = target - count <= 0`` → return) must fire.
        gate._consume_calibration_chunk(second_batch)

        # Counter unchanged (didn't grow beyond target).
        assert gate._calibration_count == _CAL_TARGET, (
            "calibration_count must NOT grow past _calibration_target — "
            "the idempotence guard must short-circuit subsequent chunks."
        )
        # sumsq unchanged (didn't accumulate the second batch).
        assert gate._calibration_sumsq == first_sumsq, (
            "calibration_sumsq must NOT accumulate past calibration — "
            "the idempotence guard must short-circuit subsequent chunks."
        )
        # Thresholds unchanged (didn't re-derive with the 0.05 RMS).
        assert gate._open_threshold == first_open_threshold
        assert gate._close_threshold == first_close_threshold
        # Still calibrated.
        assert gate._calibrated is True

    def test_calibration_does_not_re_run_after_process_path(self) -> None:
        """When called via the public ``process()`` path (which calls
        ``_consume_calibration_chunk`` internally while
        ``_calibrated is False``), calibration runs ONCE and then
        subsequent ``process()`` calls take the calibrated state
        machine branch (not the calibration branch)."""
        gate = NoiseGate(adaptive=True, sample_rate=_SR)

        # First call: enough samples to complete calibration.
        first_chunk = _constant_rms_chunk(_CAL_TARGET, 0.01)
        out1 = gate.process(first_chunk, _SR)
        # Calibration branch returns the input unchanged (gate is
        # OPEN during calibration).
        assert out1 is not None
        assert gate._calibrated is True
        threshold_after_first = gate._open_threshold
        count_after_first = gate._calibration_count

        # Second call: takes the calibrated state-machine branch —
        # ``_consume_calibration_chunk`` is NOT called by process()
        # when ``_calibrated is True``.
        second_chunk = _constant_rms_chunk(1024, 0.05)
        out2 = gate.process(second_chunk, _SR)

        assert out2 is not None
        # Counter + threshold unchanged — calibration did NOT re-run.
        assert gate._calibration_count == count_after_first
        assert gate._open_threshold == threshold_after_first

    def test_partial_calibration_does_not_set_calibrated_flag(self) -> None:
        """Feeding FEWER than ``_calibration_target`` samples must
        leave ``_calibrated`` False — the derivation only runs once
        the target is met (avoids setting thresholds on a
        statistically-insufficient sample)."""
        gate = NoiseGate(adaptive=True, sample_rate=_SR)

        # Feed half the target.
        half_chunk = _constant_rms_chunk(_CAL_TARGET // 2, 0.01)
        gate._consume_calibration_chunk(half_chunk)

        assert gate._calibrated is False
        assert gate._calibration_count == _CAL_TARGET // 2
        # Thresholds still at their initial values (not yet derived).
        assert gate._open_threshold == gate._initial_open_threshold
        assert gate._close_threshold == gate._initial_close_threshold

    def test_zero_length_chunk_does_not_advance_calibration(self) -> None:
        """An empty chunk (``len(samples) == 0``) must NOT advance
        the calibration counter — the ``take <= 0`` early-return
        guards against a degenerate empty-chunk call corrupting the
        accumulation."""
        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(np.array([], dtype=np.float32))

        assert gate._calibrated is False
        assert gate._calibration_count == 0
        assert gate._calibration_sumsq == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov", "--timeout=30"])
