"""Limiter (brick-wall, OBS-style)."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from voice_typer.server.audio_filters.base import (
    AudioFilter,
    db_to_mul,
    mul_to_db,
    one_pole_coeff,
)

log = logging.getLogger(__name__)

# Hardcoded attack time (matches OBS limiter-filter.c)
_ATTACK_TIME_SECONDS: float = 0.001  # 1 ms


class Limiter(AudioFilter):
    """Brick-wall limiter (OBS-style).

    A compressor with ``slope=1.0`` (infinity:1 ratio) and 1ms attack.
    Absolutely refuses to let any sample exceed the ceiling. Prevents
    transient clicks/pops from saturating downstream stages.

    Algorithm (ported from OBS ``limiter-filter.c``):
    Same as Compressor but with ``slope=1.0`` and ``attack=1ms``.
    """

    def __init__(
        self,
        ceiling_db: float = -6.0,
        release_ms: float = 60.0,
        sample_rate: int = 16000,
    ) -> None:
        self.name = f"Limiter({ceiling_db:.0f}dB)"
        self._threshold_db = float(ceiling_db)
        self._slope = 1.0  # brick-wall
        self._sample_rate = int(sample_rate)

        self._attack_coeff = one_pole_coeff(self._sample_rate, _ATTACK_TIME_SECONDS)
        self._release_coeff = one_pole_coeff(self._sample_rate, release_ms / 1000.0)
        self._envelope: float = 0.0

    def process(self, audio: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)

        threshold_db = self._threshold_db
        slope = self._slope
        attack_c = self._attack_coeff
        release_c = self._release_coeff
        env = self._envelope

        output = np.empty(n, dtype=np.float32)

        for i in range(n):
            s = float(samples[i])
            abs_s = abs(s)

            if abs_s > env:
                env = attack_c * env + (1.0 - attack_c) * abs_s
            else:
                env = release_c * env + (1.0 - release_c) * abs_s

            if env > 1e-10:
                env_db = mul_to_db(env)
                gain_db = slope * (threshold_db - env_db)
                if gain_db > 0.0:
                    gain_db = 0.0
                gain = db_to_mul(gain_db)
            else:
                gain = 1.0

            output[i] = s * gain

        self._envelope = env
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
