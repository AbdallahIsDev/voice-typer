"""Noise gate / downward expander (OBS-style).

The peak-hold level estimator is vectorized with
``np.maximum.accumulate`` (linear-decay peak-hold trick -- see comment
in ``process``).

The open/close + attack/hold/release state machine is ALSO vectorized
(``_state_machine_vector``):

* the per-sample ``is_open`` state is a pure "last effective event
  wins" scan -- an open event (``level > open_thr``) always sets the
  state open, a close event (``level < close_thr`` while open) sets it
  closed -- so the state after each sample is resolved by two
  ``np.maximum.accumulate`` passes over the open/close event indices;
* within a maximal open run the attenuation is a monotone increasing
  ``cumsum`` of ``attack_rate*dt`` clamped element-wise at 1.0 (the
  per-step clamp can only bind at the top of a monotone sequence, so an
  element-wise ``np.minimum`` over the raw cumsum is exactly the
  progressive clamp); the release ramp is the mirror image with
  ``np.maximum(., 0.0)`` after the hold phase;
* the hold timer accumulates ``dt`` by repeated float addition in the
  reference loop, so the vectorized path reproduces it with a
  ``np.cumsum`` seeded with the carried value -- identical left-to-right
  float additions, hence identical ``held_time > hold_time`` comparisons
  even at exact-equality boundaries.

Bit-identical output to the loop is pinned by
``tests/test_noise_gate_vector_equivalence.py`` (randomized + edge
inputs, multi-chunk carry, end-to-end ``process()`` comparison against
the verbatim pre-vectorization loop).

A scalar per-sample loop (``_state_machine_scalar``) is kept as the
fallback for pathological chunks whose level oscillates across a
gate threshold on (almost) every sample: the vectorized path is O(runs)
numpy dispatches, and when the run count explodes past
``max(4, n // 32)`` the flat Python loop is cheaper. The fallback is
the verbatim pre-vectorization loop, so both paths agree bitwise.

when ``adaptive=True`` is passed to the constructor, the gate
samples the first ``_ADAPTIVE_CALIBRATION_MS`` of audio after each
``reset()`` / construction to estimate the ambient noise floor (RMS),
then derives ``open_threshold = noise_floor + 6dB`` and
``close_threshold = noise_floor + 0dB``. During calibration the gate
is OPEN (full pass-through) so the first words aren't dropped. Once
calibrated, the state machine uses the derived thresholds (overriding
the hardcoded ``-26 / -32 dBFS`` defaults).
"""

from __future__ import annotations

import logging
import math

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import (  # noqa: E402
    AudioFilter,
    db_to_mul,
    mul_to_db,
)

log = logging.getLogger(__name__)

# adaptive calibration constants
_ADAPTIVE_CALIBRATION_MS: float = 500.0
_ADAPTIVE_OPEN_OFFSET_DB: float = 6.0
_ADAPTIVE_CLOSE_OFFSET_DB: float = 0.0
_ADAPTIVE_MIN_THRESHOLD_DB: float = -90.0
_ADAPTIVE_MAX_THRESHOLD_DB: float = 0.0


