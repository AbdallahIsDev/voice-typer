"""Limiter (brick-wall, OBS-style)."""

from __future__ import annotations

import logging

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import (  # noqa: E402
    AudioFilter,
    _get_lfilter,
    one_pole_coeff,
)

log = logging.getLogger(__name__)

_ATTACK_TIME_SECONDS: float = 0.001  # 1 ms (matches OBS limiter-filter.c)


class Limiter(AudioFilter):
    """Brick-wall limiter (OBS-style)."""

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
        # pre-allocate the b/a coefficient arrays and the zi
        self._attack_b = np.array([1.0 - self._attack_coeff], dtype=np.float64)
        self._attack_a = np.array([1.0, -self._attack_coeff], dtype=np.float64)
        self._release_b = np.array([1.0 - self._release_coeff], dtype=np.float64)
        self._release_a = np.array([1.0, -self._release_coeff], dtype=np.float64)
        self._zi_buf = np.zeros(1, dtype=np.float64)
        # pre-allocated float64 working buffer for the
        self._env_db_buf: np.ndarray | None = None
        # pre-allocated float64 gain buffer + float64/float32 output
        self._gain_buf: np.ndarray | None = None
        self._output_f64_buf: np.ndarray | None = None
        self._output_f32_buf: np.ndarray | None = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the envelope-follower coefficients
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

        abs_x = np.abs(samples).astype(np.float64)

        # reuse the pre-allocated zi buffer. Set the initial
        self._zi_buf[0] = self._envelope
        attack_env, _ = _get_lfilter()(
            self._attack_b,
            self._attack_a,
            abs_x,
            zi=self._zi_buf,
        )
        self._zi_buf[0] = self._envelope
        release_env, _ = _get_lfilter()(
            self._release_b,
            self._release_a,
            abs_x,
            zi=self._zi_buf,
        )
        # in-place element-wise maximum into attack_env (avoids
        env = np.maximum(attack_env, release_env, out=attack_env)

        above_floor = env > 1e-10
        # reuse a pre-allocated buffer for the safe_env / env_db
        if self._env_db_buf is None or self._env_db_buf.shape[0] < n:
            cap = max(n, 1024)
            self._env_db_buf = np.empty(cap, dtype=np.float64)
        env_db = self._env_db_buf[:n]
        # safe_env = where(above_floor, env, 1.0), np.where has no out=
        np.copyto(env_db, env, where=above_floor)
        env_db[~above_floor] = 1.0
        np.log10(env_db, out=env_db)
        env_db *= 20.0  # 20 * log10(safe_env)
        # gain_db = slope * (threshold_db - env_db) in-place.
        env_db *= -self._slope
        env_db += self._slope * self._threshold_db
        np.minimum(env_db, 0.0, out=env_db)
        gain_db = env_db
        # gain = np.power(10.0, gain_db / 20.0), computed in-place into
        if self._gain_buf is None or self._gain_buf.shape[0] < n:
            cap = max(n, 1024)
            self._gain_buf = np.empty(cap, dtype=np.float64)
            self._output_f64_buf = np.empty(cap, dtype=np.float64)
            self._output_f32_buf = np.empty(cap, dtype=np.float32)
        gain = self._gain_buf[:n]
        np.divide(gain_db, 20.0, out=gain)
        np.power(10.0, gain, out=gain)
        # np.where(above_floor, gain, 1.0), np.where has no out= kwarg
        np.copyto(gain, 1.0, where=~above_floor)

        # output = (samples.astype(float64) * gain).astype(float32),
        output_f64 = self._output_f64_buf[:n]
        np.multiply(samples, gain, out=output_f64, casting="same_kind")
        output = self._output_f32_buf[:n]
        np.copyto(output, output_f64, casting="same_kind")
        self._envelope = float(env[-1])
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
        # zero the pre-allocated dB-domain working buffer so the
        if self._env_db_buf is not None:
            self._env_db_buf.fill(0)
        # zero the gain + output buffers for the same privacy rationale
        for buf in (self._gain_buf, self._output_f64_buf, self._output_f32_buf):
            if buf is not None:
                buf.fill(0)
