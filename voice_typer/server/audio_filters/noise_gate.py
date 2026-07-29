"""Noise gate / downward expander (OBS-style).

The peak-hold level estimator is vectorized with
``np.maximum.accumulate`` (linear-decay peak-hold trick -- see comment
in ``process``). The open/close + attack/hold/release state machine
remains a Python loop because its state transitions are inherently
sequential, but it now operates on the pre-computed ``level`` array --
no per-sample ``abs()`` or peak-hold bookkeeping in the loop body.
"""

from __future__ import annotations

import logging

import numpy as np

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.audio_filters.base import (
    AudioFilter,
    db_to_mul,
)

log = logging.getLogger(__name__)


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
    ) -> None:
        self.name = "NoiseGate"
        self._open_threshold = db_to_mul(open_threshold_db)
        self._close_threshold = db_to_mul(close_threshold_db)
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

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)
        if n == 0:
            return audio
        dt = 1.0 / sample_rate

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
        abs_x = np.abs(samples).astype(np.float64)
        i_arr = np.arange(n, dtype=np.float64)
        y = abs_x + i_arr * decay
        y_with_init = np.empty(n + 1, dtype=np.float64)
        y_with_init[0] = self._level
        y_with_init[1:] = y
        z = np.maximum.accumulate(y_with_init)[1:]
        level_arr = np.maximum(z - i_arr * decay, 0.0)

        # State machine (sequential -- inherently stateful). Operates on
        # the pre-computed ``level_arr`` so the inner loop is cheap (a few
        # float comparisons + arithmetic, no abs/max calls).
        attenuation_arr = np.empty(n, dtype=np.float64)
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

        output = (samples.astype(np.float64) * attenuation_arr).astype(np.float32)

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
