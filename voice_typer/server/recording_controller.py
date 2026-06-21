"""#2 (Round 9): RecordingController — extracted from VoiceTyperApp.

Owns the recording lifecycle: toggle/start/stop/cancel, silence/xrun
callbacks, and the streaming session management that runs alongside
recording.

Previously this concern lived in VoiceTyperApp as ~400 LOC across these
methods:
    toggle_dictation, _start_dictation, _stop_dictation, _cancel_dictation,
    _on_recorder_rms, _on_silence_warning, _on_silence_auto_stop,
    _on_max_duration_auto_stop, _on_xrun_threshold,
    _streaming_enabled, _streaming_config, _start_streaming_session_if_enabled,
    _cancel_streaming_session, _force_recover_from_stuck_transcription,
    _get_streaming_session, _set_streaming_session

All of those now live here. VoiceTyperApp keeps thin delegate methods
(``app.toggle_dictation()``, ``app._start_dictation()``, etc.) for
back-compat with callers (hotkey backend, tray menu, IPC, tests).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


class RecordingController:
    """Owns recording lifecycle + streaming session + silence/xrun callbacks.

    #2 (Round 9): extracted from VoiceTyperApp. The app passes itself
    (``app``) so RecordingController can:
    - Read ``app.config`` (recording_mode, streaming_*, silence_*)
    - Read/write ``app.recorder`` (Recorder instance)
    - Read/write ``app._busy_event`` (busy flag)
    - Read/write ``app._transcription_thread``
    - Update ``app.tray`` state during recording
    - Call ``app._schedule_timer`` / ``app._cancel_pending_timers``
    - Call ``app.models.ensure_active_engine_loaded()`` / ``app._fallback_to_whisper()``
    - Call ``app._get_active_transcriber()``
    - Call ``app._duck_volume()`` / ``app._restore_volume()``
    - Call ``app._waveform_bubble`` show/hide/reset_level
    - Call ``app._audio_quality.reset()`` / ``app._finalize_audio_quality_report()``
    - Read ``app._cycle_id`` / increment ``app._cycle_counter``
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._streaming_session: Optional[StreamingTranscriptionSession] = None
        self._transcription_thread: Optional[threading.Thread] = None
        # ERR-002: watchdog firing counter for the current transcription
        # cycle. Reset to 0 whenever a new transcription thread starts.
        # After _watchdog_max_firings consecutive watchdog expirations
        # with the worker still alive, we force-recover instead of
        # re-arming — otherwise a genuinely deadlocked ctranslate2 call
        # leaves the app stuck busy forever.
        self._watchdog_firings = 0
        self._watchdog_max_firings = 3
        self._watchdog_lock = threading.Lock()
        # ARCH-018: dedicated lock for _streaming_session. Previously
        # the accessors claimed "thread-safe" but weren't — concurrent
        # start()/cancel() calls could see torn reads or trigger
        # duplicate add_final callbacks.
        self._streaming_session_lock = threading.Lock()

    # ── Streaming session accessors ────────────────────────────────────

    def get_streaming_session(self) -> Optional[StreamingTranscriptionSession]:
        """Thread-safe accessor for the active streaming session.

        ARCH-018: now guarded by ``_streaming_session_lock``.
        """
        with self._streaming_session_lock:
            return self._streaming_session

    def set_streaming_session(self, session_or_none: Optional[StreamingTranscriptionSession]) -> None:
        """Thread-safe setter for the active streaming session.

        ARCH-018: now guarded by ``_streaming_session_lock``.
        """
        with self._streaming_session_lock:
            self._streaming_session = session_or_none

    # ── Toggle / start / stop / cancel ─────────────────────────────────

    def toggle(self) -> None:
        """Toggle recording on/off."""
        app = self._app
        # Generate cycle correlation ID for this dictation
        app._cycle_counter += 1
        app._cycle_id = f"#{app._cycle_counter}"

        active = app._get_active_transcriber()
        model_loaded = active is not None and active.is_loaded
        log.info(
            "[HOTKEY FIRED] toggle_dictation called "
            "(recording=%s, busy=%s, model_loaded=%s, thread=%s, cycle=%s)",
            app.recorder.recording, app._busy_event.is_set(),
            model_loaded,
            threading.current_thread().name,
            app._cycle_id,
        )
        if not app._busy_event.is_set():  # busy
            log.warning("[F2 BLOCKED] Busy transcribing, ignoring toggle (cycle=%s)", app._cycle_id)
            return

        # Model still loading in the background (post-fast-startup).  Queue
        # the request and let the loader auto-start recording once it
        # finishes.  This makes F2 feel responsive even on a cold boot.
        #
        # Capture the loader reference ONCE: the background loader's finally
        # block sets self._model_load_thread = None, and since toggle_dictation
        # runs on the hotkey thread while the loader runs on its own thread,
        # re-reading the attribute in the is_alive() check is a TOCTOU race
        # that can dereference None.
        loader = app._model_load_thread
        if loader is not None and loader.is_alive():
            log.info(
                "[HOTKEY FIRED] Model still loading -- queuing dictation (cycle=%s)",
                app._cycle_id,
            )
            app._pending_dictation = True
            app.tray.set_state(
                AppState.LOADING,
                "Loading model -- your dictation will start automatically…",
            )
            return

        if active is None:
            app.tray.set_state(AppState.LOADING, "Starting up -- please wait...")
            return

        if app.recorder.recording:
            # #2 (Round 9): Call app._stop_dictation (which delegates to
            # self.stop()) so tests that monkeypatch app._stop_dictation
            # still intercept the call.
            app._stop_dictation()
        else:
            app._start_dictation()

    def start(self) -> None:
        """Start a recording session."""
        app = self._app
        if app.recorder.recording:
            log.info("[DICTATION] _start_dictation: already recording, no-op")
            return

        # Cancel any stale pending timers from previous sessions
        app._cancel_pending_timers()

        # ERR-003: if a model change was deferred during the previous
        # recording, apply it now (loads the new backend before we start
        # capturing audio). Without this, the user's "change to medium
        # after current recording" would silently never happen.
        try:
            app.models.apply_pending_model_change()
        except Exception:
            log.exception("[DICTATION] Failed to apply pending model change; continuing")

        # Lazy-init engines if backend was changed via Electron UI after startup.
        # #2 (Round 9): ModelManager handles the lazy-init + registry sync.
        app.models.ensure_active_engine_loaded()
        active = app._get_active_transcriber()

        if active is None or not getattr(active, 'is_loaded', False):
            # No engine loaded -- try to load whisper as a fallback
            log.warning("[DICTATION] No loaded engine found, lazy-loading Whisper as fallback")
            app._fallback_to_whisper(notify_on_failure=True)
            active = app._get_active_transcriber()
            if active is None or not getattr(active, 'is_loaded', False):
                log.error("[DICTATION] Whisper fallback also failed, cannot record")
                app._schedule_timer(
                    3.0, lambda: app.tray.set_state(
                        AppState.ERROR, "Model failed to load -- press F2 to retry"
                    )
                )
                return

        log.info("[DICTATION] Starting recording... (cycle=%s)", app._cycle_id)
        try:
            # H12: Wire silence detection callbacks
            app.recorder.on_silence_warning = self.on_silence_warning
            app.recorder.on_silence_auto_stop = self.on_silence_auto_stop
            app.recorder.on_max_duration_auto_stop = self.on_max_duration_auto_stop

            # Waveform bubble: feed RMS levels from the audio callback
            app.recorder.on_rms_level = self.on_recorder_rms

            # Reset audio-quality analyzer accumulators so per-chunk
            # statistics don't carry over from the previous session.
            try:
                app._audio_quality.reset()
            except Exception:
                log.debug("[AUDIO_QUALITY] reset on start failed", exc_info=True)

            app.recorder.start()
            self._start_streaming_session_if_enabled()
            app.tray.set_state(AppState.RECORDING, "Recording...")
            # Show the floating bubble once we know the stream is open
            app._waveform_bubble.show()
            # Duck system volume AFTER recording starts so the first
            # chunk of audio benefits from the ducked speakers.
            app._duck_volume()
            log.info("[DICTATION] Recording started OK (cycle=%s)", app._cycle_id)
        except Exception as e:
            log.exception("[DICTATION] Failed to start recording: %s", e)
            self._cancel_streaming_session()
            app.tray.set_state(AppState.ERROR, "Recording failed")
            app.tray.notify(
                "Voice Typer",
                f"Could not start recording.\n{e}\n\n"
                "Check voice-typer.log for traceback.",
            )
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))

    def stop(self) -> None:
        """Stop recording and transcribe in background."""
        app = self._app
        if not app.recorder.recording:
            log.info("[DICTATION] _stop_dictation: not recording, no-op")
            return

        # Cancel any stale pending timers
        app._cancel_pending_timers()

        log.info("[DICTATION] Stopping recording... (cycle=%s)", app._cycle_id)

        app._busy_event.clear()  # busy = True

        # Detach the RMS callback and hide the bubble so the audio path
        # cannot keep pushing levels after the stream is closed.
        app.recorder.on_rms_level = None
        # Push a final zero-level event so the renderer resets its animation
        # envelope. Without this, the dots stay frozen at their last active
        # height because rawLevelRef is never set back to 0.
        app._waveform_bubble.reset_level()
        # Hide bubble unless always_visible mode (bubble stays on screen)
        if app.config.bubble_behavior != 'always_visible':
            app._waveform_bubble.hide()

        try:
            audio = app.recorder.stop()
        except Exception as e:
            log.exception("[DICTATION] Failed to stop recording")
            self._cancel_streaming_session()
            app._restore_volume()
            app.tray.set_state(AppState.ERROR, "Stop failed")
            app.tray.notify("Voice Typer", f"Could not stop recording.\n{e}")
            app._busy_event.set()  # busy = False
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        # Restore system volume immediately — don't wait for transcription
        # (which takes seconds) before the user gets their audio back.
        app._restore_volume()

        # Audio has already been resampled to config.sample_rate by Recorder.stop()
        duration = len(audio) / app.config.sample_rate if len(audio) > 0 else 0
        # Capture RMS before starting transcription thread (race-safe)
        recorded_rms = app.recorder.last_rms

        # Run the revived AudioQualityAnalyzer on the captured audio.
        if duration > 0:
            try:
                app._finalize_audio_quality_report(audio)
            except Exception:
                log.debug("[AUDIO_QUALITY] finalize failed", exc_info=True)
        log.info(
            "[DICTATION] Recording stopped -- %.1fs of audio, busy=True (cycle=%s)",
            duration, app._cycle_id,
        )

        if duration < 0.5:
            log.info("[DICTATION] Audio too short, skipping transcription")
            self._cancel_streaming_session()
            app.tray.set_state(AppState.IDLE, "Too short -- ignored")
            app._busy_event.set()  # busy = False
            app._schedule_timer(2.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        log.info("[DICTATION] Starting transcription thread... (cycle=%s)", app._cycle_id)
        app.tray.set_state(AppState.TRANSCRIBING, "Transcribing...")

        # ERR-002: reset watchdog counter for this transcription cycle.
        with self._watchdog_lock:
            self._watchdog_firings = 0

        # PERF-NEW-005: signal the streaming session to cancel BEFORE
        # starting the final transcription thread.
        session = self.get_streaming_session()
        if session is not None:
            try:
                session._cancel_event.set()
            except Exception:
                pass

        # Safety watchdog: if transcription hangs for >60s, force-recover.
        # ERR-002: re-arm up to _watchdog_max_firings times. After that,
        # the next firing force-recovers even if the worker is still
        # alive (covers deadlocks inside ctranslate2).
        def _watchdog_fire():
            with self._watchdog_lock:
                self._watchdog_firings += 1
                firings = self._watchdog_firings
            self._force_recover_from_stuck_transcription(force=firings >= self._watchdog_max_firings)

        watchdog = threading.Timer(
            60.0,
            _watchdog_fire,
        )
        watchdog.daemon = True
        # ARCH-017: track the watchdog Timer in the app's _pending_timers
        # list so quit() / _cancel_pending_timers() cancels it on shutdown.
        # Otherwise the timer holds a reference to the closure (which
        # captures `self`) and delays GC; worse, it may fire post-quit
        # and call _force_recover on a half-torn-down app.
        try:
            with app._pending_timers_lock:
                app._pending_timers.append(watchdog)
        except Exception:
            log.debug("[WATCHDOG] could not track in _pending_timers", exc_info=True)
        watchdog.start()

        _captured_cycle_id = app._cycle_id

        # ARCH-006: transcribe_thread extracted to DictationPipeline class.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        def transcribe_thread():
            pipeline = DictationPipeline(app)
            pipeline.run(
                audio=audio,
                duration=duration,
                recorded_rms=recorded_rms,
                cycle_id=_captured_cycle_id,
                watchdog=watchdog,
            )

        self._transcription_thread = threading.Thread(
            target=transcribe_thread,
            name="Transcription",
            daemon=True,
        )
        self._transcription_thread.start()

    def cancel(self) -> None:
        """Feature: ESC to cancel -- cancel current recording/transcription.

        ERR-023: previously, if ``recorder.discard()`` raised (PortAudio
        error, stream close race), the cancel path aborted before
        resetting tray state — leaving the tray stuck on RECORDING.
        We now guarantee the post-discard cleanup always runs.

        ARCH-042: set AppState.CANCELLING for ~200ms during cancel so
        the tray icon shows a distinct "cancelling" state instead of
        instantly transitioning RECORDING → IDLE (which flickers).
        """
        app = self._app
        log.info("[CANCEL] Cancelling current dictation (cycle=%s)", app._cycle_id)
        # ARCH-042: show CANCELLING state immediately.
        try:
            app.tray.set_state(AppState.CANCELLING, "Cancelling...")
        except Exception:
            log.debug("[CANCEL] could not set CANCELLING state", exc_info=True)
        app._cancel_pending_timers()

        if app.recorder.recording:
            try:
                # Detach RMS callback and stop background audio first
                app.recorder.on_rms_level = None
                # Push a final zero-level event to reset the bubble visualizer
                app._waveform_bubble.reset_level()
                app.recorder.discard()
                log.info("[CANCEL] Recording discarded (cycle=%s)", app._cycle_id)
            except Exception as e:
                # ERR-023: don't abort the cancel path — fall through to
                # ensure tray state + busy flag are reset.
                log.exception(
                    "[CANCEL] Failed to discard recording (cycle=%s): %s",
                    app._cycle_id, e,
                )

        # Always run these — even if discard failed.
        try:
            self._cancel_streaming_session()
        except Exception:
            log.exception("[CANCEL] Failed to cancel streaming session")

        # Restore system volume on cancel
        try:
            app._restore_volume()
        except Exception:
            log.exception("[CANCEL] Failed to restore volume")

        # Hide bubble unless always_visible mode
        try:
            if app.config.bubble_behavior != 'always_visible':
                app._waveform_bubble.hide()
        except Exception:
            log.exception("[CANCEL] Failed to hide bubble")

        # ERR-023: tray state + busy flag MUST be cleared so the user
        # can press F2 again after a cancel.
        app.tray.set_state(AppState.IDLE, "Cancelled")
        app._busy_event.set()

    # ── Audio callbacks (wired to Recorder) ────────────────────────────

    def on_recorder_rms(self, rms: float, peak: float, audio_chunk=None) -> None:
        """T021: Forward per-chunk RMS + audio chunk to the bubble.

        Previously this called update_level(rms, peak) with no audio_chunk,
        which meant the Silero VAD gate in WaveformBubble.update_level
        was inert in production (audio_chunk defaulted to None → VAD
        skipped). Now we forward the audio_chunk from the recorder so
        VAD actually fires during real dictation, filtering out ambient
        noise from the visualizer.
        """
        self._app._waveform_bubble.update_level(rms, peak, audio_chunk=audio_chunk)

    def on_silence_warning(self) -> None:
        """Handle silence warning from recorder."""
        log.warning("[DICTATION] Silence warning: no audio detected for a while")
        try:
            self._app.tray.notify_safety(
                "Voice Typer",
                "No audio detected. Check your microphone is connected and working.",
            )
        except Exception:
            pass

    def on_silence_auto_stop(self) -> None:
        """Handle silence auto-stop from recorder."""
        log.warning("[DICTATION] Silence auto-stop: stopping recording due to prolonged silence")
        try:
            self._app.tray.notify_safety(
                "Voice Typer",
                "Recording stopped: no audio detected for an extended period.",
            )
        except Exception:
            pass
        # Must NOT call stop() directly here -- this callback runs
        # inside the audio callback while Recorder._lock is held.  Calling
        # recorder.stop() would deadlock on the same lock.  Schedule it on a
        # separate thread instead.
        # #2 (Round 9): call app._stop_dictation (delegate) instead of
        # self.stop() directly so tests that monkeypatch _stop_dictation
        # still intercept the call.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_max_duration_auto_stop(self) -> None:
        """Handle max duration auto-stop from recorder."""
        log.warning("[DICTATION] Max duration auto-stop: stopping recording")
        try:
            self._app.tray.notify_safety(
                "Voice Typer",
                "Recording stopped: maximum recording duration reached.",
            )
        except Exception:
            pass
        # Same reason as on_silence_auto_stop: avoid deadlock on Recorder._lock.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_xrun_threshold(self, count: int) -> None:
        """Item 1: notify the user when xrun count exceeds threshold."""
        log.warning("[XRUN] Threshold reached: %d xruns", count)
        if self._app.config.show_notifications:
            try:
                self._app.tray.notify(
                    "Voice Typer — Audio Issues",
                    f"Detected {count} audio buffer underruns. "
                    "Try closing other audio apps or reducing CPU load.",
                )
            except Exception:
                pass

    # ── Streaming session ──────────────────────────────────────────────

    def _streaming_enabled(self) -> bool:
        """Return whether hidden streaming should run for the next recording."""
        if os.environ.get("VOICE_TYPER_STREAMING") == "0":
            return False
        # pyrefly: ignore [unnecessary-type-conversion]
        return bool(self._app.config.streaming_transcription)

    def _streaming_config(self) -> StreamingConfig:
        cfg = self._app.config
        return StreamingConfig(
            enabled=self._streaming_enabled(),
            chunk_seconds=cfg.streaming_chunk_seconds,
            step_seconds=cfg.streaming_step_seconds,
            left_overlap_seconds=cfg.streaming_left_overlap_seconds,
            right_guard_seconds=cfg.streaming_right_guard_seconds,
            min_first_chunk_seconds=cfg.streaming_min_first_chunk_seconds,
            silence_threshold=cfg.streaming_silence_threshold,
        )

    def _start_streaming_session_if_enabled(self) -> None:
        """Start hidden streaming work for the active recording if enabled."""
        app = self._app
        self.set_streaming_session(None)
        if not self._streaming_enabled():
            return

        # Streaming requires transcribe_words (word-level timestamps).
        # Only Whisper supports this; skip for Parakeet/Qwen.
        active = app._get_active_transcriber()
        if active is not None:
            log.info(
                "[STREAMING] Checking transcriber: %s has transcribe_words=%s",
                type(active).__name__,
                hasattr(active, "transcribe_words"),
            )
            if not hasattr(active, "transcribe_words"):
                log.info(
                    "[STREAMING] Transcriber lacks transcribe_words, skipping streaming (cycle=%s)",
                    app._cycle_id,
                )
                return
        else:
            log.info("[STREAMING] No active transcriber, skipping streaming (cycle=%s)", app._cycle_id)
            return

        try:
            session = StreamingTranscriptionSession(
                recorder=app.recorder,
                transcriber=app._get_active_transcriber(),
                config=self._streaming_config(),
                sample_rate=app.config.sample_rate,
            )
            session.start()
            self.set_streaming_session(session)
            log.info("[STREAMING] Hidden streaming session started (cycle=%s)", app._cycle_id)
        except Exception as e:
            log.exception("[STREAMING] Failed to start streaming session: %s", e)
            self.set_streaming_session(None)

    def _cancel_streaming_session(self) -> None:
        """Cancel any active hidden streaming session."""
        session = self.get_streaming_session()
        self.set_streaming_session(None)
        if session is not None:
            try:
                session.cancel()
            except Exception:
                log.exception("[STREAMING] Failed to cancel streaming session")

    def _force_recover_from_stuck_transcription(self, force: bool = False) -> None:
        """Safety net: recover from stuck transcription state.

        ERR-002: When the transcription thread is still alive at the
        time the watchdog fires, we used to leave the app busy and
        return. That meant a genuinely deadlocked worker (e.g. ctranslate2
        stuck in CUDA) would never recover. We now re-arm the watchdog
        up to ``_watchdog_max_firings`` times; once the counter exceeds
        the threshold (or ``force=True`` is passed), we unconditionally
        clear the busy flag and reset the tray state.
        """
        app = self._app
        if app._busy_event.is_set():  # not busy
            return  # Already recovered, nothing to do
        if (
            not force
            and self._transcription_thread is not None
            and self._transcription_thread.is_alive()
        ):
            log.warning(
                "Transcription watchdog fired (%d/%d), but worker is still "
                "alive; leaving app busy to avoid overlapping model calls",
                self._watchdog_firings, self._watchdog_max_firings,
            )
            app.tray.set_state(AppState.TRANSCRIBING, "Still transcribing...")
            app.tray.notify(
                "Voice Typer",
                "Transcription is still running.\n"
                "Long recordings or CPU fallback can take extra time.",
            )
            # Re-arm the watchdog for another 60s window.
            def _re_fire():
                with self._watchdog_lock:
                    self._watchdog_firings += 1
                    firings = self._watchdog_firings
                self._force_recover_from_stuck_transcription(
                    force=firings >= self._watchdog_max_firings
                )

            next_watchdog = threading.Timer(60.0, _re_fire)
            next_watchdog.daemon = True
            # ARCH-017: track the re-armed watchdog too.
            try:
                with app._pending_timers_lock:
                    app._pending_timers.append(next_watchdog)
            except Exception:
                log.debug("[WATCHDOG] could not track re-armed timer", exc_info=True)
            next_watchdog.start()
            return

        if force:
            log.warning(
                "[RECOVERY] FORCE RECOVER: watchdog fired %d times with "
                "worker still alive; assuming deadlock and resetting state",
                self._watchdog_firings,
            )
        else:
            log.warning("[RECOVERY] FORCE RECOVER: transcription watchdog fired, resetting state")
        app._busy_event.set()  # busy = False
        app.tray.set_state(AppState.IDLE, "Recovered -- transcription timed out")
        app.tray.notify(
            "Voice Typer",
            "Transcription took too long and was cancelled.\n"
            "Press F2 to try again.",
        )
        app._schedule_timer(5.0, lambda: app.tray.set_state(AppState.IDLE))
