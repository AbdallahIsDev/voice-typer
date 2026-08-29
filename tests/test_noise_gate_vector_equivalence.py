"""Bitwise equivalence pin: vectorized vs scalar NoiseGate state machine.

The noise gate's attack/hold/release state machine used to be a per-sample
Python loop. It is now vectorized (last-event scan via
``np.maximum.accumulate`` + per-run cumulative fills), with the original
loop kept as the scalar fallback for pathological chunks. Because the gate
runs on the real-time audio path, the vectorized implementation must be
NUMERICALLY IDENTICAL to the loop for the same inputs — not approximately.

Equivalence argument (why bitwise equality is achievable at all):

* The per-sample gate state (``is_open``) is a pure "last effective event
  wins" scan: an open event (``level > open_thr``) always sets the state
  open; a close event (``level < close_thr``, only effective while open)
  sets it closed. Therefore ``state_open[i]`` is fully determined by the
  last event at or before ``i`` — computable with two
  ``np.maximum.accumulate`` passes. At a sample where both comparisons
  fire (only possible when ``open_thr < close_thr``), the loop's
  ``if/elif`` gives the open event precedence, so ties resolve open.
* Within a maximal open run the recurrence is
  ``att = min(att + attack_rate*dt, 1.0)`` — a monotone increasing
  sequence, so the per-step clamp at 1.0 is exactly an element-wise
  ``np.minimum`` over the cumulative sum (the clamp can only bind at the
  top, and once bound every later raw value is also >= 1.0). The same
  argument applies to the release ramp with ``np.maximum(., 0.0)``.
* The hold timer accumulates ``dt`` by REPEATED float addition in the
  loop. ``np.cumsum`` over a buffer seeded with the carried value
  performs the identical left-to-right float additions, so the held-time
  values — and therefore the ``held_time > hold_time`` comparisons, even
  at exact-equality boundaries — are bit-identical.

The reference loop below is copied verbatim from the pre-vectorization
implementation. If this test ever fails, the vectorized path has drifted
from the loop: fix the implementation, never weaken this pin.

Run-layout note: the tests exercise both state machines directly (same
``level_arr`` in, bitwise-equal ``attenuation`` arrays and final state
out) and the full ``process()`` pipeline end-to-end across chunk
boundaries (carried ``_level`` / ``_attenuation`` / ``_held_time`` /
``_is_open`` state), on randomized plus adversarial edge inputs.
"""

from __future__ import annotations

import numpy as np
import pytest
from voice_typer.server.audio_filters.base import db_to_mul
from voice_typer.server.audio_filters.noise_gate import NoiseGate

# ═════════════════════════════════════════════════════════════════════════
# Reference implementation — the ORIGINAL per-sample loop, verbatim.
# ═════════════════════════════════════════════════════════════════════════


def reference_state_machine(
    level_arr: np.ndarray,
    n: int,
    dt: float,
    attack_rate: float,
    release_rate: float,
    hold_time: float,
    attenuation_arr: np.ndarray,
    *,
    open_thr: float,
    close_thr: float,
    is_open: bool,
    attenuation: float,
    held_time: float,
) -> tuple[bool, float, float]:
    """The pre-vectorization per-sample loop, unmodified.

    Returns ``(is_open, attenuation, held_time)`` after the chunk and
    fills ``attenuation_arr[:n]`` in place.
    """
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

    return is_open, attenuation, held_time


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════


def _make_gate(
    open_db: float = -26.0,
    close_db: float = -32.0,
    attack_ms: float = 25.0,
    hold_ms: float = 200.0,
    release_ms: float = 150.0,
    sample_rate: int = 16000,
) -> NoiseGate:
    return NoiseGate(
        open_threshold_db=open_db,
        close_threshold_db=close_db,
        attack_ms=attack_ms,
        hold_ms=hold_ms,
        release_ms=release_ms,
        sample_rate=sample_rate,
    )


