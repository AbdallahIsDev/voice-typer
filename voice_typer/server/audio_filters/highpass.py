"""High-pass Butterworth IIR filter."""

from __future__ import annotations

import logging
from typing import cast

from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: E402
from voice_typer.server.audio_filters.base import ANTIDENORMAL_EPSILON, AudioFilter, _get_lfilter  # noqa: E402

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

    def __init__(self, cutoff_hz: float = 80.0, sample_rate: int = WHISPER_SAMPLE_RATE) -> None:
        self.name = f"HighPass({cutoff_hz:.0f}Hz)"
        self._cutoff_hz = float(cutoff_hz)
        self._sample_rate = int(sample_rate)
        self._state: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
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
            # scipy.signal.butter's stubs declare a union return type
            # (zpk / ba / sos) that pyrefly cannot narrow through unpacking.
            # ``cast`` is the narrowest correct fix: at runtime ``butter``
            # with the default ``output="ba"`` always returns a 2-tuple of
            # 1-D ndarrays. Using ``cast`` keeps the runtime path identical
            # while giving the static analyzer the precise shape it needs.
            result = butter(4, cutoff / nyq, btype="high")
            b, a = cast("tuple[np.ndarray, np.ndarray]", result)
            # cast coefficients + state to float32 once at init.
            # Previously kept as float64 (from butter()), which forced a
            # per-chunk astype(np.float64, copy=False) in process() —
            # ALWAYS a copy (dtype mismatch) = 128 KB/s of float64
            # allocation on the RT thread. float32 is sufficient for
            # order-4 IIR at 16 kHz (no audible precision loss).
            b = b.astype(np.float32)
            a = a.astype(np.float32)
            zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float32)
            # Anti-denormal: add epsilon to first state element.
            zi[0] = ANTIDENORMAL_EPSILON
            self._state = (b, a, zi)
            log.debug("[HIGHPASS] ready: cutoff=%.0f Hz, sr=%d, order=4", cutoff, self._sample_rate)
        except Exception as exc:
            log.warning("[HIGHPASS] init failed: %s", exc)
            self._state = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
        # Debug-only guard: the IIR coefficients were designed at
        # ``self._sample_rate`` (see ``_init_filter``); feeding audio
        # at a different rate silently mistunes the cutoff (an 80 Hz
        # high-pass built at 16 kHz actually cuts at 240 Hz when fed
        # 48 kHz audio). Python strips this assert under ``-O`` so
        # production builds pay zero cost; in debug builds a mismatch
        # surfaces as an ``AssertionError`` instead of silent mistune.
        assert sample_rate == self._sample_rate, (
            f"{type(self).__name__} built at {self._sample_rate} Hz, called with {sample_rate} Hz"
        )
        if self._state is None or audio.size == 0:
            return audio

        b, a, zi = self._state
        original_shape = audio.shape
        # process in float32 (coefficients are float32 from init).
        # No per-chunk astype upcast — saves 128 KB/s of float64 allocation.
        flat = np.ravel(audio).astype(np.float32, copy=False)
        filtered, zi = _get_lfilter()(b, a, flat, zi=zi)
        self._state = (b, a, zi)
        return filtered.reshape(original_shape)

    def reset(self) -> None:
        if self._state is not None:
            b, a, zi = self._state
            # zero the existing IIR state in place so carry-over
            # samples (which encode filter memory of the previous audio)
            # are securely cleared rather than left in process memory
            # until the numpy allocator reuses the block.
            # reuse the just-zeroed array instead of allocating a
            # fresh ``np.zeros(...)`` block on every reset(). The
            # coefficients ``a`` and ``b`` don't change after init, so
            # the existing ``zi`` array is already the correct length
            # (``max(len(a), len(b)) - 1``) and dtype (float32). The
            # redundant allocation showed up as heap churn on every
            # device-disconnect / config-rebuild cycle.
            if zi.size > 0:
                zi.fill(0)
            # Anti-denormal: re-apply epsilon to the first state element.
            # Safe no-op when ``zi`` is empty (zero-size IIR state).
            if zi.size > 0:
                zi[0] = ANTIDENORMAL_EPSILON
            self._state = (b, a, zi)

    @property
    def is_degraded(self) -> bool:
        return self._state is None

    @property
    def degraded_reason(self) -> str:
        return "scipy not available" if self._state is None else ""
