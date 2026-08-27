"""Noise gate / downward expander (OBS-style).

The peak-hold level estimator is vectorized with
``np.maximum.accumulate`` (linear-decay peak-hold trick -- see comment
in ``process``). The open/close + attack/hold/release state machine
remains a Python loop because its state transitions are inherently
sequential, but it now operates on the pre-computed ``level`` array --
no per-sample ``abs()`` or peak-hold bookkeeping in the loop body.

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

        open_thr = self._open_threshold
        close_thr = self._close_threshold
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

        # State machine (sequential -- inherently stateful). Operates on
        # the pre-computed ``level_arr`` so the inner loop is cheap (a few
        # float comparisons + arithmetic, no abs/max calls).
        attenuation_arr = self._attenuation_buf[:n]
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
