"""Transcription stage for the dictation pipeline."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any  # noqa: F401  # re-exported for tests (transcribe_step.Any)

from voice_typer.server.branding import APP_NAME
from voice_typer.server.cloud_engines import CloudEngine
from voice_typer.server.dictation_pipeline.helpers import (
    BackendNotLoadedError,
    _lookup_local_whisper,
)
from voice_typer.server.i18n import t as _i18n_t
from voice_typer.server.tray_types import AppState

if TYPE_CHECKING:
    # Type-only import to avoid the import cycle (the orchestrator is
    from voice_typer.server.app import VoiceTyperApp

    # Real class (helpers.py), imported for annotation only. The
    from voice_typer.server.dictation_pipeline.helpers import _AbortWatcher

# NOTE: ``_AbortWatcher`` is intentionally NOT imported at module level

log = logging.getLogger(__name__)


class _TranscribeStepMixin:
    """* :meth:`_check_resources_throttled`: throttled wrapper around
    * :meth:`_check_resources`: direct (unthrottled) probe; called
    """

    # Declared here so the standalone mixin type-checks (mypy cannot
    _app: VoiceTyperApp
    _cycle_id: str
    _audio: Any
    _audio_stats: tuple[float, float, float] | None
    _duration: float
    _recorded_rms: float
    _last_resources_check_ts: float
    _resources_check_interval: float

    def _hide_or_idle_bubble(self, log_label: str = "bubble hide/set idle") -> None:
        """Hide the waveform bubble or set it to idle (always_visible mode)."""
        try:
            if self._app.config.bubble_behavior == "always_visible":
                self._app._waveform_bubble.set_state("idle")
            else:
                self._app._waveform_bubble.hide()
        except Exception:
            log.debug("[PIPELINE] %s failed", log_label, exc_info=True)

    def _check_resources_throttled(self) -> None:
        """Throttled wrapper around _check_resources.
        Delegates to ``resource_probe.check_resources_throttled`` (extracted
        """
        from voice_typer.server.resource_probe import check_resources_throttled

        interval = getattr(self, "_resources_check_interval", 60.0)
        if not isinstance(interval, (int, float)):
            interval = 60.0
        instance_ts = getattr(self, "_last_resources_check_ts", 0.0)
        if not isinstance(instance_ts, (int, float)):
            instance_ts = 0.0
        app = getattr(self, "_app", None)
        shared_ts = getattr(app, "_shared_resources_check_ts", None) if app is not None else None
        last = float(shared_ts) if isinstance(shared_ts, (int, float)) else float(instance_ts)

        new_ts = check_resources_throttled(
            last,
            float(interval),
            logger=log,
        )
        self._last_resources_check_ts = new_ts
        try:
            if app is not None:
                app._shared_resources_check_ts = new_ts
        except Exception:
            log.debug("[PIPELINE] failed to persist shared resources-check timestamp", exc_info=True)

    def _check_resources(self) -> None:
        """Delegates to ``resource_probe.check_resources`` (extracted to a
        are logged at DEBUG level by the delegated ``check_resources``
        """
        from voice_typer.server.resource_probe import check_resources

        check_resources(logger=log)

    def _transcribe(self) -> str:
        """Step 1: Get transcription via streaming finalize or direct.

        Returns the transcript from the active streaming session (if one
        """
        #  capture the active transcriber ONCE, the
        active = self._app.models.active_transcriber()
        backend_was_loaded = bool(getattr(active, "is_loaded", False))

        # Clear any stale abort from a previous cycle before starting
        from voice_typer.server import dictation_pipeline as _dp_pkg

        _abort_watcher_cls = _dp_pkg._AbortWatcher
        abort_watcher: _AbortWatcher | None = None
        if active is not None and hasattr(active, "clear_abort"):
            with contextlib.suppress(Exception):
                active.clear_abort()
            if hasattr(active, "request_abort"):
                abort_watcher = _abort_watcher_cls(self._app, self._cycle_id, active)
                abort_watcher.start()

        try:
            #  sibling: pop_streaming_session() atomically owns the
            session = self._app.recording.pop_streaming_session()
            if session is not None:
                log.info("[STREAMING] Finalizing streaming transcript (cycle=%s)", self._cycle_id)
                # Annotated so the batch-branch Any return (from the
                try:
                    import numpy as _np

                    _audio_backup = self._audio.copy() if isinstance(self._audio, _np.ndarray) else self._audio
                except Exception:
                    log.debug("[STREAMING] audio backup before finalize failed", exc_info=True)
                    _audio_backup = None
                text: str = session.finalize(self._audio)
                if not text and active is not None and backend_was_loaded and _audio_backup is not None:
                    try:
                        _audio_len = len(_audio_backup)
                    except Exception:
                        log.debug("[STREAMING] len(audio backup) failed", exc_info=True)
                        _audio_len = 0
                    _audio_captured = self._recorded_rms >= 0.005
                    if _audio_len > 0 and _audio_captured:
                        log.info(
                            "[STREAMING] Empty streaming result on high-energy audio "
                            "(rms=%.4f) | retrying batch transcription (cycle=%s)",
                            self._recorded_rms,
                            self._cycle_id,
                        )
                        try:
                            _retry_local = None
                            if isinstance(active, CloudEngine):
                                _retry_local = _lookup_local_whisper(self._app)
                            _registry = self._app.models.registry
                            with _registry.busy_context(_registry.active_name):
                                text = active.transcribe_with_fallback(
                                    _audio_backup,
                                    audio_stats=self._audio_stats,
                                    local_engine=_retry_local,
                                )
                            self._quality_summary = getattr(active, "last_quality_summary", None)
                        except Exception:
                            log.debug(
                                "[STREAMING] Batch retry after empty streaming result failed",
                                exc_info=True,
                            )
                            text = ""
            else:
                # When ``active_transcriber()`` returned None AND
                if active is None:
                    raise BackendNotLoadedError(
                        "No ASR backend is registered, wait for the model "
                        "to finish loading, or open Settings to verify a "
                        "backend is available.",
                        engine_name="<none>",
                    )
                # pass the pre-computed audio stats so the

                # a-review Finding 8: previously this call was wrapped in a

                # When the active backend is a CloudEngine, look
                local_engine = None
                if isinstance(active, CloudEngine):
                    local_engine = _lookup_local_whisper(self._app)
                # Route through the registry's busy-flag wrapper so
                registry = self._app.models.registry
                with registry.busy_context(registry.active_name):
                    text = active.transcribe_with_fallback(
                        self._audio,
                        audio_stats=self._audio_stats,
                        local_engine=local_engine,
                    )
                    # Capture the engine's compact quality summary (mean /
                    self._quality_summary = getattr(active, "last_quality_summary", None)
        finally:
            if abort_watcher is not None:
                with contextlib.suppress(Exception):
                    abort_watcher.stop()

        # PERF-015: refresh the LRU timestamp for the active backend
        with contextlib.suppress(Exception):
            self._app.models.touch_active_model()

        # reuse the captured ``active`` local for device_info
        self._device_info = (
            active.device_info if active is not None and hasattr(active, "device_info") else "Parakeet ASR"
        )

        # Empty-transcription diagnostic: when the engine returns an
        if not text:
            backend_name = type(active).__name__ if active is not None else "<none>"
            stats_repr = (
                "rms={:.4f} peak={:.4f} silence_pct={:.1f}".format(*self._audio_stats)
                if self._audio_stats is not None
                else "<unavailable>"
            )
            log.warning(
                "[TRANSCRIBE] Empty transcription result (cycle=%s | "
                "duration=%.2fs, recorded_rms=%.4f, audio_stats=[%s] | "
                "backend=%s, backend_is_loaded=%s, path=%s), check empty transcription handler guidance",
                self._cycle_id,
                self._duration,
                self._recorded_rms,
                stats_repr,
                backend_name,
                backend_was_loaded,
                "streaming" if session is not None else "batch",
            )
            # if the backend was not loaded when we entered
            if not backend_was_loaded:
                raise BackendNotLoadedError(
                    "Active ASR backend is not loaded, "
                    "transcribe_with_fallback returned empty output. "
                    "Check that the model finished loading and that no "
                    "set_active_backend call unloaded it mid-cycle.",
                    engine_name=backend_name,
                )
        return text

    def _handle_empty_transcription(self) -> None:
        """Step 2: Handle case where no speech was detected."""
        # BP-89: ESC-during-transcribe marks the cycle cancelled
        _recording = getattr(self._app, "recording", None)
        _cancelled_set = getattr(_recording, "_cancelled_cycle_ids", None)
        if _cancelled_set is not None:
            _cancelled_lock = getattr(_recording, "_cancelled_cycle_ids_lock", None)
            if _cancelled_lock is not None:
                with _cancelled_lock:
                    _is_cancelled = self._cycle_id in _cancelled_set
            else:
                _is_cancelled = self._cycle_id in _cancelled_set
            if _is_cancelled:
                log.info(
                    "[TRANSCRIBE] Cycle %s was ESC-cancelled, skipping "
                    "empty-transcription handling (no misleading "
                    "'no speech detected' message)",
                    self._cycle_id,
                )
                self._hide_or_idle_bubble("bubble hide/set idle on cancelled empty")
                return

        log.info("[TRANSCRIBE] No speech detected (cycle=%s)", self._cycle_id)
        # Hide the bubble since there's nothing to
        self._hide_or_idle_bubble("bubble hide/set idle on empty")

        # UX-SILENCE-GRACE: Suppress the notification for short recordings (< 15s).
        _grace_period = 15.0
        # Same near-silence threshold used by the long-recording branch
        _silence_rms_threshold = 0.005
        _audio_was_captured = self._recorded_rms >= _silence_rms_threshold

        if self._duration < _grace_period and not _audio_was_captured:
            # Short recording AND near-silence: the user almost certainly
            log.info(
                "[TRANSCRIBE] No speech detected but recording was only %.1fs "
                "(< %.0fs grace period) and near-silent (rms=%.4f), suppressing notification",
                self._duration,
                _grace_period,
                self._recorded_rms,
            )
            self._app.tray.set_state(AppState.IDLE, _i18n_t("state.dictation_pipeline.no_speech_detected"))
            #  (observability): publish a ``dictation_suppressed``
            with contextlib.suppress(Exception):
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "dictation_suppressed",
                        "data": {
                            "duration": self._duration,
                            "recorded_rms": self._recorded_rms,
                            "reason": "short_silence",
                        },
                    }
                )
        elif self._duration < _grace_period and _audio_was_captured:
            # Short recording BUT real audio was captured: the engine
            log.warning(
                "[TRANSCRIBE] Short recording (%.1fs) with audio "
                "(rms=%.4f >= %.4f) produced empty transcription, "
                "engine returned no text (cycle=%s)",
                self._duration,
                self._recorded_rms,
                _silence_rms_threshold,
                self._cycle_id,
            )
            self._app.tray.set_state(
                AppState.IDLE,
                _i18n_t("state.dictation_pipeline.transcription_empty"),
            )
        elif self._recorded_rms < _silence_rms_threshold:
            self._app.tray.set_state(
                AppState.IDLE,
                _i18n_t("state.dictation_pipeline.no_speech_check_mic"),
            )
            self._app.tray.notify(
                APP_NAME,
                "No speech was detected and audio was near-silence.\n"
                "Your microphone may not be capturing audio.\n"
                "Check that the correct mic is selected and is active.",
            )
        else:
            # Long recording with real audio but the engine returned
            log.warning(
                "[TRANSCRIBE] Long recording (%.1fs) with audio "
                "(rms=%.4f) produced empty transcription, engine "
                "returned no text (cycle=%s)",
                self._duration,
                self._recorded_rms,
                self._cycle_id,
            )
            self._app.tray.set_state(
                AppState.IDLE,
                _i18n_t("state.dictation_pipeline.transcription_empty"),
            )
            self._app.tray.notify(
                APP_NAME,
                "Audio was recorded but no transcription was produced.\n"
                "This can happen if the model is misconfigured or the "
                "audio is unclear. Try again, or check the log file for "
                "details.",
            )
        # busy = False) instead of the raw inverted _busy_event.
        self._app._busyness.set_idle()
        self._app._schedule_timer(2.0, lambda: self._app.tray.set_state(AppState.IDLE))
