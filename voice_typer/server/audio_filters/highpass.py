"""High-pass Butterworth IIR filter."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from voice_typer.server.audio_filters.base import AudioFilter, ANTIDENORMAL_EPSILON

log = logging.getLogger(__name__)


class HighPassFilter(AudioFilter):
    """Butterworth high-pass filter (scipy IIR, order 4).

    Removes low-frequency rumble (HVAC, traffic, fan noise, proximity
    effect) below the cutoff frequency. Order 4 gives a 24 dB/octave
    rolloff — steeper than the previous order-2 design.

    Stateful: the IIR ``zi`` state carries across ``process()`` calls
    for click-free continuity. Anti-denormal epsilon prevents CPU-killing
    denormal floats on some CPUs.
    """

    def __init__(self, cutoff_hz: float = 80.0, sample_rate: int = 16000) -> None:
        self.name = f"HighPass({cutoff_hz:.0f}Hz)"
        self._cutoff_hz = float(cutoff_hz)
        self._sample_rate = int(sample_rate)
        self._state: Optional[tuple[np.ndarray, np.ndarray, np.ndarray]] = None
        self._init_filter()

    def _init_filter(self) -> None:
        try:
            from scipy.signal import butter
        except ImportError:
            log.warning("[HIGHPASS] scipy not available — filter disabled")
            self._state = None
            return

        nyq = self._sample_rate / 2.0
        cutoff = min(max(self._cutoff_hz, 20.0), nyq * 0.99)
        try:
            # Order 4 for steeper rolloff (24 dB/octave).
            b, a = butter(4, cutoff / nyq, btype="high")
            zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float64)
            # Anti-denormal: add epsilon to first state element.
            zi[0] = ANTIDENORMAL_EPSILON
            self._state = (b, a, zi)
            log.debug("[HIGHPASS] ready: cutoff=%.0f Hz, sr=%d, order=4", cutoff, self._sample_rate)
        except Exception as exc:
            log.warning("[HIGHPASS] init failed: %s", exc)
            self._state = None

    def process(self, audio: np.ndarray, sample_rate: int) -> Optional[np.ndarray]:
        if self._state is None or audio.size == 0:
            return audio
        from scipy.signal import lfilter

        b, a, zi = self._state
        original_shape = audio.shape
        flat = np.ravel(audio).astype(np.float64, copy=False)
        filtered, zi = lfilter(b, a, flat, zi=zi)
        self._state = (b, a, zi)
        return filtered.astype(np.float32, copy=False).reshape(original_shape)

    def reset(self) -> None:
        if self._state is not None:
            b, a, _ = self._state
            zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float64)
            zi[0] = ANTIDENORMAL_EPSILON
            self._state = (b, a, zi)

    @property
    def is_degraded(self) -> bool:
        return self._state is None

    @property
    def degraded_reason(self) -> str:
        return "scipy not available" if self._state is None else ""