def _run_vector(gate: NoiseGate, level_arr: np.ndarray, n: int) -> tuple[np.ndarray, bool, float, float]:
    """Run the gate's vectorized state machine on ``level_arr``."""
    dt = 1.0 / gate._sample_rate
    attack_rate = 1.0 / max(gate._attack_ms / 1000.0, dt)
    release_rate = 1.0 / max(gate._release_ms / 1000.0, dt)
    hold_time = gate._hold_ms / 1000.0
    gate._ensure_buffers(n)
    att_arr = gate._attenuation_buf[:n].copy()
    is_open, attenuation, held_time = gate._state_machine_vector(
        level_arr,
        n,
        dt,
        attack_rate,
        release_rate,
        hold_time,
        att_arr,
    )
    return att_arr, is_open, attenuation, held_time


def _run_reference(gate: NoiseGate, level_arr: np.ndarray, n: int) -> tuple[np.ndarray, bool, float, float]:
    """Run the verbatim reference loop with the gate's parameters/state."""
    dt = 1.0 / gate._sample_rate
    attack_rate = 1.0 / max(gate._attack_ms / 1000.0, dt)
    release_rate = 1.0 / max(gate._release_ms / 1000.0, dt)
    hold_time = gate._hold_ms / 1000.0
    att_arr = np.empty(n, dtype=np.float64)
    is_open, attenuation, held_time = reference_state_machine(
        level_arr,
        n,
        dt,
        attack_rate,
        release_rate,
        hold_time,
        att_arr,
        open_thr=gate._open_threshold,
        close_thr=gate._close_threshold,
        is_open=gate._is_open,
        attenuation=gate._attenuation,
        held_time=gate._held_time,
    )
    return att_arr, is_open, attenuation, held_time


def _assert_equivalent(gate: NoiseGate, level_arr: np.ndarray) -> None:
    """Bitwise-compare vector vs reference on the gate's current state."""
    n = len(level_arr)
    v_att, v_open, v_atten, v_held = _run_vector(gate, level_arr, n)
    r_att, r_open, r_atten, r_held = _run_reference(gate, level_arr, n)

    np.testing.assert_array_equal(
        v_att,
        r_att,
        err_msg="attenuation arrays diverged (vectorized path is not bit-identical to the loop)",
    )
    assert v_open == r_open, f"is_open diverged: vector={v_open} ref={r_open}"
    assert v_atten == r_atten, f"final attenuation diverged: vector={v_atten!r} ref={r_atten!r}"
    assert v_held == r_held, f"final held_time diverged: vector={v_held!r} ref={r_held!r}"


# ═════════════════════════════════════════════════════════════════════════
# Edge cases
# ═════════════════════════════════════════════════════════════════════════


