"""Compressor (OBS-style peak envelope follower + dB-domain gain)."""

from __future__ import annotations

import logging

import numpy as np

from voice_typer.server.audio_filters.base import (
    AudioFilter,
    db_to_mul,
    mul_to_db,
    one_pole_coeff,
)

log = logging.getLogger(__name__)


class Compressor(AudioFilter):
    """OBS-style compressor with peak envelope follower and dB-domain gain.

    Evens out loud/quiet speech — the single biggest STT accuracy win.
    When the signal exceeds the threshold, gain is reduced by the ratio.
    Attack/release use the OBS one-pole smoother.

    Algorithm (ported from OBS ``compressor-filter.c``):
    1. Envelope follower: ``env = sample + coeff * (env - sample)``
       (attack if rising, release if falling).
    2. Gain in dB: ``gain_db = slope * (threshold_db - env_db)``, clamped <= 0.
       where ``slope = 1 - 1/ratio``.
    3. Output: ``sample *= db_to_mul(gain_db) * output_gain``.
    """

    def __init__(
        self,
        threshold_db: float = -18.0,
        ratio: float = 3.0,
        attack_ms: float = 6.0,
        release_ms: float = 60.0,
        output_gain_db: float = 0.0,
        sample_rate: int = 16000,
    ) -> None:
        self.name = f"Compressor({ratio:.0f}:1,{threshold_db:.0f}dB)"
        self._threshold_db = float(threshold_db)
        self._ratio = max(float(ratio), 1.0)
        self._slope = 1.0 - (1.0 / self._ratio)
        self._output_gain = db_to_mul(output_gain_db)
        self._sample_rate = int(sample_rate)

        # Attack/release coefficients (OBS one-pole smoother)
        self._attack_coeff = one_pole_coeff(self._sample_rate, attack_ms / 1000.0)
        self._release_coeff = one_pole_coeff(self._sample_rate, release_ms / 1000.0)

        # Envelope state
        self._envelope: float = 0.0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if audio.size == 0:
            return audio

        original_shape = audio.shape
        samples = np.ravel(audio).astype(np.float32, copy=False)
        n = len(samples)

        threshold_db = self._threshold_db
        slope = self._slope
        output_gain = self._output_gain
        attack_c = self._attack_coeff
        release_c = self._release_coeff
        env = self._envelope

        output = np.empty(n, dtype=np.float32)

        for i in range(n):
            s = float(samples[i])
            abs_s = abs(s)

            # Envelope follower (attack if rising, release if falling)
            if abs_s > env:
                env = attack_c * env + (1.0 - attack_c) * abs_s
            else:
                env = release_c * env + (1.0 - release_c) * abs_s

            # Compute gain in dB domain
            if env > 1e-10:
                env_db = mul_to_db(env)
                gain_db = slope * (threshold_db - env_db)
                if gain_db > 0.0:
                    gain_db = 0.0  # compressor never boosts
                gain = db_to_mul(gain_db) * output_gain
            else:
                gain = output_gain

            output[i] = s * gain

        self._envelope = env
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
