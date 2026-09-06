"""High-pass Butterworth IIR filter (second-order-sections form)."""

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
    """Butterworth high-pass filter (scipy SOS IIR, order 4).

    Removes low-frequency rumble (HVAC, traffic, fan noise, proximity
    effect) below the cutoff frequency. Order 4 gives a 24 dB/octave
    rolloff — steeper than the previous order-2 design.

    Stateful: the IIR ``zi`` state carries across ``process()`` calls
    for click-free continuity. Anti-denormal epsilon prevents CPU-killing
    denormal floats on some CPUs.

    Numerical form: the design uses ``scipy.signal.butter(...,
    output="sos")`` + ``sosfilt`` instead of transfer-function
    ``b``/``a`` + ``lfilter``. An order-4 Butterworth high-pass has four
    tightly-clustered poles near z=1; in ``b``/``a`` form, casting the
    coefficients to float32 rounds the pole radii OUTSIDE the unit
    circle at 44.1/48/96 kHz sample rates, making the recursion
    unstable — output diverges to inf/NaN within ~150 ms of audio
    (observed as "invalid value encountered in subtract" downstream in
    the equalizer). SOS sections keep each section's poles far enough
    from the unit circle that the same float32 rounding stays stable at
    every native sample rate, while keeping the identical float32
    per-chunk zero-copy allocation profile.
    """

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
            log.warning("[HIGHPASS] scipy not available — filter disabled")
            self._state = None
            return

        nyq = self._sample_rate / 2.0
        cutoff = min(max(self._cutoff_hz, 20.0), nyq * 0.99)
        try:
            from scipy.signal import butter, sos2zpk

            # Order 4 for steeper rolloff (24 dB/octave), SOS form for
            # numerical stability (see class docstring).
            sos = butter(4, cutoff / nyq, btype="high", output="sos")
            sos = np.asarray(sos, dtype=np.float32)
            # Design-time stability gate: the float32 cast above (which
            # keeps the per-chunk path zero-copy) must leave every pole
            # strictly INSIDE the unit circle. An order-4 Butterworth
            # high-pass has four tightly-clustered poles near z=1; the
            # old b/a form rounded them outward at high sample rates and
            # diverged to inf/NaN within ~150 ms — the failure mode the
            # SOS form exists to prevent. Verified against the real
            # design at every native rate (max|pole| ≈ 0.998 at 96 kHz,
            # comfortably < 1), so this only trips on a genuinely broken
            # design (e.g. a future order/cutoff change). An explicit
            # raise — not an ``assert``, which ``-O`` strips — routes
            # through the ``except`` below to the degraded-passthrough
            # path instead of ever running a recursion known to diverge.
            _zeros, poles, _gain = sos2zpk(sos)
            max_pole = float(np.max(np.abs(poles))) if len(poles) else 0.0
            if not max_pole < 1.0:
                raise ValueError(
                    f"unstable high-pass design: max|pole|={max_pole:.6f} >= 1 "
                    f"(cutoff={cutoff:.1f} Hz, sr={self._sample_rate}, order=4, float32 SOS)"
                )
            # Start from silence: all-zero IIR memory, exactly like the
            # previous b/a form (``zi = zeros``). Do NOT use
            # ``sosfilt_zi`` here — it returns the steady-state DC
            # initial condition, which for a HIGH-pass is an enormous
            # impulse-like start-up transient (a few units of amplitude
            # on the first samples) instead of a silent start.
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

        sos, zi = self._state
        original_shape = audio.shape
        # process in float32 (coefficients are float32 from init).
        # No per-chunk astype upcast — keeps the float32 zero-copy path.
        flat = np.ravel(audio).astype(np.float32, copy=False)
        filtered, zi = _get_sosfilt()(sos, flat, zi=zi)
        self._state = (sos, zi)
        return filtered.reshape(original_shape)

    def reset(self) -> None:
        if self._state is not None:
            sos, zi = self._state
            # zero the existing IIR state in place so carry-over
            # samples (which encode filter memory of the previous audio)
            # are securely cleared rather than left in process memory
            # until the numpy allocator reuses the block. Reusing the
            # just-zeroed array instead of allocating a fresh
            # ``np.zeros(...)`` block on every reset() avoids heap churn
            # on every device-disconnect / config-rebuild cycle. The
            # ``sos`` coefficients don't change after init, so the
            # existing ``zi`` array is already the correct shape
            # (``(n_sections, 2)``) and dtype (float32).
            if zi.size > 0:
                zi.fill(0)
            # Anti-denormal: re-apply epsilon to the first state element.
            # Safe no-op when ``zi`` is empty (zero-size IIR state).
            if zi.size > 0:
                zi[0, 0] = zi.dtype.type(ANTIDENORMAL_EPSILON)
            self._state = (sos, zi)

    @property
    def is_degraded(self) -> bool:
        return self._state is None
