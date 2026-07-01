"""Noise gate / downward expander (OBS-style)."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from voice_typer.server.audio_filters.base import (
    AudioFilter,
    db_to_mul,
    one_pole_coeff,
)

log = logging.getLogger(__name__)


class NoiseGate(AudioFilter):
    """OBS-style noise gate with peak-hold level estimator and state machine.

    Unlike a hard gate (which snaps to silence), this is a smooth
    downward expander: below the close threshold, gain is reduced with
    attack/release smoothing. This preserves speech tails and avoids
    audible chopping.

    Algorithm (ported from OBS ``noise-gate-filter.c``):
    1. Compute per-sample peak level: ``level = max(level, |sample|) - decay_rate``
    2. State machine:
       - ``level > open_threshold`` → gate opens
       - ``level < close_threshold`` → gate closes, hold timer resets
       - Open: attenuation rises toward 1.0 at attack rate
       - Closed (after hold): attenuation falls toward 0.0 at release rate
    3. Each output sample multiplied by ``attenuation``.
    """

    def __init__(
        self,
        open_threshold_db: float = -26.0,
        close_threshold_db: float = -32.0,
        attack_ms: float = 25.0,
        hold_ms: float = 200.0,
        release_ms: float = 150.0,
        sample_rate: int = 16000,
    ) -> None:
        self.name = "NoiseGate"
        self._open_threshold = db_to_mul(open_threshold_db)
        self._close_threshold = db_to_mul(close_threshold_db)
        self._attack_ms = float(attack_ms)
        self._hold_ms = float(hold_ms)
        self._release_ms = float(release_ms)
        self._sample_rate = int(sample_rate)

        # State (carried across process() calls)
        self._is_open: bool = False
        self._attenuation: float = 0.0
        self._level: float = 0.0
        self._held_time: float = 0.0

        # Precompute decay rate: level estimator decays across the
        # open/close threshold gap in ~13ms (OBS: sample_rate/75).
        if self._open_threshold > self._close_threshold:
            self._decay_rate = (self._open_threshold - self._close_threshold) / (
                self._sample_rate / 75.0
            )
        else:
            self._decay_rate = 0.001

    def process(self, audio: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
        if audio.size == 0:
            return audio

        # Ensure 1-D float32
        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)
        dt = 1.0 / sample_rate

        attack_rate = 1.0 / max(self._attack_ms / 1000.0, dt)
        release_rate = 1.0 / max(self._release_ms / 1000.0, dt)
        hold_time = self._hold_ms / 1000.0

        output = np.empty(n, dtype=np.float32)
        level = self._level
        is_open = self._is_open
        attenuation = self._attenuation
        held_time = self._held_time
        open_thr = self._open_threshold
        close_thr = self._close_threshold
        decay = self._decay_rate

        for i in range(n):
            s = float(samples[i])
            cur_level = abs(s)

            # Peak-hold with leaky decay
            if cur_level > level:
                level = cur_level
            else:
                level -= decay
                if level < 0.0:
                    level = 0.0

            # State machine
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

            output[i] = s * attenuation

        self._level = level
        self._is_open = is_open
        self._attenuation = attenuation
        self._held_time = held_time

        return output.reshape(original_shape)

    def reset(self) -> None:
        self._is_open = False
        self._attenuation = 0.0
        self._level = 0.0
        self._held_time = 0.0
