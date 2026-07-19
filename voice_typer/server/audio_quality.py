"""Audio quality analysis: clipping, low volume, high noise detection.

Analyzes audio during and after recording to detect quality issues
that would degrade transcription accuracy. Produces warnings that
can be shown as notifications or in the microphone diagnostics screen.
"""

import logging
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class AudioQualityReport:
    """Report on audio quality issues detected during recording."""

    clipping_detected: bool = False
    clipping_count: int = 0
    clipping_peak: float = 0.0

    low_volume_detected: bool = False
    low_volume_rms: float = 0.0

    high_noise_detected: bool = False
    noise_ratio: float = 0.0

    warnings: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return self.clipping_detected or self.low_volume_detected or self.high_noise_detected

    def get_summary(self) -> str:
        """Human-readable summary of issues."""
        parts = []
        if self.clipping_detected:
            parts.append(f"Clipping ({self.clipping_count} chunks, peak={self.clipping_peak:.4f}). Reduce mic gain.")
        if self.low_volume_detected:
            parts.append(f"Low volume (RMS={self.low_volume_rms:.6f}). Increase mic gain or move closer.")
        if self.high_noise_detected:
            parts.append(f"High noise (ratio={self.noise_ratio:.2f}). Try a quieter environment.")
        return " | ".join(parts) if parts else "Audio quality OK"


