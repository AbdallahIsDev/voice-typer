"""Audio quality analysis: clipping, low volume, high noise detection.

Analyzes audio during and after recording to detect quality issues
that would degrade transcription accuracy. Produces warnings that
can be shown as notifications or in the microphone diagnostics screen.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

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
    CLIPPING_THRESHOLD = 0.99       # Peak above this = clipping
    LOW_VOLUME_THRESHOLD = 0.005    # RMS below this = too quiet
    HIGH_NOISE_THRESHOLD = 0.5      # Noise ratio above this = too noisy

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

    def reset(self) -> None:
        """Reset analyzer state for a new recording session."""
        self._clip_count = 0
        self._peak = 0.0
        self._chunk_count = 0

    def analyze_chunk(self, chunk: np.ndarray) -> Optional[str]:
        """Analyze a single audio chunk during recording.

        Call this from the audio callback for each incoming chunk.
        Returns a warning string if an immediate issue is detected,
        or None if everything is fine.
        """
        rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
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
            report.warnings.append(
                f"Low volume: RMS={rms:.6f}. "
                "Increase microphone gain or move closer to mic."
            )

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
            rms, peak, self._clip_count,
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
