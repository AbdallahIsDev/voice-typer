"""Real-time and post-capture audio cleaning for the dictation pipeline.

The :class:`AudioProcessor` applies a chain of optional filters to
microphone audio:

1. **High-pass filter** (scipy IIR Butterworth) — removes low-frequency
   rumble (HVAC, traffic, computer fans) below a configurable cutoff
   (default 80 Hz).  Cheap (~0.05 ms per chunk), default ON.

2. **Noise gate** (numpy) — silences audio below a threshold (default
   -45 dBFS / 0.015 linear) to remove idle hiss and keyboard gap noise.
   Cheap (~0.02 ms per chunk), default ON.

3. **RNNoise** (optional, ``rnnoise-webrtc``) — neural-network-based
   real-time denoiser.  Removes broadband noise (fans, AC, room
   ambience).  ~1 ms per 30 ms frame.  Default OFF due to CPU cost;
   users opt in via settings.

4. **Post-capture spectral gating** (``noisereduce``, offline) — runs on
   the complete audio in ``Recorder.stop()``.  Safety net if the
   real-time filters are disabled or miss noise.  ~200 ms for 30 s
   audio.  Default ON.

All filters are individually toggleable.  If a filter library is
missing (scipy, noisereduce, rnnoise), that filter is silently skipped
with a debug log — the app never crashes on a missing optional dep.

**Critical constraint:** the real-time chain
(:meth:`process_chunk`) runs inside the PortAudio audio callback and
**must not block**.  Only pre-allocated buffers and fast numpy/scipy
operations are used.  RNNoise is borderline (~1 ms); if xruns appear
in testing, it should be moved to a consumer thread reading from a
ring buffer.  For v1.1.0 it stays in-callback with default OFF.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

QualityCallback = Callable[[float, float], None]


@dataclass
class AudioProcessorConfig:
    """Configuration for :class:`AudioProcessor`.

    All fields map 1:1 to ``Config`` fields with the ``noise_filter_``
    prefix.  Constructed once at recording start and cached.
    """

    enabled: bool = True
    highpass: bool = True
    highpass_cutoff_hz: float = 80.0
    noise_gate: bool = True
    noise_gate_threshold: float = 0.015
    rnnoise: bool = False
    post_capture: bool = True

    @classmethod
    def from_config(cls, config: object) -> "AudioProcessorConfig":
        """Build from a :class:`Config`-like object with ``noise_filter_*`` attrs."""
        return cls(
            enabled=bool(getattr(config, "noise_filter_enabled", True)),
            highpass=bool(getattr(config, "noise_filter_highpass", True)),
            highpass_cutoff_hz=float(getattr(config, "noise_filter_highpass_cutoff_hz", 80.0)),
            noise_gate=bool(getattr(config, "noise_filter_gate", True)),
            noise_gate_threshold=float(getattr(config, "noise_filter_gate_threshold", 0.015)),
            rnnoise=bool(getattr(config, "noise_filter_rnnoise", False)),
            post_capture=bool(getattr(config, "noise_filter_post_capture", True)),
        )


class AudioProcessor:
    """Real-time + post-capture audio cleaning.

    Real-time filters run in :meth:`process_chunk` (called from the
    PortAudio callback — must be non-blocking).  Post-capture filtering
    runs in :meth:`process_full_audio` (called from ``Recorder.stop()``
    — can block).

    The processor is stateful: the high-pass filter maintains IIR
    state across chunks for continuity, and RNNoise may buffer partial
    frames.  Call :meth:`reset` at the start of each recording session.
    """

    def __init__(self, config: AudioProcessorConfig, sample_rate: int = 16000) -> None:
        self._config = config
        self._sample_rate = sample_rate
        self._hp_state: Optional[tuple] = None  # (b, a, zi)
        self._rnnoise: Optional[object] = None
        self._rnnoise_frame_size: int = 480  # 30 ms at 16 kHz
        self._rnnoise_carry: np.ndarray = np.array([], dtype=np.float32)
        self._quality_callback: Optional[QualityCallback] = None
        self._init_filters()

    # ── Lifecycle ───────────────────────────────────────────────────

    def _init_filters(self) -> None:
        """Pre-compute filter coefficients and load optional libraries."""
        if self._config.highpass:
            self._init_highpass()
        if self._config.rnnoise:
            self._init_rnnoise()

    def _init_highpass(self) -> None:
        """Pre-compute Butterworth high-pass filter coefficients."""
        try:
            from scipy.signal import butter

            nyq = self._sample_rate / 2.0
            cutoff = min(self._config.highpass_cutoff_hz, nyq * 0.99)
            cutoff = max(20.0, cutoff)
            # Order 2 Butterworth — good rolloff without excessive ripple.
            b, a = butter(2, cutoff / nyq, btype="high")
            # zi is the initial filter state (zeros = zero-phase start).
            zi = np.zeros(max(len(a), len(b)) - 1, dtype=np.float64)
            self._hp_state = (b, a, zi)
            log.debug(
                "[AUDIO-PROC] high-pass ready: cutoff=%.0f Hz, sr=%d",
                cutoff, self._sample_rate,
            )
        except ImportError:
            log.info("[AUDIO-PROC] scipy not available — high-pass disabled")
            self._hp_state = None
        except Exception as exc:
            log.warning("[AUDIO-PROC] high-pass init failed: %s", exc)
            self._hp_state = None

    def _init_rnnoise(self) -> None:
        """Load the RNNoise model (optional, may not be installed)."""
        try:
            import rnnoise  # type: ignore[import-not-found]

            self._rnnoise = rnnoise.RNNoise()
            log.info("[AUDIO-PROC] RNNoise loaded")
        except ImportError:
            log.info("[AUDIO-PROC] rnnoise not installed — skipping neural denoise")
            self._rnnoise = None
        except Exception as exc:
            log.warning("[AUDIO-PROC] RNNoise init failed: %s", exc)
            self._rnnoise = None

    def reset(self) -> None:
        """Reset filter state for a new recording session.

        Call from ``Recorder.start()`` so the high-pass IIR state
        doesn't carry over from the previous session.
        """
        if self._hp_state is not None:
            b, a, _ = self._hp_state
            self._hp_state = (b, a, np.zeros(max(len(a), len(b)) - 1, dtype=np.float64))
        self._rnnoise_carry = np.array([], dtype=np.float32)

    def set_quality_callback(self, cb: QualityCallback) -> None:
        """Wire a quality detector callback (revives audio_quality.py).

        The callback receives ``(rms, peak)`` per chunk and can
        accumulate statistics for clipping/noise/SNR reporting.
        """
        self._quality_callback = cb

    # ── Real-time processing (called from PortAudio callback) ───────

    def process_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """Apply the real-time filter chain to a single audio chunk.

        **Must be non-blocking.**  Only pre-allocated buffers and fast
        numpy/scipy operations are used.  Returns the filtered chunk
        (same shape and dtype as input).
        """
        if not self._config.enabled or chunk.size == 0:
            return chunk

        # Ensure float32 for consistent processing.
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)

        # 1. High-pass filter (stateful IIR — continuous across chunks)
        if self._hp_state is not None:
            chunk = self._apply_highpass(chunk)

        # 2. Noise gate
        if self._config.noise_gate:
            chunk = self._apply_noise_gate(chunk)

        # 3. RNNoise (optional, real-time neural denoise)
        if self._rnnoise is not None:
            chunk = self._apply_rnnoise(chunk)

        # 4. Quality detection (feeds tray warnings via callback)
        if self._quality_callback is not None:
            self._run_quality_check(chunk)

        return chunk

    def _apply_highpass(self, chunk: np.ndarray) -> np.ndarray:
        """Apply the stateful Butterworth high-pass filter."""
        from scipy.signal import lfilter

        b, a, zi = self._hp_state  # type: ignore[misc]
        # lfilter returns (filtered, new_zi); update state for continuity.
        filtered, zi = lfilter(b, a, chunk.astype(np.float64), zi=zi)
        self._hp_state = (b, a, zi)
        return filtered.astype(np.float32, copy=False)

    def _apply_noise_gate(self, chunk: np.ndarray) -> np.ndarray:
        """Silence audio below the threshold (removes idle hiss)."""
        if chunk.size == 0:
            return chunk
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        if rms < self._config.noise_gate_threshold:
            # In-place zero — cheaper than allocating a new array.
            chunk.fill(0.0)
        return chunk

    def _apply_rnnoise(self, chunk: np.ndarray) -> np.ndarray:
        """Process chunk through RNNoise in fixed-size frames.

        RNNoise requires 480-sample frames (30 ms at 16 kHz).  This
        method buffers partial frames at chunk boundaries for
        continuity.  The carry buffer is reset on :meth:`reset`.
        """
        if self._rnnoise is None or chunk.size == 0:
            return chunk

        frame_size = self._rnnoise_frame_size
        # Prepend any carry from the previous chunk.
        combined = np.concatenate([self._rnnoise_carry, chunk.ravel()])
        n_full = len(combined) // frame_size
        remainder = len(combined) - n_full * frame_size

        if n_full == 0:
            # Not enough data for even one frame — buffer it.
            self._rnnoise_carry = combined
            # Return silence for this chunk (will be filled when we
            # have a full frame).  This adds ≤30 ms latency.
            return np.zeros_like(chunk)

        output_parts = []
        for i in range(n_full):
            start = i * frame_size
            frame = combined[start:start + frame_size].astype(np.float32)
            try:
                # RNNoise expects float32 in [-1, 1] and returns same.
                cleaned = self._rnnoise.filter_frame(frame)  # type: ignore[union-attr]
                output_parts.append(cleaned)
            except Exception as exc:
                log.debug("[AUDIO-PROC] RNNoise frame failed: %s", exc)
                output_parts.append(frame)

        # Save remainder for next call.
        if remainder > 0:
            self._rnnoise_carry = combined[n_full * frame_size:]
        else:
            self._rnnoise_carry = np.array([], dtype=np.float32)

        result = np.concatenate(output_parts)
        # If we consumed carry from the previous chunk, the output may
        # be longer or shorter than the input.  Return the portion
        # corresponding to this chunk's input length, padding/truncating.
        consumed_input = len(chunk.ravel())
        if len(result) >= consumed_input:
            return result[:consumed_input].reshape(chunk.shape)
        else:
            padded = np.zeros(consumed_input, dtype=np.float32)
            padded[:len(result)] = result
            return padded.reshape(chunk.shape)

    def _run_quality_check(self, chunk: np.ndarray) -> None:
        """Compute lightweight quality metrics and fire the callback."""
        if chunk.size == 0:
            return
        peak = float(np.max(np.abs(chunk)))
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        try:
            self._quality_callback(rms, peak)  # type: ignore[misc]
        except Exception:
            log.debug("[AUDIO-PROC] quality callback raised", exc_info=True)

    # ── Post-capture processing (called from Recorder.stop()) ───────

    def process_full_audio(self, audio: np.ndarray) -> np.ndarray:
        """Apply offline spectral noise reduction to the full recording.

        Called from ``Recorder.stop()`` after resampling.  Uses
        ``noisereduce`` (spectral gating) with a noise profile sampled
        from the first 0.5 s of audio (assumed pre-speech silence).
        ~200 ms for 30 s audio at 16 kHz.
        """
        if not self._config.post_capture or audio.size == 0:
            return audio
        try:
            import noisereduce as nr  # type: ignore[import-not-found]
        except ImportError:
            log.debug("[AUDIO-PROC] noisereduce not installed — skipping post-capture")
            return audio

        # Need at least 0.5 s of audio for a noise profile.
        min_samples = int(0.5 * self._sample_rate)
        if len(audio) < min_samples:
            log.debug("[AUDIO-PROC] audio too short for post-capture (%d samples)", len(audio))
            return audio

        try:
            noise_profile = audio[:min_samples]
            cleaned = nr.reduce_noise(
                y=audio,
                sr=self._sample_rate,
                y_noise=noise_profile,
                stationary=True,
                prop_decrease=0.8,
            )
            log.info(
                "[AUDIO-PROC] noisereduce: %d → %d samples (prop_decrease=0.8)",
                len(audio), len(cleaned),
            )
            return cleaned.astype(np.float32, copy=False)
        except Exception as exc:
            log.warning("[AUDIO-PROC] noisereduce failed: %s", exc)
            return audio

    # ── Introspection ───────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled

    @property
    def has_highpass(self) -> bool:
        return self._hp_state is not None

    @property
    def has_rnnoise(self) -> bool:
        return self._rnnoise is not None

    @property
    def has_post_capture(self) -> bool:
        try:
            import noisereduce  # noqa: F401
            return self._config.post_capture
        except ImportError:
            return False
