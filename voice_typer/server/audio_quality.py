"""Audio quality analysis: clipping, low volume, high noise detection."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

# (PERF-COLDSTART-001): numpy is ~250-335ms cumulative on cold start
from voice_typer.server._audio_constants import AUDIO_CLIPPING_THRESHOLD, AUDIO_LOW_VOLUME_RMS
from voice_typer.server._lazy_import import lazy_module

np = lazy_module("numpy")

log = logging.getLogger(__name__)

# Block size (elements) for the float64 sum-of-squares
_SUMSQ_BLOCK_ELEMENTS = 1 << 20


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
    CLIPPING_THRESHOLD = AUDIO_CLIPPING_THRESHOLD  # Peak above this = clipping
    LOW_VOLUME_THRESHOLD = AUDIO_LOW_VOLUME_RMS  # RMS below this = too quiet
    HIGH_NOISE_THRESHOLD = 0.5  # Noise ratio above this = too noisy

    # EMA smoothing factor for the per-chunk RMS accumulator.
    RMS_EMA_ALPHA: float = 0.05

    # number of consecutive chunks with EMA below
    LOW_VOLUME_SUSTAINED_CHUNKS: int = 50

    def __init__(self):
        self._clip_count: int = 0
        self._peak: float = 0.0
        # 17-C-: _rms_values was a write-only list, appended to on
        self._chunk_count: int = 0

        # per-chunk RMS exponential moving average. The live
        self._rms_ema: float = 0.0
        # Counter of consecutive chunks where EMA < LOW_VOLUME_THRESHOLD.
        self._low_volume_chunks: int = 0
        # Latch: once the warning fires for an episode, suppress repeats
        self._low_volume_warned: bool = False

    def reset(self) -> None:
        """Reset analyzer state for a new recording session."""
        self._clip_count = 0
        self._peak = 0.0
        self._chunk_count = 0
        # reset the RMS EMA + low-volume tracking so a new
        self._rms_ema = 0.0
        self._low_volume_chunks = 0
        self._low_volume_warned = False

    def update_live_rms(self, rms: float) -> str | None:
        """Feed a per-chunk RMS value into the EMA accumulator."""
        # EMA update: alpha * new + (1 - alpha) * prev.
        self._rms_ema = self.RMS_EMA_ALPHA * float(rms) + (1.0 - self.RMS_EMA_ALPHA) * self._rms_ema
        if self._rms_ema < self.LOW_VOLUME_THRESHOLD:
            self._low_volume_chunks += 1
            if self._low_volume_chunks >= self.LOW_VOLUME_SUSTAINED_CHUNKS and not self._low_volume_warned:
                self._low_volume_warned = True
                return "low input level, increase mic gain"
        else:
            # Recovery: reset the counter and unlatch the warning so a
            self._low_volume_chunks = 0
            self._low_volume_warned = False
        return None

    def analyze_chunk(self, chunk: np.ndarray) -> str | None:
        """Analyze a single audio chunk during recording."""
        # REC-3: dead no-op expression removed. The previous line
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

        flat = audio.ravel()
        # Keep float64 accumulation like the previous
        size = int(flat.size)
        mean = float(flat.mean(dtype=np.float64))
        sumsq = 0.0
        for block_start in range(0, size, _SUMSQ_BLOCK_ELEMENTS):
            block = flat[block_start : block_start + _SUMSQ_BLOCK_ELEMENTS].astype(np.float64)
            sumsq += float(np.dot(block, block))
        rms = math.sqrt(max(sumsq / size, 0.0))
        # Peak without the np.abs temporary: max(max, −min) is the same
        peak = max(float(flat.max()), -float(flat.min()))

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
        variance = max(sumsq / size - mean * mean, 0.0)
        if rms > 0:
            noise_ratio = variance / (rms * rms)
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
            "[AUDIO_QUALITY] Report: rms=%.6f, peak=%.4f, clips=%d | issues=%s",
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
        """exponential moving average of per-chunk RMS."""
        return self._rms_ema

    @property
    def low_volume_chunks(self) -> int:
        """consecutive chunks with EMA below LOW_VOLUME_THRESHOLD."""
        return self._low_volume_chunks

    @property
    def low_volume_warned(self) -> bool:
        """latch, True once the low-volume warning has fired
        for the current episode. Resets to False on recovery."""
        return self._low_volume_warned