class AudioQualityAnalyzer:
    """Analyzes audio quality metrics during and after recording."""

    # Thresholds (configurable in future)
    CLIPPING_THRESHOLD = 0.99  # Peak above this = clipping
    LOW_VOLUME_THRESHOLD = 0.005  # RMS below this = too quiet
    HIGH_NOISE_THRESHOLD = 0.5  # Noise ratio above this = too noisy

    # AUDIO-8: EMA smoothing factor for the per-chunk RMS accumulator.
    # 0.05 = ~20-chunk effective window (at 16 Hz chunk rate that's ~1.25s
    # of audio) — long enough to suppress transient dips (e.g. a single
    # quiet consonant), short enough to surface a sustained low-input
    # condition within ~3s.
    RMS_EMA_ALPHA: float = 0.05

    # AUDIO-8: number of consecutive chunks with EMA below
    # LOW_VOLUME_THRESHOLD before the "low input level" warning fires.
    # At 16 Hz chunk rate, 50 chunks ≈ 3.1s of sustained low input.
    LOW_VOLUME_SUSTAINED_CHUNKS: int = 50

    def __init__(self):
        self._clip_count: int = 0
        self._peak: float = 0.0
        # 17-C-FIX-3: _rms_values was a write-only list — appended to on
        # every audio chunk (via app.py:_on_audio_quality_chunk) but never
        # read by any production code or test. analyze_full_audio()
        # recomputes RMS from the full audio array. Removed to eliminate
        # ~1-3 MB of wasted memory per 20-min recording + ~112K unnecessary
        # list.append() calls on the audio hot path.
        self._chunk_count: int = 0

        # AUDIO-8: per-chunk RMS exponential moving average. The live
        # quality callback (AudioQualityController._on_audio_quality_chunk)
        # already receives a precomputed ``rms`` value from
        # AudioProcessor._run_quality_check — previously that value was
        # dropped on the floor (only ``peak`` was used for clipping
        # detection). The EMA surfaces sustained low-input-level
        # conditions without recomputing RMS from the raw chunk.
        self._rms_ema: float = 0.0
        # Counter of consecutive chunks where EMA < LOW_VOLUME_THRESHOLD.
        # Reset to 0 on recovery (EMA rises back above threshold).
        self._low_volume_chunks: int = 0
        # Latch: once the warning fires for an episode, suppress repeats
        # until EMA recovers. Avoids log spam during a long quiet passage.
        self._low_volume_warned: bool = False

    def reset(self) -> None:
        """Reset analyzer state for a new recording session."""
        self._clip_count = 0
        self._peak = 0.0
        self._chunk_count = 0
        # AUDIO-8: reset the RMS EMA + low-volume tracking so a new
        # recording session starts with a clean slate.
        self._rms_ema = 0.0
        self._low_volume_chunks = 0
        self._low_volume_warned = False

    def update_live_rms(self, rms: float) -> str | None:
        """Feed a per-chunk RMS value into the EMA accumulator.

        AUDIO-8: called from the live quality callback
        (:meth:`AudioQualityController._on_audio_quality_chunk`) which
        already has ``rms`` computed by
        :meth:`AudioProcessor._run_quality_check` — avoids recomputing
        RMS from the raw chunk on the PortAudio audio thread.

        Updates ``self._rms_ema`` (exponential moving average,
        ``alpha = RMS_EMA_ALPHA``) and tracks consecutive chunks where
        the EMA is below :attr:`LOW_VOLUME_THRESHOLD`. Once
        :attr:`LOW_VOLUME_SUSTAINED_CHUNKS` is reached, returns a single
        warning string (and latches so subsequent chunks in the same
        low-volume episode don't re-fire).

        Args:
            rms: per-chunk RMS amplitude (linear, 0.0–1.0).

        Returns:
            Warning string if a new low-volume episode crossed the
            sustained threshold, else ``None``. Callers (the controller)
            log the warning at WARNING level — does NOT raise a tray
            notification (the post-recording report handles user-facing
            warnings via :meth:`analyze_full_audio`).
        """
        # EMA update: alpha * new + (1 - alpha) * prev.
        self._rms_ema = self.RMS_EMA_ALPHA * float(rms) + (1.0 - self.RMS_EMA_ALPHA) * self._rms_ema
        if self._rms_ema < self.LOW_VOLUME_THRESHOLD:
            self._low_volume_chunks += 1
            if self._low_volume_chunks >= self.LOW_VOLUME_SUSTAINED_CHUNKS and not self._low_volume_warned:
                self._low_volume_warned = True
                return "low input level — increase mic gain"
        else:
            # Recovery: reset the counter and unlatch the warning so a
            # future low-volume episode can fire again.
            self._low_volume_chunks = 0
            self._low_volume_warned = False
        return None

    def analyze_chunk(self, chunk: np.ndarray) -> str | None:
        """Analyze a single audio chunk during recording.

        Call this from the audio callback for each incoming chunk.
        Returns a warning string if an immediate issue is detected,
        or None if everything is fine.
        """
        # REC-3: dead no-op expression removed. The previous line
        # ``float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))``
        # computed an RMS value but never assigned it to anything — the
        # result was discarded, wasting CPU on every chunk (~16 Hz) for
        # no effect. ``analyze_full_audio`` is the path that actually
        # computes RMS for the quality report.
        peak = float(np.max(np.abs(chunk)))

        self._chunk_count += 1

        if peak > self._peak:
            self._peak = peak

        if peak >= self.CLIPPING_THRESHOLD:
            self._clip_count += 1

        # Immediate warning for clipping (every 50 chunks)
        if self._clip_count > 0 and self._chunk_count % 50 == 0:
            return f"Clipping detected: peak={peak:.4f}, {self._clip_count} clipping chunks. Reduce mic gain."

        return None

    def analyze_full_audio(self, audio: np.ndarray) -> AudioQualityReport:
        """Analyze the complete audio after recording stops.

        Returns a comprehensive quality report with all detected issues.
        """
        report = AudioQualityReport()

        if len(audio) == 0:
            report.warnings.append("No audio data captured")
            return report

        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64)))
        peak = float(np.max(np.abs(audio)))

        # Check clipping
        if self._clip_count > 0 or peak >= self.CLIPPING_THRESHOLD:
            report.clipping_detected = True
            report.clipping_count = self._clip_count
            report.clipping_peak = peak
            report.warnings.append(
                f"Clipping detected: peak={peak:.4f}, {self._clip_count} clipping chunks. "
                "Reduce microphone gain or move further from mic."
            )

        # Check low volume
        if rms < self.LOW_VOLUME_THRESHOLD:
            report.low_volume_detected = True
            report.low_volume_rms = rms
            report.warnings.append(f"Low volume: RMS={rms:.6f}. Increase microphone gain or move closer to mic.")

        # Check high noise (using variance as noise indicator)
        # High noise = high variance relative to RMS (signal is noisy, not clean speech)
        variance = float(np.var(audio))
        if rms > 0:
            noise_ratio = variance / (rms * rms) if rms > 0 else 0
            report.noise_ratio = noise_ratio
            if noise_ratio > self.HIGH_NOISE_THRESHOLD and rms < 0.05:
                report.high_noise_detected = True
                report.warnings.append(
                    f"High background noise: noise ratio={noise_ratio:.2f}. "
                    "Try a quieter environment or use a better microphone."
                )

        if not report.has_issues:
            report.warnings.append("Audio quality OK")

        log.info(
            "[AUDIO_QUALITY] Report: rms=%.6f, peak=%.4f, clips=%d, issues=%s",
            rms,
            peak,
            self._clip_count,
            ", ".join(report.warnings) if report.has_issues else "none",
        )

        return report

    @property
    def clip_count(self) -> int:
        """Number of clipping chunks detected so far."""
        return self._clip_count

    @property
    def peak(self) -> float:
        """Peak audio level detected so far."""
        return self._peak

    @property
    def rms_ema(self) -> float:
        """AUDIO-8: exponential moving average of per-chunk RMS.

        Read-only accessor so callers (tests, diagnostics UI, log
        scrapers) can observe the smoothed RMS without recomputing it.
        Updated by :meth:`update_live_rms` from the live quality
        callback.
        """
        return self._rms_ema

    @property
    def low_volume_chunks(self) -> int:
        """AUDIO-8: consecutive chunks with EMA below LOW_VOLUME_THRESHOLD."""
        return self._low_volume_chunks

    @property
    def low_volume_warned(self) -> bool:
        """AUDIO-8: latch — True once the low-volume warning has fired
        for the current episode. Resets to False on recovery."""
        return self._low_volume_warned
