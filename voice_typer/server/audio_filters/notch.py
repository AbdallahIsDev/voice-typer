"""Notch filter for 50/60Hz mains hum removal."""

from __future__ import annotations

import logging

import numpy as np

from voice_typer.server.audio_filters.base import ANTIDENORMAL_EPSILON, AudioFilter

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
        sample_rate: int = 16000,
    ) -> None:
        # 0.0 means auto-detect: 50 for EU/Asia, 60 for Americas.
        # For simplicity, default to 60 (Americas) when auto is requested.
        # A future enhancement could use locale to pick.
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
        For now, default to 60 (most Voice Typer users are in the Americas).
        Users in EU/Asia can set the frequency explicitly.
        """
        return 60.0

    def _init_filter(self) -> None:
        try:
            from scipy.signal import iirnotch
        except ImportError:
            log.warning("[NOTCH] scipy not available — filter disabled")
            self._state = None
            return

        nyq = self._sample_rate / 2.0
        freq = min(max(self._frequency_hz, 1.0), nyq * 0.99)
        try:
            w0 = freq / nyq
            q = 30.0  # narrow notch (~3Hz wide)
            b, a = iirnotch(w0, q)
            # PVT-011: cast to float32 once at init (see highpass.py for rationale)
            b = b.astype(np.float32)
            a = a.astype(np.float32)
            zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float32)
            self._state = (b, a, zi)
            log.debug("[NOTCH] ready: freq=%.0f Hz, Q=30, sr=%d", freq, self._sample_rate)
        except Exception as exc:
            log.warning("[NOTCH] init failed: %s", exc)
            self._state = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        if self._state is None or audio.size == 0:
            return audio
        from scipy.signal import lfilter

        b, a, zi = self._state
        original_shape = audio.shape
        # PVT-011: process in float32 (coefficients are float32 from init)
        flat = np.ravel(audio).astype(np.float32, copy=False)
        filtered, zi = lfilter(b, a, flat, zi=zi)
        self._state = (b, a, zi)
        return filtered.reshape(original_shape)

    def reset(self) -> None:
        if self._state is not None:
            b, a, zi = self._state
            # G4-L-05: zero the existing IIR state in place (mirrors
            # HighPassFilter.reset).  The notch filter's carry state is
            # small (1 sample) but still encodes a residual of the
            # previous audio, so zero it for symmetry with the highpass
            # path and the same SEC-audit-008 guarantee.
            # XV-39: reuse the just-zeroed array instead of allocating a
            # fresh ``np.zeros(...)`` block on every reset() — same
            # rationale as HighPassFilter.reset.
            if zi.size > 0:
                zi.fill(0)
            # ANTIDENORMAL (Round 0 forward-port): re-apply epsilon to
            # the first state element (mirrors HighPassFilter.reset).
            # Without this, reset() leaves the IIR state at exact zero,
            # which on some CPUs triggers denormal float handling and
            # burns cycles in the audio callback.
            if zi.size > 0:
                zi[0] = ANTIDENORMAL_EPSILON
            self._state = (b, a, zi)

    @property
    def is_degraded(self) -> bool:
        return self._state is None

    @property
    def degraded_reason(self) -> str:
        return "scipy not available" if self._state is None else ""
