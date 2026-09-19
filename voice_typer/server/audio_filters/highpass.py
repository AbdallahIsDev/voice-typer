"""High-pass filter."""

from __future__ import annotations

import logging

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import (  # noqa: E402
    ANTIDENORMAL_EPSILON,
    AudioFilter,
    _get_sosfilt,
)

log = logging.getLogger(__name__)


class HighPassFilter(AudioFilter):
    """Butterworth high-pass filter (scipy SOS IIR, order 4)."""

    def __init__(self, cutoff_hz: float = 80.0, sample_rate: int = WHISPER_SAMPLE_RATE) -> None:
        self.name = f"HighPass({cutoff_hz:.0f}Hz)"
        self._cutoff_hz = float(cutoff_hz)
        self._sample_rate = int(sample_rate)
        # (sos coefficients, running zi state).
        self._state: tuple[np.ndarray, np.ndarray] | None = None
        self._init_filter()

    def _init_filter(self) -> None:
        try:
            from scipy.signal import butter
        except ImportError:
            log.warning("[HIGHPASS] scipy not available, filter disabled")
            self._state = None
            return

        nyq = self._sample_rate / 2.0
        cutoff = min(max(self._cutoff_hz, 20.0), nyq * 0.99)
        try:
            from scipy.signal import butter, sos2zpk

            # Order 4 for steeper rolloff (24 dB/octave), SOS form for
            sos = butter(4, cutoff / nyq, btype="high", output="sos")
            sos = np.asarray(sos, dtype=np.float32)
            # Design-time stability gate: the float32 cast above (which
            _zeros, poles, _gain = sos2zpk(sos)
            max_pole = float(np.max(np.abs(poles))) if len(poles) else 0.0
            if not max_pole < 1.0:
                raise ValueError(
                    f"unstable high-pass design: max|pole|={max_pole:.6f} >= 1 "
                    f"(cutoff={cutoff:.1f} Hz, sr={self._sample_rate}, order=4, float32 SOS)"
                )
            # Start from silence: all-zero IIR memory, exactly like the
            zi = np.zeros((sos.shape[0], 2), dtype=np.float32)
            # Anti-denormal: add epsilon to first state element.
            zi[0, 0] = zi.dtype.type(ANTIDENORMAL_EPSILON)
            self._state = (sos, zi)
            log.debug("[HIGHPASS] ready: cutoff=%.0f Hz, sr=%d, order=4 (SOS)", cutoff, self._sample_rate)
        except Exception as exc:
            log.warning("[HIGHPASS] init failed: %s", exc)
            self._state = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the IIR coefficients were designed at
        assert sample_rate == self._sample_rate, (
            f"{type(self).__name__} built at {self._sample_rate} Hz, called with {sample_rate} Hz"
        )
        if self._state is None or audio.size == 0:
            return audio

        sos, zi = self._state
        original_shape = audio.shape
        # process in float32 (coefficients are float32 from init).
        flat = np.ravel(audio).astype(np.float32, copy=False)
        filtered, zi = _get_sosfilt()(sos, flat, zi=zi)
        self._state = (sos, zi)
        return filtered.reshape(original_shape)

    def reset(self) -> None:
        if self._state is not None:
            sos, zi = self._state
            # zero the existing IIR state in place so carry-over
            if zi.size > 0:
                zi.fill(0)
            # Anti-denormal: re-apply epsilon to the first state element.
            if zi.size > 0:
                zi[0, 0] = zi.dtype.type(ANTIDENORMAL_EPSILON)
            self._state = (sos, zi)

    @property
    def is_degraded(self) -> bool:
        return self._state is None
