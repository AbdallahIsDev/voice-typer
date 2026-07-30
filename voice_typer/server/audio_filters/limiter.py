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
        # DJ-72: pre-allocate the b/a coefficient arrays and the zi
        # state buffer in __init__ so process() does not allocate
        # fresh Python lists + 1-element ndarrays per call. The b/a
        # arrays are constant after __init__; the zi buffer is
        # overwritten with the current envelope before each lfilter
        # call (lfilter accepts zi as the initial state and does not
        # mutate the caller's array — it returns the final state as a
        # new array via the second tuple element, which we discard).
        self._attack_b = np.array([1.0 - self._attack_coeff], dtype=np.float64)
        self._attack_a = np.array([1.0, -self._attack_coeff], dtype=np.float64)
        self._release_b = np.array([1.0 - self._release_coeff], dtype=np.float64)
        self._release_a = np.array([1.0, -self._release_coeff], dtype=np.float64)
        self._zi_buf = np.zeros(1, dtype=np.float64)
        # DJ-73: pre-allocated float64 working buffer for the
        # safe_env -> env_db -> gain_db pipeline. Lazy-resized to the
        # largest chunk seen so the first call allocates and subsequent
        # calls reuse. Eliminates 3 fresh array allocations per chunk.
        self._env_db_buf: np.ndarray | None = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)
        if n == 0:
            return audio

        abs_x = np.abs(samples).astype(np.float64)

        # DJ-72: reuse the pre-allocated zi buffer — set the initial
        # state to the current envelope, then pass the buffer to
        # lfilter. lfilter reads but does not mutate the caller's zi
        # array (it returns the final state as a new array).
        self._zi_buf[0] = self._envelope
        attack_env, _ = lfilter(
            self._attack_b,
            self._attack_a,
            abs_x,
            zi=self._zi_buf,
        )
        self._zi_buf[0] = self._envelope
        release_env, _ = lfilter(
            self._release_b,
            self._release_a,
            abs_x,
            zi=self._zi_buf,
        )
        # DJ-73: in-place element-wise maximum into attack_env (avoids
        # one fresh allocation per chunk).
        env = np.maximum(attack_env, release_env, out=attack_env)

        above_floor = env > 1e-10
        # DJ-73: reuse a pre-allocated buffer for the safe_env / env_db /
        # gain_db pipeline — 3 ops collapsed into a single buffer.
        if self._env_db_buf is None or self._env_db_buf.shape[0] < n:
            cap = max(n, 1024)
            self._env_db_buf = np.empty(cap, dtype=np.float64)
        env_db = self._env_db_buf[:n]
        # safe_env = where(above_floor, env, 1.0) — np.where has no out=
        # kwarg, so use np.copyto with a where= mask + a scalar fill on
        # the below-floor slots. Avoids one fresh allocation per chunk.
        np.copyto(env_db, env, where=above_floor)
        env_db[~above_floor] = 1.0
        np.log10(env_db, out=env_db)
        env_db *= 20.0  # 20 * log10(safe_env)
        # gain_db = slope * (threshold_db - env_db) in-place.
        env_db *= -self._slope
        env_db += self._slope * self._threshold_db
        np.minimum(env_db, 0.0, out=env_db)
        gain_db = env_db
        gain = np.power(10.0, gain_db / 20.0)
        gain = np.where(above_floor, gain, 1.0)

        output = (samples.astype(np.float64) * gain).astype(np.float32)
        self._envelope = float(env[-1])
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
