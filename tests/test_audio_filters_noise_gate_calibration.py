"""Tests for :meth:`NoiseGate._consume_calibration_chunk`."""

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
_CAL_TARGET = int(_SR * _ADAPTIVE_CALIBRATION_MS / 1000.0)


def _constant_rms_chunk(n: int, rms: float) -> np.ndarray:
    """Return an ``n``-sample float32 array with the requested RMS."""
    arr = np.full(n, rms, dtype=np.float32)
    arr[1::2] = -rms
    return arr


class TestCalibrationSetsOpenThreshold:
    """After ``_calibration_target`` samples of varying RMS, the gate"""

    def test_calibration_sets_open_threshold_to_noise_floor_plus_offset(
        self,
    ) -> None:
        """Feed N chunks of varying RMS (each chunk a different"""
        # Build 4 chunks of 2000 samples each = 8000 total = target.
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
            f"open_threshold: expected {expected_open_mul} ({expected_open_db:.3f} dB), got {gate._open_threshold}"
        )
        assert gate._close_threshold == pytest.approx(expected_close_mul, rel=1e-5), (
            f"close_threshold: expected {expected_close_mul} ({expected_close_db:.3f} dB), got {gate._close_threshold}"
        )

    def test_calibration_uses_quadratic_mean_of_concatenated_rms(
        self,
    ) -> None:
        """Two chunks of equal length with RMS values 0.01 and 0.03"""
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
        assert gate._open_threshold == pytest.approx(db_to_mul(expected_open_db), rel=1e-5)

    def test_calibration_clamps_to_max_when_noise_floor_near_zero_db(
        self,
    ) -> None:
        """derived open_threshold would be +6 dB, clamp to 0 dB"""
        # Full-scale constant amplitude (RMS = 1.0 == 0 dBFS).
        chunk = _constant_rms_chunk(_CAL_TARGET, 1.0)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(chunk)

        assert gate._calibrated is True
        expected_open_db = 1.0  # close_db (0) + 1.0 (fallback)
        assert gate._open_threshold == pytest.approx(db_to_mul(expected_open_db), rel=1e-5)

    def test_calibration_clamps_to_min_when_noise_floor_very_low(
        self,
    ) -> None:
        """If the calibration audio is extremely quiet (noise floor"""
        # 1e-6 amplitude == -120 dBFS, well below the -90 min.
        chunk = _constant_rms_chunk(_CAL_TARGET, 1e-6)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(chunk)

        assert gate._calibrated is True
        assert gate._open_threshold == pytest.approx(db_to_mul(-89.0), rel=1e-5)
        assert gate._close_threshold == pytest.approx(db_to_mul(-90.0), rel=1e-5)


class TestSilentChunksFallbackToInitial:
    """When the calibration audio is entirely silent (sumsq == 0),"""

    def test_silent_chunks_fallback_to_initial_open_threshold(self) -> None:
        """Feed ``_calibration_target`` silent samples. The fallback"""
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
        assert gate._open_threshold == pytest.approx(db_to_mul(expected_open_db), rel=1e-5)
        assert gate._close_threshold == pytest.approx(db_to_mul(expected_close_db), rel=1e-5)

    def test_silent_chunks_do_not_produce_inf_threshold(self) -> None:
        """``_open_threshold == 0`` would NEVER open, silencing all"""
        silent_chunk = np.zeros(_CAL_TARGET, dtype=np.float32)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(silent_chunk)

        assert math.isfinite(gate._open_threshold)
        assert gate._open_threshold > 0.0, "open_threshold must be > 0 (a 0 threshold would silence all audio)."


class TestCalibrationCompletesOnceAndIsIdempotent:
    """Once ``_calibration_count >= _calibration_target``, subsequent"""

    def test_calibration_completes_once_and_is_idempotent(self) -> None:
        """Feed > ``_calibration_target`` samples (in two batches)."""
        # First batch, exactly enough to complete calibration.
        first_batch = _constant_rms_chunk(_CAL_TARGET, 0.01)
        second_batch = _constant_rms_chunk(_CAL_TARGET, 0.05)

        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(first_batch)

        # Sanity: calibration completed on the first batch.
        assert gate._calibrated is True
        assert gate._calibration_count == _CAL_TARGET
        first_open_threshold = gate._open_threshold
        first_close_threshold = gate._close_threshold
        first_sumsq = gate._calibration_sumsq

        # Feed a second batch, the early-return guard
        gate._consume_calibration_chunk(second_batch)

        # Counter unchanged (didn't grow beyond target).
        assert gate._calibration_count == _CAL_TARGET, (
            "calibration_count must NOT grow past _calibration_target, "
            "the idempotence guard must short-circuit subsequent chunks."
        )
        assert gate._calibration_sumsq == first_sumsq, (
            "calibration_sumsq must NOT accumulate past calibration, "
            "the idempotence guard must short-circuit subsequent chunks."
        )
        # Thresholds unchanged (didn't re-derive with the 0.05 RMS).
        assert gate._open_threshold == first_open_threshold
        assert gate._close_threshold == first_close_threshold
        # Still calibrated.
        assert gate._calibrated is True

    def test_calibration_does_not_re_run_after_process_path(self) -> None:
        """When called via the public ``process()`` path (which calls"""
        gate = NoiseGate(adaptive=True, sample_rate=_SR)

        # First call: enough samples to complete calibration.
        first_chunk = _constant_rms_chunk(_CAL_TARGET, 0.01)
        out1 = gate.process(first_chunk, _SR)
        # Calibration branch returns the input unchanged (gate is
        assert out1 is not None
        assert gate._calibrated is True
        threshold_after_first = gate._open_threshold
        count_after_first = gate._calibration_count

        # Second call: takes the calibrated state-machine branch —
        second_chunk = _constant_rms_chunk(1024, 0.05)
        out2 = gate.process(second_chunk, _SR)

        assert out2 is not None
        # Counter + threshold unchanged, calibration did NOT re-run.
        assert gate._calibration_count == count_after_first
        assert gate._open_threshold == threshold_after_first

    def test_partial_calibration_does_not_set_calibrated_flag(self) -> None:
        """Feeding FEWER than ``_calibration_target`` samples must"""
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
        """An empty chunk (``len(samples) == 0``) must NOT advance"""
        gate = NoiseGate(adaptive=True, sample_rate=_SR)
        gate._consume_calibration_chunk(np.array([], dtype=np.float32))

        assert gate._calibrated is False
        assert gate._calibration_count == 0
        assert gate._calibration_sumsq == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov", "--timeout=30"])
