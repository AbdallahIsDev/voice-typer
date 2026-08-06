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

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import (  # noqa: E402
    AudioFilter,
    _get_lfilter,
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
        sample_rate: int = WHISPER_SAMPLE_RATE,
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
        # pre-allocate the b/a coefficient arrays and the zi
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
        # pre-allocated float64 working buffer for the
        # safe_env -> env_db -> gain_db pipeline. Lazy-resized to the
        # largest chunk seen so the first call allocates and subsequent
        # calls reuse. Eliminates 3 fresh array allocations per chunk.
        self._env_db_buf: np.ndarray | None = None
        # pre-allocated float64 gain buffer + float64/float32 output
        # buffers for the final gain stage. Before this, the gain stage
        # allocated ~7 fresh arrays per chunk (``gain_db / 20.0``,
        # ``np.power(...)``, ``* output_gain``, ``np.where(...)``,
        # ``samples.astype(float64)``, ``* gain``, ``.astype(float32)``).
        # Now computed in-place: ``_gain_buf = gain_db / 20``;
        # ``np.power(10, _gain_buf, out=_gain_buf)``;
        # ``_gain_buf *= output_gain``; ``np.copyto(_gain_buf,
        # output_gain, where=~above_floor)`` (replaces ``np.where``);
        # ``np.multiply(samples, _gain_buf, out=_output_f64_buf,
        # casting='same_kind')``; ``np.copyto(_output_f32_buf,
        # _output_f64_buf, casting='same_kind')``. Lazy-resized to the
        # largest chunk seen (mirror ``_env_db_buf``).
        self._gain_buf: np.ndarray | None = None
        self._output_f64_buf: np.ndarray | None = None
        self._output_f32_buf: np.ndarray | None = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the envelope-follower coefficients
        # (``_attack_coeff`` / ``_release_coeff``) are derived from
        # ``self._sample_rate``; feeding audio at a different rate
        # shifts the attack/release ballistics (a 6 ms attack built
        # at 16 kHz actually responds in 2 ms when fed 48 kHz audio).
        # Python strips this assert under ``-O``; in debug builds a
        # mismatch surfaces as an ``AssertionError``.
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

        # Envelope follower via two parallel one-pole IIR filters.
        #
        # The OBS follower picks the attack coefficient when |x| > env_prev
        # and the release coefficient otherwise. The standard vectorization
        # is to run BOTH filters in parallel and take the element-wise max:
        #   - attack_env responds fast (small coeff) -> tracks rising signals
        #   - release_env decays slow (large coeff) -> holds falling signals
        # max(attack_env, release_env) reproduces the asymmetric behavior.
        #
        # reuse the pre-allocated zi buffer — set the initial
        # state to the current envelope, then pass the buffer to
        # lfilter. lfilter reads but does not mutate the caller's zi
        # array (it returns the final state as a new array).
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
        env = np.maximum(attack_env, release_env, out=attack_env)

        # dB-domain gain (vectorized). env_db = 20*log10(env) where env>1e-10
        # else -inf. gain_db = slope * (threshold_db - env_db), clamped <= 0.
        above_floor = env > 1e-10
        # reuse a pre-allocated buffer for the safe_env / env_db
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
        # gain = np.power(10.0, gain_db / 20.0) * output_gain, computed
        # in-place into the pre-allocated ``_gain_buf``. Replaces 3 fresh
        # allocations (divide, power, scalar multiply) with in-place ufuncs.
        if self._gain_buf is None or self._gain_buf.shape[0] < n:
            cap = max(n, 1024)
            self._gain_buf = np.empty(cap, dtype=np.float64)
            self._output_f64_buf = np.empty(cap, dtype=np.float64)
            self._output_f32_buf = np.empty(cap, dtype=np.float32)
        gain = self._gain_buf[:n]
        np.divide(gain_db, 20.0, out=gain)
        np.power(10.0, gain, out=gain)
        gain *= self._output_gain
        # np.where(above_floor, gain, output_gain) — np.where has no out=
        # kwarg and allocates a fresh array. ``np.copyto`` with a ``where=``
        # mask overwrites the below-floor slots in-place, producing the
        # same result without the allocation. Above-floor slots retain the
        # computed gain; below-floor slots are set to ``output_gain``.
        np.copyto(gain, self._output_gain, where=~above_floor)

        # output = (samples.astype(float64) * gain).astype(float32),
        # computed in-place via the pre-allocated f64 + f32 buffers.
        # ``np.multiply(float32, float64, out=float64, casting='same_kind')``
        # promotes the float32 input to float64 internally (exact upcast)
        # and writes the float64 product into ``_output_f64_buf``. Then
        # ``np.copyto(float32, float64, casting='same_kind')`` rounds the
        # float64 product to float32 (IEEE-754 round-to-nearest-even, same
        # as ``.astype(np.float32)``).
        output_f64 = self._output_f64_buf[:n]
        np.multiply(samples, gain, out=output_f64, casting="same_kind")
        output = self._output_f32_buf[:n]
        np.copyto(output, output_f64, casting="same_kind")
        self._envelope = float(env[-1])
        return output.reshape(original_shape)

    def reset(self) -> None:
        self._envelope = 0.0
        # zero the pre-allocated dB-domain working buffer so the
        # last chunk's envelope samples do not linger in process memory
        # until the numpy allocator reuses the block. Guarded for None
        # because ``_env_db_buf`` is lazy-allocated on the first
        # ``process()`` call.
        if self._env_db_buf is not None:
            self._env_db_buf.fill(0)
        # zero the gain + output buffers for the same privacy rationale.
        # ``_gain_buf`` holds the per-sample gain (derived from the envelope
        # of the user's voice); ``_output_f64_buf`` / ``_output_f32_buf``
        # hold the filtered audio output. Guarded for None because they
        # are lazy-allocated on the first ``process()`` call.
        for buf in (self._gain_buf, self._output_f64_buf, self._output_f32_buf):
            if buf is not None:
                buf.fill(0)