class TestVectorScalarEquivalenceEdges:
    def test_single_sample(self):
        gate = _make_gate()
        gate._is_open = True
        for level in (0.0, gate._close_threshold, gate._open_threshold, 1.0):
            _assert_equivalent(gate, np.array([level]))
            # and once from the closed state
            gate._is_open = False
            gate._attenuation = 0.3
            gate._held_time = 0.05
            _assert_equivalent(gate, np.array([level]))
            gate._is_open = True
            gate._attenuation = 1.0
            gate._held_time = 0.0

    def test_exact_threshold_boundaries(self):
        """Levels exactly AT a threshold fire NO event (strict > / <)."""
        gate = _make_gate()
        open_thr = gate._open_threshold
        close_thr = gate._close_threshold
        levels = np.array(
            [
                open_thr,  # == open: not >, no event
                close_thr,  # == close: not <, no event
                open_thr * 1.0000001,  # just above open
                close_thr * 0.9999999,  # just below close
                (open_thr + close_thr) / 2.0,  # hysteresis band
            ]
        )
        gate._is_open = True
        _assert_equivalent(gate, levels)
        gate._is_open = False
        _assert_equivalent(gate, levels)

    def test_zero_hold_time_releases_on_close_sample(self):
        """hold_ms=0: the close sample itself must already release."""
        gate = _make_gate(hold_ms=0.0)
        open_thr = gate._open_threshold
        levels = np.array([open_thr * 2.0, 0.0, 0.0, open_thr * 2.0])
        gate._is_open = True
        _assert_equivalent(gate, levels)

    def test_inverted_thresholds(self):
        """open_thr < close_thr inverts the hysteresis band; open wins ties."""
        gate = _make_gate(open_db=-30.0, close_db=-20.0)
        assert gate._open_threshold < gate._close_threshold
        levels = np.array(
            [
                (gate._open_threshold + gate._close_threshold) / 2.0,  # both fire: open precedence
                0.0,
                gate._close_threshold * 2.0,  # above close (and above open): open event
                (gate._open_threshold + gate._close_threshold) / 2.0,
                0.0,
            ]
        )
        gate._is_open = True
        _assert_equivalent(gate, levels)

    def test_full_swing_open_close_cycles(self):
        gate = _make_gate()
        open_thr = gate._open_threshold
        close_thr = gate._close_threshold
        levels = np.array([open_thr * 2.0] * 8 + [0.0] * 40 + [open_thr * 2.0] * 8 + [close_thr * 0.5] * 40)
        gate._is_open = True
        _assert_equivalent(gate, levels)

    def test_pathological_alternating_levels(self):
        """Per-sample threshold oscillation — worst-case run count.

        This is the input class the scalar fallback exists for; it must
        still agree bit-for-bit.
        """
        gate = _make_gate(attack_ms=5.0, release_ms=5.0, hold_ms=1.0)
        rng = np.random.default_rng(1234)
        high = gate._open_threshold * 2.0
        low = gate._close_threshold * 0.1
        levels = np.where(rng.random(4096) < 0.5, high, low)
        gate._is_open = True
        _assert_equivalent(gate, levels)

    def test_degenerate_thresholds_collapse_decay_rate(self):
        """open <= close at construction sets the 0.001 fallback decay."""
        gate = _make_gate(open_db=-30.0, close_db=-30.0)
        assert gate._decay_rate == 0.001
        levels = np.full(64, gate._open_threshold)
        gate._is_open = True
        _assert_equivalent(gate, levels)

    @pytest.mark.parametrize("sample_rate", [8000, 16000, 22050, 44100, 48000])
    def test_sample_rates(self, sample_rate):
        gate = _make_gate(sample_rate=sample_rate)
        rng = np.random.default_rng(sample_rate)
        levels = np.abs(rng.normal(0.02, 0.05, 700))
        gate._is_open = True
        _assert_equivalent(gate, levels)


# ═════════════════════════════════════════════════════════════════════════
# Randomized sweeps (multi-chunk carry, randomized parameters)
# ═════════════════════════════════════════════════════════════════════════


