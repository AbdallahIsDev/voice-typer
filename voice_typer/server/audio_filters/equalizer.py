"""3-band equalizer (OBS-style crossover)."""

from __future__ import annotations

import logging
import math

import numpy as np

from voice_typer.server.audio_filters.base import ANTIDENORMAL_EPSILON, AudioFilter, db_to_mul

log = logging.getLogger(__name__)

# Crossover frequencies (matches OBS eq-filter.c)
LOW_FREQ: float = 800.0
HIGH_FREQ: float = 5000.0


class Equalizer(AudioFilter):
    """3-band equalizer with Linkwitz-Riley-style crossovers.

    Splits audio into Low (<800Hz), Mid (800Hz–5kHz), High (>5kHz) bands
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
        sample_rate: int = 16000,
    ) -> None:
        self.name = f"EQ({low_db:+.0f}/{mid_db:+.0f}/{high_db:+.0f}dB)"
        self._low_gain = db_to_mul(low_db)
        self._mid_gain = db_to_mul(mid_db)
        self._high_gain = db_to_mul(high_db)
        self._sample_rate = int(sample_rate)

        # One-pole filter coefficients (OBS: lf = 2*sin(pi*freq/sr))
        self._lf = 2.0 * math.sin(math.pi * LOW_FREQ / self._sample_rate)
        self._hf = 2.0 * math.sin(math.pi * HIGH_FREQ / self._sample_rate)

        # State: [delay1, delay2, delay3, low_state, high_state]
        # delay1/2/3 are the 3-sample delay line for phase alignment.
        # low_state/high_state are the one-pole filter states.
        self._delay1: float = 0.0
        self._delay2: float = 0.0
        self._delay3: float = 0.0
        self._low_state: float = ANTIDENORMAL_EPSILON
        self._high_state: float = 0.0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)

        lf = self._lf
        hf = self._hf
        low_gain = self._low_gain
        mid_gain = self._mid_gain
        high_gain = self._high_gain

        d1 = self._delay1
        d2 = self._delay2
        d3 = self._delay3
        low_s = self._low_state
        high_s = self._high_state

        output = np.empty(n, dtype=np.float32)

        for i in range(n):
            s = float(samples[i])

            # Low band: cascaded one-pole lowpass
            low_s = low_s + lf * (s - low_s)

            # High band: cascaded one-pole highpass
            high_s = high_s + hf * (s - high_s)
            high = s - high_s

            # Mid band: what's left after removing low and high
            # (with 3-sample delay for phase alignment)
            mid = d3 - (low_s + high)

            # Delay line
            d3 = d2
            d2 = d1
            d1 = s

            # ER-9: removed the `* 0.5` factor — at unity gain (low_db=mid_db=high_db=0),
        # low_gain=mid_gain=high_gain=1.0 and low_s + mid + high = d3 (3-sample
        # delayed input), so the old `* 0.5` caused -6.02 dB attenuation at unity.
        output[i] = low_s * low_gain + mid * mid_gain + high * high_gain

        self._delay1 = d1
        self._delay2 = d2
        self._delay3 = d3
        self._low_state = low_s
        self._high_state = high_s

        return output.reshape(original_shape)

    def reset(self) -> None:
        self._delay1 = 0.0
        self._delay2 = 0.0
        self._delay3 = 0.0
        self._low_state = ANTIDENORMAL_EPSILON
        self._high_state = 0.0

    @property
    def latency_ms(self) -> float:
        # 3 samples delay
        return 3.0 * 1000.0 / self._sample_rate