class NoiseGate(AudioFilter):
    """OBS-style noise gate with peak-hold level estimator and state machine.

    Unlike a hard gate (which snaps to silence), this is a smooth
    downward expander: below the close threshold, gain is reduced with
    attack/release smoothing. This preserves speech tails and avoids
    audible chopping.
    """

    def __init__(
        self,
        open_threshold_db: float = -26.0,
        close_threshold_db: float = -32.0,
        attack_ms: float = 25.0,
        hold_ms: float = 200.0,
        release_ms: float = 150.0,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        adaptive: bool = False,
    ) -> None:
        self.name = "NoiseGate"
        self._adaptive = bool(adaptive)
        self._initial_open_threshold = db_to_mul(open_threshold_db)
        self._initial_close_threshold = db_to_mul(close_threshold_db)
        self._open_threshold = self._initial_open_threshold
        self._close_threshold = self._initial_close_threshold
        self._attack_ms = float(attack_ms)
        self._hold_ms = float(hold_ms)
        self._release_ms = float(release_ms)
        self._sample_rate = int(sample_rate)

        # NOISE-GATE-INIT: gate must start OPEN with full attenuation (1.0).
        # Starting closed silences the first 100-300ms of speech.
        self._is_open: bool = True
        self._attenuation: float = 1.0
        self._level: float = 0.0
        self._held_time: float = 0.0

        if self._open_threshold > self._close_threshold:
            self._decay_rate = (self._open_threshold - self._close_threshold) / (self._sample_rate / 75.0)
        else:
            self._decay_rate = 0.001

        # adaptive-calibration state
        self._calibration_target = int(self._sample_rate * _ADAPTIVE_CALIBRATION_MS / 1000.0)
        self._calibration_sumsq: float = 0.0
        self._calibration_count: int = 0
        self._calibrated: bool = False
        if not self._adaptive:
            self._calibrated = True

        # pre-allocated per-chunk working buffers for the peak-hold
        # level estimator + state-machine + output gain stage. Lazy-
        # resized to the largest chunk seen (mirror compressor._env_db_buf)
        # so the first call allocates and subsequent calls reuse. Before
        # this pre-allocation, process() allocated ~10 fresh float64
        # arrays per chunk (abs_x, i_arr, y, y_with_init, z,
        # level_arr, attenuation_arr, output_f64, output_f32) on the
        # PortAudio RT thread. All buffers are sliced to ``[:n]`` per
        # call so a larger chunk allocates once and subsequent same-or-
        # smaller chunks reuse without reallocation.
        self._abs_buf: np.ndarray | None = None
        self._i_arr_buf: np.ndarray | None = None  # cached np.arange, sliced
        self._y_buf: np.ndarray | None = None  # size n+1 (carries _level prefix)
        self._level_arr_buf: np.ndarray | None = None  # also reused as i_arr*decay temp
        self._attenuation_buf: np.ndarray | None = None
        self._output_f64_buf: np.ndarray | None = None
        self._output_f32_buf: np.ndarray | None = None
        # state-machine scan buffers (vectorized open/close tracking).
        # Bool event masks + float64 "last event index" accumulators (the
        # indices are stored as float64 because the cached ``np.arange``
        # is float64 and every integer below 2**53 is exact) + the bool
        # per-sample gate-state / seen-open outputs.
        self._open_ev_buf: np.ndarray | None = None
        self._close_ev_buf: np.ndarray | None = None
        self._last_open_buf: np.ndarray | None = None
        self._last_close_buf: np.ndarray | None = None
        self._state_open_buf: np.ndarray | None = None
        self._has_open_buf: np.ndarray | None = None

    def _ensure_buffers(self, n: int) -> None:
        """Lazy-resize the per-chunk working buffers to at least ``n`` samples.

        Each buffer is checked independently and grown to ``max(n, 1024)``
        on the first call or when a larger chunk arrives. ``_i_arr_buf``
        caches ``np.arange(cap)`` because ``np.arange`` has no ``out=``
        kwarg — it is regenerated only when the capacity grows, then sliced
        to ``[:n]`` on every call (values ``[0, 1, ..., n-1]`` are correct
        for any ``n <= cap``).
        """
        cap = max(n, 1024)
        if self._abs_buf is None or self._abs_buf.shape[0] < n:
            self._abs_buf = np.empty(cap, dtype=np.float64)
        if self._i_arr_buf is None or self._i_arr_buf.shape[0] < n:
            # np.arange has no out= kwarg — regenerate when capacity grows.
            self._i_arr_buf = np.arange(cap, dtype=np.float64)
        if self._y_buf is None or self._y_buf.shape[0] < n + 1:
            self._y_buf = np.empty(cap + 1, dtype=np.float64)
        if self._level_arr_buf is None or self._level_arr_buf.shape[0] < n:
            self._level_arr_buf = np.empty(cap, dtype=np.float64)
        if self._attenuation_buf is None or self._attenuation_buf.shape[0] < n:
            self._attenuation_buf = np.empty(cap, dtype=np.float64)
        if self._output_f64_buf is None or self._output_f64_buf.shape[0] < n:
            self._output_f64_buf = np.empty(cap, dtype=np.float64)
        if self._output_f32_buf is None or self._output_f32_buf.shape[0] < n:
            self._output_f32_buf = np.empty(cap, dtype=np.float32)
        if self._open_ev_buf is None or self._open_ev_buf.shape[0] < n:
            self._open_ev_buf = np.empty(cap, dtype=bool)
        if self._close_ev_buf is None or self._close_ev_buf.shape[0] < n:
            self._close_ev_buf = np.empty(cap, dtype=bool)
        if self._last_open_buf is None or self._last_open_buf.shape[0] < n:
            self._last_open_buf = np.empty(cap, dtype=np.float64)
        if self._last_close_buf is None or self._last_close_buf.shape[0] < n:
            self._last_close_buf = np.empty(cap, dtype=np.float64)
        if self._state_open_buf is None or self._state_open_buf.shape[0] < n:
            self._state_open_buf = np.empty(cap, dtype=bool)
        if self._has_open_buf is None or self._has_open_buf.shape[0] < n:
            self._has_open_buf = np.empty(cap, dtype=bool)

    def _consume_calibration_chunk(self, samples: np.ndarray) -> None:
        """accumulate samples toward the noise-floor estimate."""
        remaining = self._calibration_target - self._calibration_count
        if remaining <= 0:
            return
        take = min(remaining, len(samples))
        if take <= 0:
            return
        chunk = samples[:take].astype(np.float64, copy=False)
        self._calibration_sumsq += float(np.dot(chunk, chunk))
        self._calibration_count += take
        if self._calibration_count >= self._calibration_target:
            if self._calibration_sumsq <= 0.0:
                noise_floor_db = mul_to_db(self._initial_open_threshold)
            else:
                rms = math.sqrt(self._calibration_sumsq / self._calibration_count)
                noise_floor_db = mul_to_db(rms)
            open_db = noise_floor_db + _ADAPTIVE_OPEN_OFFSET_DB
            close_db = noise_floor_db + _ADAPTIVE_CLOSE_OFFSET_DB
            open_db = max(_ADAPTIVE_MIN_THRESHOLD_DB, min(_ADAPTIVE_MAX_THRESHOLD_DB, open_db))
            close_db = max(_ADAPTIVE_MIN_THRESHOLD_DB, min(_ADAPTIVE_MAX_THRESHOLD_DB, close_db))
            if open_db <= close_db:
                open_db = close_db + 1.0
            self._open_threshold = db_to_mul(open_db)
            self._close_threshold = db_to_mul(close_db)
            if self._open_threshold > self._close_threshold:
                self._decay_rate = (self._open_threshold - self._close_threshold) / (self._sample_rate / 75.0)
            self._calibrated = True
            log.debug(
                "[NOISE-GATE] adaptive calibration complete: noise_floor=%.1fdBFS, open=%.1fdBFS, close=%.1fdBFS",
                noise_floor_db,
                open_db,
                close_db,
            )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)
        if n == 0:
            return audio
        dt = 1.0 / sample_rate

        # if adaptive calibration in progress, accumulate and return
        # input unchanged (gate stays OPEN during calibration).
        if not self._calibrated:
            self._consume_calibration_chunk(samples)
            abs_x_init = np.abs(samples).astype(np.float64)
            self._level = float(abs_x_init.max()) if abs_x_init.size > 0 else 0.0
            return audio.reshape(original_shape)

        attack_rate = 1.0 / max(self._attack_ms / 1000.0, dt)
        release_rate = 1.0 / max(self._release_ms / 1000.0, dt)
        hold_time = self._hold_ms / 1000.0

        decay = self._decay_rate

        # Vectorized peak-hold level estimator (linear decay).
        #
        # The OBS recurrence is: level[i] = max(|x[i]|, level[i-1] - decay).
        # Substituting z[i] = level[i] + i*decay gives
        #   z[i] = max(|x[i]| + i*decay, z[i-1])
        # which is a running maximum -- vectorizable with
        # ``np.maximum.accumulate``. The carried ``self._level`` is the
        # value of ``z[-1]`` from the previous chunk.
        #
        # All intermediate arrays reuse the pre-allocated lazy-resized
        # buffers (``_abs_buf`` / ``_i_arr_buf`` / ``_y_buf`` /
        # ``_level_arr_buf``) instead of allocating ~6 fresh float64
        # arrays per chunk. ``_i_arr_buf`` caches ``np.arange(cap)`` and
        # is sliced to ``[:n]`` (values are correct for any ``n <= cap``).
        # ``_level_arr_buf`` is dual-used: first as the ``i_arr * decay``
        # temp, then overwritten with the final ``level_arr`` — safe because
        # the temp value is fully consumed before the overwrite.
        self._ensure_buffers(n)
        # ``_ensure_buffers`` guarantees the lazily-allocated buffers exist
        # (and are at least ``n`` / ``n + 1`` long) before any use below.
        # The asserts narrow the ``| None`` declared types for the type
        # checker and pin the invariant at runtime (same idiom as
        # noise_suppressor.py's backend guard).
        assert self._abs_buf is not None
        assert self._i_arr_buf is not None
        assert self._y_buf is not None
        assert self._level_arr_buf is not None
        assert self._attenuation_buf is not None
        assert self._output_f64_buf is not None
        # Pre-compute abs outside the state-machine loop (vectorized).
        # ``np.abs(samples)`` returns a float32 array (1 allocation); copy it
        # into the pre-allocated float64 buffer to avoid the original
        # ``.astype(np.float64)`` second allocation. The float32 -> float64
        # upcast is exact (no precision loss), so the result is byte-identical
        # to ``np.abs(samples).astype(np.float64)``.
        abs_x = np.abs(samples)
        abs_buf = self._abs_buf[:n]
        np.copyto(abs_buf, abs_x, casting="same_kind")
        abs_x = abs_buf
        i_arr = self._i_arr_buf[:n]
        y_buf = self._y_buf[: n + 1]  # slice to exact size for in-place ops
        y_buf[0] = self._level
        # y_buf[1:] = abs_x + i_arr * decay, computed in-place via the
        # level_arr_buf temp (overwritten below with the final level_arr).
        tmp = self._level_arr_buf[:n]
        np.multiply(i_arr, decay, out=tmp)
        np.add(abs_x, tmp, out=y_buf[1:])
        # In-place cumulative max into y_buf itself; y_buf[1:] is then z.
        np.maximum.accumulate(y_buf, out=y_buf)
        # level_arr = max(z - i_arr*decay, 0.0), in-place into level_arr_buf.
        np.multiply(i_arr, decay, out=tmp)
        np.subtract(y_buf[1:], tmp, out=tmp)
        np.maximum(tmp, 0.0, out=tmp)
        level_arr = tmp

        # State machine -- vectorized (see the module docstring for the
        # equivalence argument and the scalar fallback rationale).
        # Operates on the pre-computed ``level_arr``; output is bit-
        # identical to the pre-vectorization per-sample loop (pinned by
        # tests/test_noise_gate_vector_equivalence.py).
        attenuation_arr = self._attenuation_buf[:n]
        is_open, attenuation, held_time = self._state_machine_vector(
            level_arr, n, dt, attack_rate, release_rate, hold_time, attenuation_arr
        )

        # output = (samples.astype(float64) * attenuation_arr).astype(float32)
        # computed in-place via the pre-allocated f64 + f32 buffers.
        output_f64 = self._output_f64_buf[:n]
        np.copyto(output_f64, samples, casting="same_kind")
        np.multiply(output_f64, attenuation_arr, out=output_f64)
        output_f32 = self._output_f32_buf[:n]
        np.copyto(output_f32, output_f64, casting="same_kind")
        output = output_f32

        self._level = float(level_arr[-1])
        self._is_open = is_open
        self._attenuation = attenuation
        self._held_time = held_time

        return output.reshape(original_shape)

    def _state_machine_scalar(
        self,
        level_arr: np.ndarray,
        n: int,
        dt: float,
        attack_rate: float,
        release_rate: float,
        hold_time: float,
        attenuation_arr: np.ndarray,
    ) -> tuple[bool, float, float]:
        """Original per-sample state machine (verbatim, pre-vectorization).

        Two roles: (1) the fallback for pathological chunks whose run
        count explodes past the vectorized path's dispatch cutoff (see
        :meth:`_state_machine_vector`), and (2) the reference
        implementation pinned bit-for-bit by
        ``tests/test_noise_gate_vector_equivalence.py``. Do NOT edit the
        loop body without re-deriving that equivalence.

        Returns ``(is_open, attenuation, held_time)`` after the chunk.
        """
        open_thr = self._open_threshold
        close_thr = self._close_threshold
        is_open = self._is_open
        attenuation = self._attenuation
        held_time = self._held_time

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

    def _scan_gate_state(self, level_arr: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Resolve the per-sample open/closed gate state (vectorized scan).

        The loop's threshold update is a pure "last effective event wins"
        scan: ``level > open_thr`` forces the state open; ``level <
        close_thr`` forces it closed *only* when currently open (a no-op
        when already closed). Either way the state after sample ``i`` is
        "open" exactly when the last event at or before ``i`` is an open
        event (with the open event winning ties, matching the loop's
        ``if/elif`` precedence when both comparisons fire), or when no
        event has occurred yet and the carried-in state was open.

        Returns ``(state_open, run_starts)`` where ``state_open`` is the
        bool per-sample state array and ``run_starts`` holds the indices
        of every state-flip boundary (the starts of runs 2..R; run 1
        always starts at 0).
        """
        open_ev = self._open_ev_buf[:n]
        close_ev = self._close_ev_buf[:n]
        np.greater(level_arr, self._open_threshold, out=open_ev)
        np.less(level_arr, self._close_threshold, out=close_ev)

        i_arr = self._i_arr_buf[:n]
        last_open = self._last_open_buf[:n]
        last_close = self._last_close_buf[:n]
        # "no event yet" is encoded as -1; event samples are stamped with
        # their index, then a running maximum carries the last event
        # index forward.
        last_open.fill(-1.0)
        np.copyto(last_open, i_arr, where=open_ev)
        np.maximum.accumulate(last_open, out=last_open)
        last_close.fill(-1.0)
        np.copyto(last_close, i_arr, where=close_ev)
        np.maximum.accumulate(last_close, out=last_close)

        state_open = self._state_open_buf[:n]
        np.greater_equal(last_open, last_close, out=state_open)
        if not self._is_open:
            # Carried-in state closed: a "no event yet" prefix (both
            # indices -1) must stay closed, so gate the comparison on
            # whether an open event has been seen at all.
            has_open = self._has_open_buf[:n]
            np.greater_equal(last_open, 0.0, out=has_open)
            np.logical_and(state_open, has_open, out=state_open)

        if n > 1:
            bounds = self._has_open_buf[: n - 1]
            np.not_equal(state_open[1:], state_open[:-1], out=bounds)
            run_starts = np.flatnonzero(bounds)
            run_starts += 1
        else:
            run_starts = np.empty(0, dtype=np.intp)
        return state_open, run_starts

    def _state_machine_vector(
        self,
        level_arr: np.ndarray,
        n: int,
        dt: float,
        attack_rate: float,
        release_rate: float,
        hold_time: float,
        attenuation_arr: np.ndarray,
    ) -> tuple[bool, float, float]:
        """Vectorized attack/hold/release state machine (bit-exact twin
        of :meth:`_state_machine_scalar`).

        Layout: resolve the per-sample open/closed state with the
        last-event scan, then fill each maximal same-state run with one
        cumulative sum (attack: monotone increasing, element-wise clamped
        at 1.0; release: monotone decreasing after the hold phase,
        element-wise clamped at 0.0). For a monotone sequence the
        per-step clamp of the loop is exactly an element-wise clamp over
        the raw cumsum, and the held-time sums are reproduced by a
        ``np.cumsum`` seeded with the carried value (identical
        left-to-right float additions as the loop).

        Falls back to :meth:`_state_machine_scalar` when the run count
        exceeds ``max(4, n // 32)``: per-run numpy dispatch (~6 calls per
        run) loses to the flat Python loop once the level oscillates
        across a threshold on nearly every sample.

        Returns ``(is_open, attenuation, held_time)`` after the chunk.
        """
        state_open, run_starts = self._scan_gate_state(level_arr, n)
        runs = len(run_starts) + 1
        if runs > max(4, n // 32):
            return self._state_machine_scalar(level_arr, n, dt, attack_rate, release_rate, hold_time, attenuation_arr)

        d_attack = attack_rate * dt
        d_release = release_rate * dt
        is_open0 = self._is_open
        # ``_y_buf`` (size cap + 1) is free here: the peak-hold estimator
        # consumed it before ``level_arr`` was extracted. It is the
        # cumsum scratch for both the attack ramp and the hold/release
        # timeline of each run.
        scratch = self._y_buf
        att = attenuation_arr

        entry_att = self._attenuation
        held_final = self._held_time
        seg_start = 0
        for boundary in run_starts:
            seg_end = int(boundary)
            entry_att, held_seg = self._fill_state_run(
                state_open,
                att,
                scratch,
                seg_start,
                seg_end,
                entry_att,
                is_open0,
                dt,
                d_attack,
                d_release,
                hold_time,
                self._held_time if seg_start == 0 else held_final,
            )
            if not state_open[seg_end - 1]:
                held_final = held_seg
            seg_start = seg_end
        entry_att, held_seg = self._fill_state_run(
            state_open,
            att,
            scratch,
            seg_start,
            n,
            entry_att,
            is_open0,
            dt,
            d_attack,
            d_release,
            hold_time,
            self._held_time if seg_start == 0 else held_final,
        )
        if not state_open[n - 1]:
            held_final = held_seg

        return bool(state_open[n - 1]), entry_att, held_final

    def _fill_state_run(
        self,
        state_open: np.ndarray,
        att: np.ndarray,
        scratch: np.ndarray,
        s: int,
        e: int,
        entry_att: float,
        is_open0: bool,
        dt: float,
        d_attack: float,
        d_release: float,
        hold_time: float,
        held_entry: float,
    ) -> tuple[float, float]:
        """Fill one maximal same-state run ``att[s:e]`` of the state machine.

        Returns ``(exit_att, exit_held)`` — the loop-equivalent running
        attenuation after the run's last sample, and the held-time value
        after the run's last sample (meaningful only for closed runs; the
        loop leaves ``held_time`` untouched across open samples).

        Exactness notes (see :meth:`_state_machine_vector`):

        * OPEN run: ``att`` is the monotone increasing cumsum of
          ``d_attack`` seeded with ``entry_att`` — the same left-to-right
          float additions as the loop's ``attenuation += attack_rate*dt``
          — clamped element-wise at 1.0 (== the loop's per-step clamp).
        * CLOSED run: the held-time timeline is a cumsum of ``dt`` seeded
          with ``held_entry`` (``0.0`` when the run starts at a close
          event — the loop resets the timer there — or the carried value
          when the gate was already closed at chunk start). Samples with
          ``held <= hold_time`` hold ``att`` at ``entry_att``; the rest
          release via a monotone decreasing cumsum of ``-d_release``
          clamped element-wise at 0.0.
        """
        length = e - s
        if state_open[s]:
            # OPEN run: monotone increasing attack ramp, clamp at 1.0.
            scratch[0] = entry_att
            scratch[1 : length + 1].fill(d_attack)
            np.cumsum(scratch[: length + 1], out=scratch[: length + 1])
            np.minimum(scratch[1 : length + 1], 1.0, out=att[s:e])
            return float(att[e - 1]), held_entry

        # Held-time seed: a run that starts at a close event resets the
        # timer to 0.0 (the loop writes ``held_time = 0.0`` there, then
        # the closed branch adds ``dt``); the only closed run that does
        # NOT start at a close event is a chunk carried in mid-closed,
        # which continues from the carried timer value.
        held_seed = 0.0 if (s > 0 or is_open0) else held_entry
        scratch[0] = held_seed
        scratch[1 : length + 1].fill(dt)
        np.cumsum(scratch[: length + 1], out=scratch[: length + 1])
        exit_held = float(scratch[length])
        # First ``hold_count`` samples have held_time <= hold_time (the
        # loop's decrement condition is ``held_time > hold_time``), so att
        # stays at the entry value there. searchsorted(right) on the
        # non-decreasing held timeline == the exact count of those
        # samples; the timeline floats are bit-identical to the loop's,
        # so the partition matches even at exact-equality boundaries.
        hold_count = int(np.searchsorted(scratch[1 : length + 1], hold_time, side="right"))
        if hold_count:
            att[s : s + hold_count].fill(entry_att)
        release_len = length - hold_count
        if release_len:
            scratch[0] = entry_att
            scratch[1 : release_len + 1].fill(-d_release)
            np.cumsum(scratch[: release_len + 1], out=scratch[: release_len + 1])
            np.maximum(scratch[1 : release_len + 1], 0.0, out=att[s + hold_count : e])
        return float(att[e - 1]), exit_held

    def reset(self) -> None:
        # NOISE-GATE-INIT: reset to the same open-with-full-attenuation state.
        self._is_open = True
        self._attenuation = 1.0
        self._level = 0.0
        self._held_time = 0.0
        # re-arm adaptive calibration so a mic change re-measures
        # the noise floor. Restore initial thresholds for calibration window.
        if self._adaptive:
            self._open_threshold = self._initial_open_threshold
            self._close_threshold = self._initial_close_threshold
            if self._open_threshold > self._close_threshold:
                self._decay_rate = (self._open_threshold - self._close_threshold) / (self._sample_rate / 75.0)
            self._calibration_sumsq = 0.0
            self._calibration_count = 0
            self._calibrated = False
        # zero the pre-allocated working buffers so the last chunk's
        # raw-audio-derived samples (abs, level, attenuation, output)
        # do not linger in process memory until the numpy allocator
        # reuses the blocks. Mirrors the compressor/limiter/equalizer
        # reset-zero pattern. Guarded for None because the buffers are
        # lazy-allocated on the first ``process()`` call.
        for buf in (
            self._abs_buf,
            self._y_buf,
            self._level_arr_buf,
            self._attenuation_buf,
            self._output_f64_buf,
            self._output_f32_buf,
        ):
            if buf is not None:
                buf.fill(0)
        # _i_arr_buf holds [0, 1, 2, ...] (not audio-derived) — no PII,
        # but zero for consistency and so a stale arange doesn't leak
        # the previous chunk size.
        if self._i_arr_buf is not None:
            self._i_arr_buf.fill(0)
