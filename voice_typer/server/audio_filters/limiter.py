"""Limiter (brick-wall, OBS-style).

Vectorized with the same two-parallel-IIR + max trick as the
Compressor (see ``compressor.py``). The brick-wall behavior comes
from ``slope = 1.0`` (infinity:1 ratio) so gain_db = threshold_db -
env_db, clamped <= 0 -- when env >= ceiling, gain_db = threshold_db -
env_db <= 0 and output is held at the ceiling.
"""

from __future__ import annotations

import logging

import numpy as np

# DJ-71: hoisted from per-call `from scipy.signal import lfilter`
# (was inside process() — 6 imports/chunk × 16 Hz = 96 lookups/sec on
# the audio worker thread). Module-top import under try/except so the
# module still loads when scipy is missing (tests with mock filters).
try:
    from scipy.signal import lfilter
except ImportError:  # pragma: no cover — scipy is a hard dep in prod
    lfilter = None  # type: ignore[assignment]

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE
from voice_typer.server.audio_filters.base import (
    AudioFilter,
    one_pole_coeff,
)

log = logging.getLogger(__name__)

_ATTACK_TIME_SECONDS: float = 0.001  # 1 ms (matches OBS limiter-filter.c)


class Limiter(AudioFilter):
    """Brick-wall limiter (OBS-style).

    A compressor with ``slope=1.0`` (infinity:1 ratio) and 1ms attack.
    Absolutely refuses to let any sample exceed the ceiling. Prevents
    transient clicks/pops from saturating downstream stages.
    """

    def __init__(
        self,
        ceiling_db: float = -6.0,
        release_ms: float = 60.0,
        sample_rate: int = WHISPER_SAMPLE_RATE,
    ) -> None:
        self.name = f"Limiter({ceiling_db:.0f}dB)"
        self._threshold_db = float(ceiling_db)
        self._slope = 1.0  # brick-wall
        self._sample_rate = int(sample_rate)
        self._attack_coeff = one_pole_coeff(self._sample_rate, _ATTACK_TIME_SECONDS)
        self._release_coeff = one_pole_coeff(self._sample_rate, release_ms / 1000.0)
        self._envelope: float = 0.0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)
        if n == 0:
            return audio

        abs_x = np.abs(samples).astype(np.float64)

        attack_env, _ = lfilter(
            [1.0 - self._attack_coeff],
            [1.0, -self._attack_coeff],
            abs_x,
            zi=np.array([self._envelope], dtype=np.float64),
        )
        release_env, _ = lfilter(
            [1.0 - self._release_coeff],
            [1.0, -self._release_coeff],
            abs_x,
            zi=np.array([self._envelope], dtype=np.float64),
        )
        env = np.maximum(attack_env, release_env)

        above_floor = env > 1e-10
        safe_env = np.where(above_floor, env, 1.0)
        env_db = 20.0 * np.log10(safe_env)
        gain_db = self._slope * (self._threshold_db - env_db)
        np.minimum(gain_db, 0.0, out=gain_db)
        gain = np.power(10.0, gain_db / 20.0)
        gain = np.where(above_floor, gain, 1.0)

        output = (samples.astype(np.float64) * gain).astype(np.float32)
        self._envelope = float(env[-1])
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
