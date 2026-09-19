"""Dictation pipeline orchestrator: start/stop/cancel through stage steps."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from voice_typer.server.branding import APP_NAME
from voice_typer.server.dictation_pipeline.helpers import (
    _friendly_transcription_error,
    _timed_stage,
)
from voice_typer.server.dictation_stages import (
    PipelineContext,
    _PipelineAbortCancelled,
    _PipelineAbortEmpty,
    build_default_stages,
)
from voice_typer.server.duration import format_duration
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class _OrchestratorMixin:
    """Mixin: ``__init__``, ``request_abort``, and ``run`` orchestration."""

    # Pipeline-side cap on how long the dictation thread will wait for
    _LLM_POLISH_PIPELINE_TIMEOUT_S: float = 4.0

    # Lazily-built 11-stage template shared across all pipeline
    _SHARED_STAGES: list | None = None

    # Step methods owned by ``_TranscribeStepMixin`` and reached via
    _check_resources_throttled: Callable[[], None]
    _hide_or_idle_bubble: Callable[..., None]

    def __init__(self, app: Any):
        self._app = app
        self._cycle_id = ""
        self._audio = None
        self._duration = 0.0
        self._recorded_rms = 0.0
        self._device_info = ""
        # Throttle _check_resources to once per 60s. The values
        self._last_resources_check_ts: float = 0.0
        self._resources_check_interval: float = 60.0
        # pre-computed (rms, peak, silence_pct) from
        self._audio_stats: tuple[float, float, float] | None = None
        # Compact quality summary captured from the active ASR engine
        self._quality_summary: dict[str, float] | None = None
        #  (defense-in-depth observability): tracks whether
        self._templates_applied: bool = False
        # the 11-stage dictation pipeline. Each stage is a thin
        if _OrchestratorMixin._SHARED_STAGES is None:
            _OrchestratorMixin._SHARED_STAGES = build_default_stages()
        self._stages: list = list(_OrchestratorMixin._SHARED_STAGES)

    def request_abort(self) -> None:
        """watchdog's ``_busy_event.set()``) is independent."""
        try:
            active = self._app.models.active_transcriber()
        except Exception:
            log.debug("[PIPELINE] request_abort: could not read active transcriber", exc_info=True)
            return
        if active is None:
            return
        if not hasattr(active, "request_abort"):
            return
        try:
            active.request_abort()
        except Exception:
            log.debug("[PIPELINE] request_abort: engine.request_abort() raised (non-fatal)", exc_info=True)

    def run(
        self,
        audio,
        duration: float,
        recorded_rms: float,
        cycle_id: str,
    ) -> None:
        """This is the entry point called from VoiceTyperApp._stop_dictation."""
        self._audio = audio
        self._duration = duration
        self._recorded_rms = recorded_rms
        self._cycle_id = cycle_id
        # Write an in-flight sentinel so crash_recovery can detect
        with contextlib.suppress(Exception):
            from voice_typer.server._paths import config_dir as _config_dir
            from voice_typer.server.secure_file_io import _secure_atomic_write

            _sentinel = _config_dir() / ".dictation-in-flight"
            _secure_atomic_write(_sentinel, str(cycle_id), durability=False)
        # publish cycle_id as the correlation id for this thread's
        from voice_typer.server.log import set_correlation_id

        if cycle_id:
            _corr_token = set_correlation_id(cycle_id)
        # capture the pre-computed audio stats from the
        self._audio_stats = getattr(self._app.recorder, "_last_audio_stats", None)
        # Reset the per-cycle quality summary BEFORE transcription so a
        self._quality_summary = None
        _t0 = time.perf_counter()

        # Hoist ``text = ""`` outside the try block so the
        text = ""

        try:
            log.info("[TRANSCRIBE] Starting transcription... (cycle=%s)", self._cycle_id)

            # PRE-FLIGHT: resource health check, provides diagnostic
            self._check_resources_throttled()

            # per-stage timing instrumentation.
            _timings: dict[str, float] = {}

            # Lazily rebuild the stage list if a test bypassed

            stages = getattr(self, "_stages", None) or build_default_stages()
            ctx = PipelineContext(
                cycle_id=self._cycle_id,
                audio=self._audio,
                app=self._app,
                pipeline=self,
            )
            # ``text = ""`` was hoisted to before the try block
            for stage in stages:
                if getattr(stage, "timed", True):
                    with _timed_stage(_timings, stage.name):
                        text = stage.run(text, ctx)
                else:
                    text = stage.run(text, ctx)
                # Step 1's post-stage logging is unique (it reports the
                if stage.name == "transcribe":
                    _elapsed = time.perf_counter() - _t0
                    log.info(
                        "[TRANSCRIBE] Transcription complete (len=%d | cycle=%s)%s",
                        len(text) if text else 0,
                        self._cycle_id,
                        format_duration(_elapsed),
                    )
                    log.debug(
                        "[PIPE-PERF] transcribe: %.0f ms (cycle=%s)",
                        _timings.get("transcribe", 0.0),
                        self._cycle_id,
                    )
                    # zero and release both audio references
                    try:
                        if self._audio is not None and isinstance(self._audio, np.ndarray):
                            self._audio.fill(0)
                    except Exception:
                        log.debug(
                            "[PIPELINE] post-transcribe audio zero failed",
                            exc_info=True,
                        )
                    self._audio = None
                    ctx.audio = None

            _total_ms = (time.perf_counter() - _t0) * 1000
            log.info(
                "[PIPE-PERF] total=%.0fms | stages: transcribe=%.0f, clean=%.0f, "
                "vocab=%.0f, templates=%.0f, punct=%.0f, store=%.0f, "
                "paste=%.0f | cycle=%s",
                _total_ms,
                _timings.get("transcribe", 0.0),
                _timings.get("clean", 0.0),
                _timings.get("vocab", 0.0),
                _timings.get("templates", 0.0),
                _timings.get("punct", 0.0),
                _timings.get("store", 0.0),
                _timings.get("paste", 0.0),
                self._cycle_id,
            )
            if _timings.get("llm", 0.0) > 1:
                log.info(
                    "[PIPE-PERF] llm_polish=%.0fms, ai_enhance=%.0fms, vocab_auto=%.0fms | cycle=%s",
                    _timings.get("llm", 0.0),
                    _timings.get("ai", 0.0),
                    _timings.get("vocab_auto", 0.0),
                    self._cycle_id,
                )

        except _PipelineAbortEmpty:
            # ``EmptyCheckStage`` already called
            pass
        except _PipelineAbortCancelled:
            #  ``CancellationGuard`` (wrapping
            pass
        except Exception as e:
            log.exception("[TRANSCRIBE] Transcription FAILED (cycle=%s)", self._cycle_id)
            #  surface the failure in the bubble instead
            try:
                self._app._waveform_bubble.set_state("error")

                def _bubble_error_to_idle() -> None:
                    self._hide_or_idle_bubble("bubble error->idle transition")

                self._app._schedule_timer(3.0, _bubble_error_to_idle)
            except Exception:
                log.debug("[PIPELINE] bubble set_state('error') on failure failed", exc_info=True)
            # The tooltip shows the same user-friendly reason as the
            self._app.tray.set_state(AppState.ERROR, _friendly_transcription_error(e))
            self._app.tray.notify(
                APP_NAME,
                _friendly_transcription_error(e),
            )
            self._app._schedule_timer(3.0, lambda: self._app.tray.set_state(AppState.IDLE))
            # Save the partial transcription to crash recovery
            if text and getattr(self._app.config, "crash_recovery_enabled", False):
                with contextlib.suppress(Exception):
                    self._app._crash_recovery.add(text, pasted=False, cycle_id=self._cycle_id)
                    self._app._crash_recovery.flush(timeout=0.5)

        finally:
            # Each cleanup step below is delegated to a named helper that
            self._cleanup_sentinel_unlink()
            self._cleanup_audio_zero()
            self._cleanup_watchdog_reset()
            self._cleanup_streaming_session_cancel()
            self._cleanup_busyness_idle()
            self._cleanup_transcription_thread_clear()
            self._cleanup_gc_collect()
            log.debug("[TRANSCRIBE] busy reset to False (cycle=%s)", self._cycle_id)
            # clear the correlation id published at the top of run().
            if _corr_token is not None:
                from voice_typer.server.log import reset_correlation_id

                reset_correlation_id(_corr_token)

    # Each helper below owns one of the 7 cleanup steps that ``run()``'s

    def _cleanup_sentinel_unlink(self) -> None:
        """Finally-block step 1: clear the in-flight sentinel."""
        try:
            from voice_typer.server._paths import config_dir as _config_dir

            _sentinel = _config_dir() / ".dictation-in-flight"
            if _sentinel.exists():
                _sentinel.unlink()
        except Exception:
            log.debug(
                "[PIPELINE] finally cleanup step sentinel_unlink failed",
                exc_info=True,
            )

    def _cleanup_audio_zero(self) -> None:
        """Finally-block step 2: zero the audio array after transcription.

        SEC-audit-008: Zero the audio array after transcription completes
        """
        try:
            if self._audio is not None and isinstance(self._audio, np.ndarray):
                self._audio.fill(0)
                self._audio = None
        except Exception:
            log.debug(
                "[PIPELINE] finally cleanup step audio_zero failed",
                exc_info=True,
            )

    def _cleanup_watchdog_reset(self) -> None:
        """RACE-013: reset the persistent watchdog thread (signal that
        RACE-016: wrap daemon thread finally block with try/except to
        """
        try:
            # Phase 2: fixed typo, was `_recording_controller`
            recording = getattr(self._app, "recording", None)
            if recording is not None:
                recording._reset_watchdog()
                recording._stop_watchdog_thread()
                # discard this cycle from the cancelled set so
                _cancelled_lock = getattr(recording, "_cancelled_cycle_ids_lock", None)
                _cancelled_set = getattr(recording, "_cancelled_cycle_ids", None)
                if _cancelled_lock is not None and _cancelled_set is not None:
                    with _cancelled_lock:
                        _cancelled_set.discard(self._cycle_id)
        except Exception:
            log.debug(
                "[PIPELINE] finally cleanup step watchdog_reset failed",
                exc_info=True,
            )

    def _cleanup_streaming_session_cancel(self) -> None:
        """Finally-block step 4: cancel any active streaming session."""
        try:
            session = self._app.recording.pop_streaming_session()
            if session is not None and not self._app.recorder.recording:
                try:
                    session.cancel()
                except Exception:
                    log.debug(
                        "[PIPELINE] finally cleanup step streaming_session_cancel failed",
                        exc_info=True,
                    )
        except Exception:
            log.debug("[TRANSCRIBE] finally: session cleanup failed", exc_info=True)

    def _cleanup_busyness_idle(self) -> None:
        """``_busy_event`` - the inverted legacy primitive stays internal"""
        try:
            self._app._busyness.set_idle()
        except Exception:
            log.debug(
                "[PIPELINE] finally cleanup step busyness idle failed",
                exc_info=True,
            )

    def _cleanup_transcription_thread_clear(self) -> None:
        """Finally-block step 6: clear ``_transcription_thread``."""
        _recording = getattr(self._app, "recording", None)
        _watchdog_lock = getattr(_recording, "_watchdog_lock", None) if _recording is not None else None
        try:
            if _watchdog_lock is not None:
                with _watchdog_lock:
                    _recording._transcription_thread = None
            else:
                # Defensive: very old or stub app without
                raise AttributeError("recording._watchdog_lock not present")
        except Exception:
            # Defensive: if the lock is unavailable we still want
            log.debug(
                "[TRANSCRIBE] could not acquire recording._watchdog_lock "
                "to clear _transcription_thread; assigning without lock",
                exc_info=True,
            )
            try:
                if _recording is not None:
                    _recording._transcription_thread = None
            except Exception:
                log.debug(
                    "[PIPELINE] finally cleanup step transcription_thread_clear_unsafe failed",
                    exc_info=True,
                )

    def _cleanup_gc_collect(self) -> None:
        """Finally-block step 7: generation-0 GC pass."""
        try:
            import gc

            gc.collect(0)
        except Exception:
            log.debug(
                "[PIPELINE] finally cleanup step gc_collect failed",
                exc_info=True,
            )
