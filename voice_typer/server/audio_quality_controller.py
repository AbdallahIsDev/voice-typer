"""Owns quality analysis + filter rebuilds via an app back-reference."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # np only for type annotations; keep out of runtime import (cold-start).
    import numpy as np

log = logging.getLogger(__name__)


class AudioQualityController:
    """Post-recording report never fires a tray notification.
    PERF-02 / R8: rebuild path refreshes recorder._vad_enabled cache.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    # Per-chunk quality callback (PortAudio thread -- MUST be non-blocking)

    def _on_audio_quality_chunk(self, rms: float, peak: float) -> None:
        """Runs inside the PortAudio audio callback (via"""
        try:
            aq = self._app._audio_quality
            aq._chunk_count += 1
            if peak > aq._peak:
                aq._peak = peak
            if peak >= aq.CLIPPING_THRESHOLD:
                aq._clip_count += 1
            # feed the precomputed rms into the EMA accumulator
            warning = aq.update_live_rms(rms)
            if warning is not None:
                # WARNING level (not DEBUG) so operators see sustained
                log.warning(
                    "[AUDIO_QUALITY] %s (rms_ema=%.6f, sustained_chunks=%d)",
                    warning,
                    aq.rms_ema,
                    aq.low_volume_chunks,
                )
        except Exception:
            # Quality analysis must NEVER break the audio callback.
            log.debug("[AUDIO_QUALITY] per-chunk update failed", exc_info=True)

    # Filter-chain rebuild (called from service.apply_config_side_effects)

    def _rebuild_audio_processor(self, force_sr: int | None = None) -> None:
        """Called by ``service.apply_config_side_effects`` when any"""
        try:
            if force_sr is not None:
                set_sr = getattr(self._app._audio_processor, "set_sample_rate", None)
                if callable(set_sr):
                    set_sr(force_sr)
            self._app._audio_processor.rebuild_from_config(self._app.config)
            # PERF-02 (R8): refresh the recorder's _vad_enabled cache so the
            recorder_on_config_changed = getattr(self._app.recorder, "on_config_changed", None)
            if callable(recorder_on_config_changed):
                recorder_on_config_changed()
            log.info(
                "[APP] Audio processor rebuilt: %s",
                self._app._audio_processor.filter_names or "none",
            )
        except Exception:
            log.exception("[APP] Failed to rebuild audio processor")

    # Post-recording analysis (called from RecordingController.stop())

    def _finalize_audio_quality_report(self, audio: np.ndarray) -> None:
        """Called from :meth:`_stop_dictation` after ``recorder.stop()``
        returns the (already filtered + resampled) audio.
        """
        # Hard short-circuit: NEVER show a tray notification. The
        try:
            if not getattr(self._app.config, "audio_quality_warnings", False):
                return
            # Even when the flag is True, we deliberately do NOT call
            try:
                report = self._app._audio_quality.analyze_full_audio(audio)
                if report.has_issues:
                    summary = report.get_summary()
                    log.info("[AUDIO_QUALITY] Issues detected: %s", summary)
            except Exception:
                log.debug("[AUDIO_QUALITY] finalize report failed", exc_info=True)
        finally:
            # reset for the next session ALWAYS runs -- even on
            try:
                self._app._audio_quality.reset()
            except Exception:
                log.debug("[AUDIO_QUALITY] reset() during finalize failed", exc_info=True)