class TestVectorScalarEquivalenceRandomized:
    def test_randomized_configs_and_level_sequences(self):
        rng = np.random.default_rng(20260227)
        for _trial in range(40):
            sample_rate = int(rng.choice([8000, 16000, 44100, 48000]))
            gate = _make_gate(
                open_db=float(rng.uniform(-60.0, -10.0)),
                close_db=float(rng.uniform(-70.0, -5.0)),
                attack_ms=float(rng.choice([0.0, 1.0, 25.0, 200.0])),
                hold_ms=float(rng.choice([0.0, 5.0, 200.0, 1000.0])),
                release_ms=float(rng.choice([0.0, 1.0, 150.0, 800.0])),
                sample_rate=sample_rate,
            )
            # random walk level envelope, strictly non-negative
            walk = np.cumsum(rng.normal(0.0, 0.2, 900))
            levels = np.abs(0.05 + walk)
            # feed in variable-size chunks, carrying state between chunks
            # through the gate's real state attributes (like process() does)
            pos = 0
            while pos < len(levels):
                n = int(rng.choice([1, 2, 7, 160, 333]))
                chunk = levels[pos : pos + n]
                if chunk.size == 0:
                    break
                # run BOTH machines against (and then adopt) the same
                # starting state so the comparison is apples-to-apples
                state = (gate._is_open, gate._attenuation, gate._held_time)
                _assert_equivalent(gate, chunk)
                # adopt the REFERENCE result as the carried state (both
                # machines agree bitwise, per _assert_equivalent)
                att_arr, is_open, attenuation, held_time = _run_reference(gate, chunk, len(chunk))
                gate._is_open = is_open
                gate._attenuation = attenuation
                gate._held_time = held_time
                pos += n
            assert state[0] in (True, False)

    def test_randomized_seeds_sweep(self):
        for seed in range(25):
            rng = np.random.default_rng(seed)
            gate = _make_gate(
                open_db=float(rng.uniform(-50.0, -15.0)),
                close_db=float(rng.uniform(-60.0, -10.0)),
                attack_ms=float(rng.uniform(0.0, 60.0)),
                hold_ms=float(rng.uniform(0.0, 300.0)),
                release_ms=float(rng.uniform(0.0, 400.0)),
                sample_rate=16000,
            )
            levels = np.abs(rng.normal(0.01, 0.08, 640))
            gate._is_open = bool(rng.integers(0, 2))
            gate._attenuation = float(rng.uniform(0.0, 1.0))
            gate._held_time = float(rng.uniform(0.0, 0.5))
            _assert_equivalent(gate, levels)

    def test_held_time_carry_across_chunk_boundary(self):
        """A closed run spanning chunks continues the held-time sum."""
        gate = _make_gate(hold_ms=200.0, release_ms=100.0)
        gate._is_open = False
        gate._attenuation = 0.8
        gate._held_time = 0.0
        # ~150 ms closed at 16 kHz (2400 samples): hold (200 ms) NOT
        # reached in chunk 1, reached 800 samples into chunk 2 —
        # exercises the carried-timer seed path AND the post-boundary
        # release ramp.
        silence = np.zeros(4800)
        chunk1, chunk2 = silence[:2400], silence[2400:]
        _assert_equivalent(gate, chunk1)
        att1, is_open1, attenuation1, held1 = _run_reference(gate, chunk1, len(chunk1))
        gate._is_open = is_open1
        gate._attenuation = attenuation1
        gate._held_time = held1
        _assert_equivalent(gate, chunk2)
        # sanity: hold NOT reached in chunk 1, release actually engaged
        # in chunk 2 (timer carried across the boundary crossed hold)
        assert att1[-1] == 0.8
        att2, _, _, held2 = _run_reference(gate, chunk2, len(chunk2))
        assert held2 > 0.2
        assert att2[-1] < 0.8


# ═════════════════════════════════════════════════════════════════════════
# End-to-end process() equivalence
# ═════════════════════════════════════════════════════════════════════════


def reference_process(gate: NoiseGate, audio: np.ndarray) -> np.ndarray:
    """The pre-vectorization process() pipeline around the reference loop.

    Reproduces the exact float op order of the old implementation:
    vectorized peak-hold level estimate (unchanged by the swap), the
    per-sample loop (reference), then the float64 multiply + float32 cast.
    """
    samples = np.ravel(audio).astype(np.float32, copy=False)
    n = len(samples)
    dt = 1.0 / gate._sample_rate

    attack_rate = 1.0 / max(gate._attack_ms / 1000.0, dt)
    release_rate = 1.0 / max(gate._release_ms / 1000.0, dt)
    hold_time = gate._hold_ms / 1000.0
    open_thr = gate._open_threshold
    close_thr = gate._close_threshold
    decay = gate._decay_rate

    # peak-hold estimator — identical math to production (linear-decay trick)
    abs_x = np.abs(samples).astype(np.float64)
    i_arr = np.arange(n, dtype=np.float64)
    y = np.empty(n + 1, dtype=np.float64)
    y[0] = gate._level
    y[1:] = abs_x + i_arr * decay
    np.maximum.accumulate(y, out=y)
    level_arr = np.maximum(y[1:] - i_arr * decay, 0.0)

    att_arr = np.empty(n, dtype=np.float64)
    is_open, attenuation, held_time = reference_state_machine(
        level_arr,
        n,
        dt,
        attack_rate,
        release_rate,
        hold_time,
        att_arr,
        open_thr=open_thr,
        close_thr=close_thr,
        is_open=gate._is_open,
        attenuation=gate._attenuation,
        held_time=gate._held_time,
    )

    output_f64 = samples.astype(np.float64) * att_arr
    output = output_f64.astype(np.float32)

    gate._level = float(level_arr[-1])
    gate._is_open = is_open
    gate._attenuation = attenuation
    gate._held_time = held_time
    return output


