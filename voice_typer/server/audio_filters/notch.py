"""Notch filter for 50/60Hz mains hum removal."""

from __future__ import annotations

import logging

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import ANTIDENORMAL_EPSILON, AudioFilter, _get_lfilter  # noqa: E402

log = logging.getLogger(__name__)


class NotchFilter(AudioFilter):
    """Narrow notch filter to remove electrical mains hum (50 or 60 Hz).

    Uses scipy's ``iirnotch`` (2nd-order IIR). Surgically removes a
    single frequency without affecting speech (50/60Hz is below the
    speech range). Quality factor Q=30 gives a narrow ~3Hz notch.
    """

    def __init__(
        self,
        frequency_hz: float = 0.0,
        sample_rate: int = WHISPER_SAMPLE_RATE,
    ) -> None:
        # 0.0 means auto-detect: 50 for EU/Asia, 60 for Americas.
        if frequency_hz <= 0.0:
            frequency_hz = self._auto_detect_frequency()
        self.name = f"Notch({frequency_hz:.0f}Hz)"
        self._frequency_hz = float(frequency_hz)
        self._sample_rate = int(sample_rate)
        self._state: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self._init_filter()

    @staticmethod
    def _auto_detect_frequency() -> float:
        """Auto-detect mains frequency. Returns 60.0 (Americas default).

        A future enhancement could check locale/timezone to pick 50 vs 60.
        For now, default to 60 (most Lausu users are in the Americas).
        Users in EU/Asia can set the frequency explicitly.
        """
        return 60.0

    def _init_filter(self) -> None:
        try:
            from scipy.signal import iirnotch
        except ImportError:
            log.warning("[NOTCH] scipy not available, filter disabled")
            self._state = None
            return

        nyq = self._sample_rate / 2.0
        freq = min(max(self._frequency_hz, 1.0), nyq * 0.99)
        try:
            w0 = freq / nyq
            q = 30.0  # narrow notch (~3Hz wide)
            b, a = iirnotch(w0, q)
            # cast to float32 once at init (see highpass.py for rationale)
            b = b.astype(np.float32)
            a = a.astype(np.float32)
            zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float32)
            self._state = (b, a, zi)
            log.debug("[NOTCH] ready: freq=%.0f Hz, Q=30, sr=%d", freq, self._sample_rate)
        except Exception as exc:
            log.warning("[NOTCH] init failed: %s", exc)
            self._state = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the notch IIR coefficients were designed
        assert sample_rate == self._sample_rate, (
            f"{type(self).__name__} built at {self._sample_rate} Hz, called with {sample_rate} Hz"
        )
        if self._state is None or audio.size == 0:
            return audio

        b, a, zi = self._state
        original_shape = audio.shape
        # process in float32 (coefficients are float32 from init)
        flat = np.ravel(audio).astype(np.float32, copy=False)
        filtered, zi = _get_lfilter()(b, a, flat, zi=zi)
        self._state = (b, a, zi)
        return filtered.reshape(original_shape)

    def reset(self) -> None:
        if self._state is not None:
            b, a, zi = self._state
            # zero the existing IIR state in place (mirrors
            if zi.size > 0:
                zi.fill(0)
            # ANTIDENORMAL (Round 0 forward-port): re-apply epsilon to
            if zi.size > 0:
                zi[0] = ANTIDENORMAL_EPSILON
            self._state = (b, a, zi)

    @property
    def is_degraded(self) -> bool:
        return self._state is None

    @property
    def degraded_reason(self) -> str:
        return "scipy not available" if self._state is None else ""
