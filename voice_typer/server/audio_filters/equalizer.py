"""Equalizer filter."""

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
    """3-band equalizer with Linkwitz-Riley-style crossovers."""

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
        self._delay_buf: np.ndarray | None = None
        # pre-allocated 1-element float64 zi state buffers for the two
        self._low_zi_buf = np.zeros(1, dtype=np.float64)
        self._high_zi_buf = np.zeros(1, dtype=np.float64)
        # pre-allocated float64 working copy of the input + float64 band-
        self._x_f64_buf: np.ndarray | None = None
        self._output_buf: np.ndarray | None = None
        self._tmp_buf: np.ndarray | None = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the EQ crossover coefficients (``_lf``,
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
        if self._x_f64_buf is None or self._x_f64_buf.shape[0] < n:
            cap = max(n, 1024)
            self._x_f64_buf = np.empty(cap, dtype=np.float64)
            self._output_buf = np.empty(cap, dtype=np.float64)
            self._tmp_buf = np.empty(cap, dtype=np.float64)
        x = self._x_f64_buf[:n]
        np.copyto(x, samples, casting="same_kind")

        # Low band: one-pole lowpass: low_s[i] = low_s[i-1] + lf * (x[i] - low_s[i-1])

        # reuse the pre-allocated 1-element zi buffer. Set [0] to the
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
        # ``_ensure_buffers`` guarantees the lazily-allocated buffers exist.
        assert self._output_buf is not None
        assert self._tmp_buf is not None
        output_f64 = self._output_buf[:n]
        tmp = self._tmp_buf[:n]
        np.multiply(low_s, low_gain, out=output_f64)
        np.multiply(mid, mid_gain, out=tmp)
        np.add(output_f64, tmp, out=output_f64)
        np.multiply(high, high_gain, out=tmp)
        np.add(output_f64, tmp, out=output_f64)
        output = output_f64.astype(np.float32)

        # Carry the last 3 input samples + final band states to the next chunk.
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
        if self._delay_buf is not None:
            self._delay_buf.fill(0)
        # zero the float64 input-copy + band-sum + temp buffers for the
        for buf in (self._x_f64_buf, self._output_buf, self._tmp_buf):
            if buf is not None:
                buf.fill(0)
        # the 1-element zi buffers hold only the carried band state
        self._low_zi_buf.fill(0)
        self._high_zi_buf.fill(0)

    @property
    def latency_ms(self) -> float:
        return 3.0 * 1000.0 / self._sample_rate