class TestProcessEndToEndEquivalence:
    def test_process_matches_reference_pipeline(self):
        rng = np.random.default_rng(777)
        gate = _make_gate()
        ref = _make_gate()
        for chunk_idx in range(30):
            # bursts of speech-like loud signal and silence
            if chunk_idx % 3 == 0:
                audio = (rng.normal(0.0, 0.05, 160)).astype(np.float32)
            else:
                audio = (rng.normal(0.0, 0.2, 160)).astype(np.float32)
            expected = reference_process(ref, audio.copy())
            got = gate.process(audio.copy(), 16000)
            assert got is not None
            np.testing.assert_array_equal(
                got,
                expected,
                err_msg=f"process() output diverged from the reference pipeline at chunk {chunk_idx}",
            )
            # carried state must agree too
            assert gate._is_open == ref._is_open
            assert gate._attenuation == ref._attenuation
            assert gate._held_time == ref._held_time
            assert gate._level == ref._level

    def test_process_multi_shape_and_reset(self):
        rng = np.random.default_rng(31)
        gate = _make_gate()
        ref = _make_gate()
        for n in (1, 2, 3, 159, 160, 161, 511, 1024):
            audio = (rng.normal(0.0, 0.15, n)).astype(np.float32)
            expected = reference_process(ref, audio.copy())
            got = gate.process(audio.copy(), 16000)
            np.testing.assert_array_equal(got, expected)
        gate.reset()
        ref.reset()
        assert gate._is_open is True and gate._attenuation == 1.0

    def test_output_shape_and_dtype_preserved(self):
        gate = _make_gate()
        audio = np.zeros((4, 40), dtype=np.float32)  # 2-D input
        out = gate.process(audio, 16000)
        assert out.shape == (4, 40)
        assert out.dtype == np.float32


# ═════════════════════════════════════════════════════════════════════════
# Vector path must actually be exercised (guard against silent fallback)
# ═════════════════════════════════════════════════════════════════════════


class TestVectorPathEngagement:
    def test_typical_chunk_uses_vector_path(self):
        """A typical speech chunk must take the vectorized path, not the
        scalar fallback — otherwise the optimization is dead code."""
        gate = _make_gate()
        calls: list[int] = []
        original = gate._state_machine_scalar

        def spy(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        gate._state_machine_scalar = spy  # type: ignore[method-assign]
        rng = np.random.default_rng(5)
        for _ in range(10):
            gate.process((rng.normal(0.0, 0.2, 160)).astype(np.float32), 16000)
        assert not calls, "typical chunks must not fall back to the scalar loop"

    def test_process_accepts_gate_starting_closed(self):
        """A gate carried closed across process() calls stays consistent."""
        gate = _make_gate()
        gate._is_open = False
        gate._attenuation = 0.5
        gate._held_time = 0.1
        silence = np.zeros(320, dtype=np.float32)
        out = gate.process(silence, 16000)
        assert out is not None
        assert np.all(out == 0.0) or np.all(out <= 0.5)


# ═════════════════════════════════════════════════════════════════════════
# Guard: the scalar fallback still exists for pathological inputs
# ═════════════════════════════════════════════════════════════════════════


class TestScalarFallbackContract:
    def test_scalar_path_matches_reference_on_pathological_input(self):
        gate = _make_gate(attack_ms=5.0, release_ms=5.0, hold_ms=1.0)
        rng = np.random.default_rng(99)
        high = gate._open_threshold * 2.0
        low = 0.0
        levels = np.where(rng.random(3000) < 0.5, high, low)
        gate._is_open = True
        v_att, v_open, v_atten, v_held = _run_vector(gate, levels, len(levels))
        r_att, r_open, r_atten, r_held = _run_reference(gate, levels, len(levels))
        np.testing.assert_array_equal(v_att, r_att)
        assert (v_open, v_atten, v_held) == (r_open, r_atten, r_held)

    def test_db_to_mul_roundtrip_for_thresholds(self):
        gate = _make_gate(open_db=-26.0, close_db=-32.0)
        assert gate._open_threshold == pytest.approx(db_to_mul(-26.0))
        assert gate._close_threshold == pytest.approx(db_to_mul(-32.0))
        assert gate._open_threshold > gate._close_threshold
