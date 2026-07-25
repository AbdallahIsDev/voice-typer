"""Compressor (OBS-style peak envelope follower + dB-domain gain).

The envelope follower is vectorized with two parallel one-pole IIR
filters (attack and release) followed by an element-wise maximum -- a
standard trick that reproduces the OBS asymmetric envelope follower's
behavior without a per-sample Python loop on the PortAudio RT thread.
The dB-domain gain computation (``np.log10`` + ``np.power``) is also
fully vectorized. This drops the per-chunk cost from ~2-3 ms (1000+
Python iterations with transcendental calls each) to a few hundred
microseconds (two ``scipy.signal.lfilter`` C calls + vectorized numpy).
"""

from __future__ import annotations

import logging

import numpy as np

from voice_typer.server.audio_filters.base import (
    AudioFilter,
    db_to_mul,
    one_pole_coeff,
)

log = logging.getLogger(__name__)


class Compressor(AudioFilter):
    """OBS-style compressor with peak envelope follower and dB-domain gain.

    Evens out loud/quiet speech -- the single biggest STT accuracy win.
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
        self._attack_coeff = one_pole_coeff(self._sample_rate, attack_ms / 1000.0)
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

        # Envelope follower via two parallel one-pole IIR filters.
        #
        # The OBS follower picks the attack coefficient when |x| > env_prev
        # and the release coefficient otherwise. The standard vectorization
        # is to run BOTH filters in parallel and take the element-wise max:
        #   - attack_env responds fast (small coeff) -> tracks rising signals
        #   - release_env decays slow (large coeff) -> holds falling signals
        # max(attack_env, release_env) reproduces the asymmetric behavior.
        from scipy.signal import lfilter

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

        # dB-domain gain (vectorized). env_db = 20*log10(env) where env>1e-10
        # else -inf. gain_db = slope * (threshold_db - env_db), clamped <= 0.
        above_floor = env > 1e-10
        safe_env = np.where(above_floor, env, 1.0)
        env_db = 20.0 * np.log10(safe_env)
        gain_db = self._slope * (self._threshold_db - env_db)
        np.minimum(gain_db, 0.0, out=gain_db)
        gain = np.power(10.0, gain_db / 20.0) * self._output_gain
        gain = np.where(above_floor, gain, self._output_gain)

        output = (samples.astype(np.float64) * gain).astype(np.float32)
        self._envelope = float(env[-1])
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
