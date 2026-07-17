"""RW-9 god-class decomposition: AudioQualityController — extracted from VoiceTyperApp.

Owns the audio-quality concern: per-chunk quality accumulation, on-the-fly
filter-chain rebuilds on config change, and the final post-recording
quality report.

Previously these three private methods lived on ``VoiceTyperApp``:

    - ``_on_audio_quality_chunk``
    - ``_rebuild_audio_processor``
    - ``_finalize_audio_quality_report``

The behaviour is preserved verbatim — only the class boundary moved.
``VoiceTyperApp`` keeps thin delegate methods so existing callers (the
``AudioProcessor`` quality-callback wiring in ``__init__``,
``service.apply_config_side_effects``, and
``RecordingController.stop()``) keep working unchanged.

RW-9 §5.2 risk: LOW — these methods are cohesive (all about audio
quality) and only touch ``self._app._audio_quality`` /
``self._app._audio_processor`` / ``self._app.tray`` /
``self._app.recorder`` / ``self._app.config``.

A note on the PortAudio thread contract (mirrors the original
docstring on ``_on_audio_quality_chunk``): that callback runs inside
the PortAudio audio callback thread, so it MUST be non-blocking. The
body only updates cheap running statistics — no I/O, no allocation of
large structures, no per-chunk logging. Full analysis runs in
``_finalize_audio_quality_report`` after ``recorder.stop()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    # Imported only for the type annotation on ``__init__``. The runtime
    # import is avoided to prevent a circular import (app.py imports
    # this module at the top of its ``__init__`` body).
    pass

log = logging.getLogger(__name__)


class AudioQualityController:
    """Owns per-chunk + post-recording audio-quality analysis + filter rebuilds.

    RW-9 §5.2: extracted from ``VoiceTyperApp``. The app passes itself
    (``app``) so ``AudioQualityController`` can:
    - Read/write ``app._audio_quality`` (the :class:`AudioQualityAnalyzer`
      instance) — per-chunk accumulators and post-recording analysis.
    - Read/write ``app._audio_processor`` (the :class:`AudioProcessor`)
      — rebuilt atomically when filter-chain config fields change.
    - Read ``app.config`` (``audio_quality_warnings`` kill-switch,
      plus the config consumed by ``rebuild_from_config``).
    - Read ``app.recorder`` — refreshes the ``_vad_enabled`` cache
      after a rebuild via ``recorder.on_config_changed`` (PERF-02 / R8).
    - (Historically) call ``app.tray.notify`` — but the post-recording
      report deliberately never surfaces a tray notification anymore
      (see :meth:`_finalize_audio_quality_report`).
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    # ── Per-chunk quality callback (PortAudio thread — MUST be non-blocking) ──

    def _on_audio_quality_chunk(self, rms: float, peak: float) -> None:
        """Per-chunk quality callback wired to AudioProcessor.

        Runs inside the PortAudio audio callback (via
        ``AudioProcessor.process_chunk`` → ``_run_quality_check``), so
        it MUST be non-blocking.  We only update cheap running
        statistics — no I/O, no allocation of large structures, no
        logging per chunk.  Full analysis runs in
        :meth:`_finalize_audio_quality_report` after stop().

        The analyzer's :meth:`analyze_chunk` would normally take the
        raw numpy chunk, but we already have (rms, peak) computed by
        the AudioProcessor — reconstructing the chunk just to compute
        the same metrics again would waste cycles.  Instead we feed
        the precomputed values into the analyzer's internal accumulators
        directly.
        """
        try:
            aq = self._app._audio_quality
            # Mirror analyze_chunk() without the numpy work — we
            # already have rms and peak from the AudioProcessor.
            # 17-C-FIX-3: _rms_values was removed (write-only list);
            # we no longer append to it here.
            aq._chunk_count += 1
            if peak > aq._peak:
                aq._peak = peak
            if peak >= aq.CLIPPING_THRESHOLD:
                aq._clip_count += 1
        except Exception:
            # Quality analysis must NEVER break the audio callback.
            log.debug("[AUDIO_QUALITY] per-chunk update failed", exc_info=True)

    # ── Filter-chain rebuild (called from service.apply_config_side_effects) ──

    def _rebuild_audio_processor(self) -> None:
        """ADR 0007 §6.1: Rebuild the audio filter chain from current config.

        Called by ``service.apply_config_side_effects`` when any
        ``noise_filter_*`` or ``audio_preset`` or
        ``noise_suppression_method`` config field changes. Atomically
        swaps the filter chain so the next ``process_chunk()`` call
        uses the new filters — no restart required.
        """
        try:
            self._app._audio_processor.rebuild_from_config(self._app.config)
            # PERF-02 (R8): refresh the recorder's _vad_enabled cache so the
            # next audio chunk sees the new VAD config without re-evaluating
            # 6 getattr calls per access on the RT thread. The recorder has a
            # 5-second TTL safety net, but explicit refresh gives sub-second
            # visibility on config changes.
            recorder_on_config_changed = getattr(self._app.recorder, "on_config_changed", None)
            if callable(recorder_on_config_changed):
                recorder_on_config_changed()
            log.info(
                "[APP] Audio processor rebuilt: %s",
                self._app._audio_processor.filter_names,
            )
        except Exception:
            log.exception("[APP] Failed to rebuild audio processor")

    # ── Post-recording analysis (called from RecordingController.stop()) ──

    def _finalize_audio_quality_report(self, audio: np.ndarray) -> None:
        """Run final audio-quality analysis and surface warnings.

        Called from :meth:`_stop_dictation` after ``recorder.stop()``
        returns the (already filtered + resampled) audio.

        FIX-HOTKEY-AND-NOTIFICATION: the tray notification that used to
        fire here ("Low volume (RMS=...). Increase mic gain or move
        closer. | High noise (ratio=...). Try a quieter environment")
        was deemed annoying by users. We now short-circuit at the top of
        this method so NO tray notification is ever shown — even if a
        user manually sets ``audio_quality_warnings = True`` in their
        config file. The internal ``AudioQualityAnalyzer`` may still
        run for logging purposes (below), but it MUST NOT surface any
        user-facing notification.
        """
        # Hard short-circuit: NEVER show a tray notification. The
        # ``audio_quality_warnings`` config field is honored here only
        # as a kill-switch (when False, we skip the analysis entirely
        # for efficiency); when True we still run the analysis for
        # internal logging but DO NOT call ``self.tray.notify``.
        if not getattr(self._app.config, "audio_quality_warnings", False):
            return
        # Even when the flag is True, we deliberately do NOT call
        # ``self.tray.notify``. Run the analysis for internal logging
        # only, then bail out.
        try:
            report = self._app._audio_quality.analyze_full_audio(audio)
            if report.has_issues:
                summary = report.get_summary()
                log.info("[AUDIO_QUALITY] Issues detected: %s", summary)
            # Reset for the next session.
            self._app._audio_quality.reset()
        except Exception:
            log.debug("[AUDIO_QUALITY] finalize report failed", exc_info=True)
