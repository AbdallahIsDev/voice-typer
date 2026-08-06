"""3-band equalizer (OBS-style crossover).

Vectorized with two ``scipy.signal.lfilter`` calls (one per one-pole
band-state) plus a numpy shift for the 3-sample delay line. The original
per-sample Python loop spent ~1 ms per chunk on the RT thread; the
vectorized version runs in ~50 us.

Bug fix bundled in: the original ``output[i] = ...`` line was
indented OUTSIDE the ``for`` loop (only the last sample was written;
the rest were ``np.empty`` garbage). The vectorized version computes
the full output array, which both fixes the bug and eliminates the
per-sample Python overhead.
"""

from __future__ import annotations

import logging
import math

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import (  # noqa: E402
    ANTIDENORMAL_EPSILON,
    AudioFilter,
    _get_lfilter,
    db_to_mul,
)

log = logging.getLogger(__name__)

LOW_FREQ: float = 800.0
HIGH_FREQ: float = 5000.0


class Equalizer(AudioFilter):
    """3-band equalizer with Linkwitz-Riley-style crossovers.

    Splits audio into Low (<800Hz), Mid (800Hz-5kHz), High (>5kHz) bands
    using cascaded one-pole filters, applies per-band gain, and recombines.

    Ported from OBS ``eq-filter.c``. Uses a 3-sample delay line for phase
    alignment between bands. Anti-denormal epsilon prevents CPU-killing
    denormal floats.
    """

    def __init__(
        self,
        low_db: float = -3.0,
        mid_db: float = 3.0,
        high_db: float = 2.0,
        sample_rate: int = WHISPER_SAMPLE_RATE,
    ) -> None:
        self.name = f"EQ({low_db:+.0f}/{mid_db:+.0f}/{high_db:+.0f}dB)"
        self._low_gain = db_to_mul(low_db)
        self._mid_gain = db_to_mul(mid_db)
        self._high_gain = db_to_mul(high_db)
        self._sample_rate = int(sample_rate)
        self._lf = 2.0 * math.sin(math.pi * LOW_FREQ / self._sample_rate)
        self._hf = 2.0 * math.sin(math.pi * HIGH_FREQ / self._sample_rate)
        self._delay1: float = 0.0
        self._delay2: float = 0.0
        self._delay3: float = 0.0
        self._low_state: float = ANTIDENORMAL_EPSILON
        self._high_state: float = 0.0
        # pre-allocated delay-line buffer (3-prefix + n-input) so
        # process() does not allocate a fresh (n+3)-element array per
        # chunk for the 3-sample delay line. Lazy-resized to the largest
        # chunk seen.
        self._delay_buf: np.ndarray | None = None
        # pre-allocated 1-element float64 zi state buffers for the two
        # lfilter calls (mirror compressor._zi_buf at line 76). Before
        # this, process() allocated two fresh ``np.array([state])`` arrays
        # per chunk. The [0] slot is overwritten with the current band
        # state before each lfilter call; lfilter reads but does not
        # mutate the caller's zi array (it returns the final state as a
        # new array via the second tuple element, which we discard).
        self._low_zi_buf = np.zeros(1, dtype=np.float64)
        self._high_zi_buf = np.zeros(1, dtype=np.float64)
        # pre-allocated float64 working copy of the input + float64 band-
        # sum output buffer. Lazy-resized to the largest chunk seen
        # (mirror compressor._env_db_buf). Before this, process()
        # allocated a fresh ``samples.astype(np.float64)`` copy per chunk
        # plus 3-5 intermediate arrays for the ``low_s*low_gain +
        # mid*mid_gain + high*high_gain`` band-sum expression. The band
        # sum is now computed in-place: ``_output_buf = low_s*low_gain``;
        # ``_tmp_buf = mid*mid_gain``; ``_output_buf += _tmp_buf`` (repeat
        # for high band); then ``astype(np.float32)`` for the final output.
        self._x_f64_buf: np.ndarray | None = None
        self._output_buf: np.ndarray | None = None
        self._tmp_buf: np.ndarray | None = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the EQ crossover coefficients (``_lf``,
        # ``_hf``) are derived from ``self._sample_rate``; feeding
        # audio at a different rate shifts the 800 Hz / 5 kHz
        # crossovers. Python strips this assert under ``-O``; in
        # debug builds a mismatch surfaces as an ``AssertionError``.
        assert sample_rate == self._sample_rate, (
            f"{type(self).__name__} built at {self._sample_rate} Hz, called with {sample_rate} Hz"
        )
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)
        if n == 0:
            return audio

        lf = self._lf
        hf = self._hf
        low_gain = self._low_gain
        mid_gain = self._mid_gain
        high_gain = self._high_gain

        # pre-allocate the float64 working copy of the input + the band-
        # sum output buffer + a scratch temp. Lazy-resized to the largest
        # chunk seen so the first call allocates and subsequent calls
        # reuse. Eliminates ~5 fresh float64 arrays per chunk.
        if self._x_f64_buf is None or self._x_f64_buf.shape[0] < n:
            cap = max(n, 1024)
            self._x_f64_buf = np.empty(cap, dtype=np.float64)
            self._output_buf = np.empty(cap, dtype=np.float64)
            self._tmp_buf = np.empty(cap, dtype=np.float64)
        x = self._x_f64_buf[:n]
        np.copyto(x, samples, casting="same_kind")  # float32 -> float64 (exact)

        # Low band: one-pole lowpass: low_s[i] = low_s[i-1] + lf * (x[i] - low_s[i-1])
        # Equivalent IIR form: low_s[i] = (1-lf) * low_s[i-1] + lf * x[i]
        # _get_lfilter()(b=[lf], a=[1, -(1-lf)]) computes exactly this.

        # reuse the pre-allocated 1-element zi buffer — set [0] to the
        # current low_state, then pass to lfilter. lfilter reads but does
        # not mutate the caller's zi array.
        self._low_zi_buf[0] = self._low_state
        low_s, _ = _get_lfilter()(
            [lf],
            [1.0, -(1.0 - lf)],
            x,
            zi=self._low_zi_buf,
        )

        # High state: one-pole lowpass on the input; high band = input - state.
        self._high_zi_buf[0] = self._high_state
        high_s, _ = _get_lfilter()(
            [hf],
            [1.0, -(1.0 - hf)],
            x,
            zi=self._high_zi_buf,
        )
        high = x - high_s

        # 3-sample delay line: d3[i] = x[i-3], using the carried _delay1/2/3
        # as x[-1], x[-2], x[-3] respectively.
        # build d3 directly via slice-assignment into a pre-allocated
        # buffer instead of np.concatenate([prefix, x]) which allocated a
        # fresh (n+3)-element array per chunk just to read the first n
        # elements. d3[0..2] = (delay3, delay2, delay1); d3[3..n] = x[0..n-3].
        # For n < 3 (rare; only at startup / end-of-stream), fall back to
        # the original concatenate path — it's correct and cheap at small n.
        # ``extended`` is (re)built on the ``n < 3`` path in the ``else``
        # below; the default keeps pyrefly's definite-assignment analysis
        # happy for the delay-carry block that reads ``extended[-1..-3]``
        # under the same ``n < 3`` branch.
        extended = x
        if n >= 3:
            if self._delay_buf is None or self._delay_buf.shape[0] < n:
                cap = max(n, 1024)
                self._delay_buf = np.empty(cap, dtype=np.float64)
            d3 = self._delay_buf[:n]
            d3[0] = self._delay3
            d3[1] = self._delay2
            d3[2] = self._delay1
            d3[3:] = x[:-3]
        else:
            prefix = np.array([self._delay3, self._delay2, self._delay1], dtype=np.float64)
            extended = np.concatenate([prefix, x])
            d3 = extended[:n]

        mid = d3 - (low_s + high)

        # removed the `* 0.5` factor -- at unity gain (low_db=mid_db=high_db=0),
        # low_gain=mid_gain=high_gain=1.0 and low_s + mid + high = d3 (3-sample
        # delayed input), so the old `* 0.5` caused -6.02 dB attenuation at unity.
        #
        # Band sum computed in-place via the pre-allocated output + temp
        # buffers: ``output_buf = low_s * low_gain``; ``tmp = mid * mid_gain``;
        # ``output_buf += tmp``; repeat for high band. The addition order
        # matches the original ``(low_s*low_gain) + (mid*mid_gain) +
        # (high*high_gain)`` left-to-right evaluation so IEEE-754 rounding
        # is identical (verified byte-for-byte by
        # ``test_audio_filters_lazy_imports.TestEqualizerByteIdentical``).
        output_f64 = self._output_buf[:n]
        tmp = self._tmp_buf[:n]
        np.multiply(low_s, low_gain, out=output_f64)
        np.multiply(mid, mid_gain, out=tmp)
        np.add(output_f64, tmp, out=output_f64)
        np.multiply(high, high_gain, out=tmp)
        np.add(output_f64, tmp, out=output_f64)
        output = output_f64.astype(np.float32)

        # Carry the last 3 input samples + final band states to the next chunk.
        # when n >= 3, the last 3 samples of x are the new delay
        # values (no need to index into a concatenated `extended` array).
        if n >= 3:
            self._delay1 = float(x[-1])
            self._delay2 = float(x[-2])
            self._delay3 = float(x[-3])
        else:
            self._delay1 = float(extended[-1])
            self._delay2 = float(extended[-2])
            self._delay3 = float(extended[-3])
        self._low_state = float(low_s[-1])
        self._high_state = float(high_s[-1])

        return output.reshape(original_shape)

    def reset(self) -> None:
        self._delay1 = 0.0
        self._delay2 = 0.0
        self._delay3 = 0.0
        self._low_state = ANTIDENORMAL_EPSILON
        self._high_state = 0.0
        # zero the pre-allocated delay-line working buffer so the
        # last chunk's raw input samples (which the 3-sample delay line
        # copies verbatim into ``_delay_buf[3:]``) do not linger in
        # process memory until the numpy allocator reuses the block.
        # Guarded for None because ``_delay_buf`` is lazy-allocated on
        # the first ``process()`` call (and only when ``n >= 3``).
        if self._delay_buf is not None:
            self._delay_buf.fill(0)
        # zero the float64 input-copy + band-sum + temp buffers for the
        # same privacy rationale. Guarded for None because they are
        # lazy-allocated on the first ``process()`` call.
        for buf in (self._x_f64_buf, self._output_buf, self._tmp_buf):
            if buf is not None:
                buf.fill(0)
        # the 1-element zi buffers hold only the carried band state
        # (a single float, already reset above) — zero for consistency.
        self._low_zi_buf.fill(0)
        self._high_zi_buf.fill(0)

    @property
    def latency_ms(self) -> float:
        return 3.0 * 1000.0 / self._sample_rate
