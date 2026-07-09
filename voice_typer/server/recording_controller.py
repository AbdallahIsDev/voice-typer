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

from voice_typer.server.branding import APP_NAME
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
    - Own ``self._transcription_thread`` / ``self._streaming_session``
      (ARCH-REFAC-003: callers must read these via ``app.recording.X``)
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
        # RACE-025: toggle serialization lock. Prevents concurrent toggle_dictation
        # calls from different threads (hotkey thread + tray thread) from both
        # passing the _busy_event check before either modifies it.
        self._toggle_lock = threading.Lock()
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
        # RACE-013: persistent watchdog thread + Event instead of chained
        # threading.Timer. Under CPU pressure, chained Timers can stack up
        # (each Timer fires and schedules the next, but the next hasn't
        # started yet so there's no cancellation path). A single persistent
        # thread using Event.wait(timeout=60) is immune to stacking and
        # cheaper than creating a new Timer object every 60s.
        self._watchdog_event = threading.Event()
        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

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

    def pop_streaming_session(self) -> Optional[StreamingTranscriptionSession]:
        """ARCH-018: Atomically get AND clear the streaming session.

        Pre-fix, ``_cancel_streaming_session`` did:
            session = self.get_streaming_session()   # lock acquire/release #1
            self.set_streaming_session(None)          # lock acquire/release #2
        This left a TOCTOU window between the two lock acquisitions: a
        concurrent ``_start_streaming_session_if_enabled`` could install
        a NEW session that the subsequent ``set_streaming_session(None)``
        would clobber — cancelling a session that was just freshly started.

        This method does the get-and-clear under a SINGLE lock
        acquisition, eliminating the race.
        """
        with self._streaming_session_lock:
            session = self._streaming_session
            self._streaming_session = None
            return session

    # ── Toggle / start / stop / cancel ─────────────────────────────────

    def toggle(self) -> None:
        """Toggle recording on/off.

        RACE-025: Serializes concurrent toggle calls from different threads
        (hotkey thread + tray thread) to prevent TOCTOU where two near-
        simultaneous F2 presses both pass the _busy_event check.
        """
        with self._toggle_lock:
            self._toggle_impl()

    def _toggle_impl(self) -> None:
        """Inner toggle implementation, called under _toggle_lock."""
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
        loader = app.models._model_load_thread
        if loader is not None and loader.is_alive():
            log.info(
                "[HOTKEY FIRED] Model still loading -- queuing dictation (cycle=%s)",
                app._cycle_id,
            )
            app.models._pending_dictation = True
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

        # NEW-PRIV-009 (revised): Enforce voice_biometric_consent before
        # capturing any audio. The config field and Electron UI toggle
        # existed previously, but the audio pipeline never checked the
        # flag — meaning the consent was a UI decoration with zero
        # enforcement. Now we refuse to start recording if the user has
        # not explicitly consented to voice biometric processing.
        #
        # This is a GDPR Art. 9 requirement for processing biometric
        # data (voice is biometric). The default is False — the user
        # MUST opt in via the Settings UI before any recording happens.
        # See FORENSIC_REVIEW_COMPLETE.md → NEW-PRIV-009.
        try:
            if not getattr(app.config, "voice_biometric_consent", False):
                log.warning(
                    "[DICTATION] Refusing to start recording - voice_biometric_consent "
                    "is False. User must enable it in Settings > Privacy."
                )
                try:
                    app.tray.set_state(AppState.ERROR, "Voice biometric consent required")
                    app.tray.notify_safety(
                        APP_NAME,
                        "Voice biometric consent is required to start recording.\n"
                        "Enable it in Settings > Privacy > Voice Biometric Consent.",
                    )
                except Exception:
                    log.debug("[DICTATION] failed to notify about missing consent", exc_info=True)
                return
        except Exception:
            # If we can't read the config, fail open (allow recording)
            # to avoid locking the user out of their own app due to a
            # config read error. Log the failure for diagnosis.
            log.exception("[DICTATION] Failed to check voice_biometric_consent - failing open")

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
            # ARCH-ESC-001: mark the recording subsystem as the keyboard
            # owner. The ESC cancel hotkey will fire normally during a
            # recording (it's the only way to cancel). When recording
            # stops, ownership returns to "normal".
            try:
                from voice_typer.server.keyboard_ownership import keyboard_ownership

                keyboard_ownership().set_owner(
                    "recording", reason=f"recording started (cycle={app._cycle_id})"
                )
            except Exception:
                log.debug(
                    "[DICTATION] failed to set keyboard ownership on start",
                    exc_info=True,
                )
            # NEW-IPC-002: emit recording_started push event so the
            # renderer can proactively refresh UI (Home/Dashboard/History)
            # SOUND-FIX-004: log push failures instead of silently
            # swallowing them — a failed push means the renderer never
            # hears about recording_started, so the sound cue won't play
            # and the user gets no audible feedback.  This must be visible.
            try:
                from voice_typer.server.ipc_server import _push_event_now
                _push_event_now({"type": "recording_started"})
            except Exception:
                log.warning(
                    "[SOUND] failed to push recording_started event",
                    exc_info=True,
                )
        except Exception as e:
            log.exception("[DICTATION] Failed to start recording: %s", e)
            self._cancel_streaming_session()
            app.tray.set_state(AppState.ERROR, "Recording failed")
            app.tray.notify(
                APP_NAME,
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
        # NEW-IPC-002: emit recording_stopped push event
        # SOUND-FIX-004: log push failures (see comment in start() above).
        try:
            from voice_typer.server.ipc_server import _push_event_now
            _push_event_now({"type": "recording_stopped"})
        except Exception:
            log.warning(
                "[SOUND] failed to push recording_stopped event",
                exc_info=True,
            )

        # ARCH-ESC-001: recording is stopping — release keyboard ownership
        # back to "normal" so the ESC cancel hotkey stops firing. This
        # MUST happen before recorder.stop() so that any key events
        # processed during the stop sequence see the correct owner.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            keyboard_ownership().set_owner(
                "normal", reason=f"recording stopped (cycle={app._cycle_id})"
            )
        except Exception:
            log.debug(
                "[DICTATION] failed to reset keyboard ownership on stop",
                exc_info=True,
            )

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
            # NEW-UX-018: critical — bypass the notification toggle
            # (dictation failed, the user must be told even if they
            # disabled normal notifications).
            app.tray.notify_safety(APP_NAME, f"Could not stop recording.\n{e}")
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

        # RACE-013: Start persistent watchdog thread using Event.wait(timeout=60).
        # Replaces chained threading.Timer which could stack under CPU pressure.
        # The watchdog thread waits on _watchdog_event with a 60s timeout.
        # If the transcription completes normally, _reset_watchdog() is called
        # from the pipeline's finally block, setting the event so wait() returns
        # immediately and the loop resets. If wait() times out (transcription
        # hung), the watchdog fires the recovery action.
        self._start_watchdog_thread()

        _captured_cycle_id = app._cycle_id

        # ARCH-006: transcribe_thread extracted to DictationPipeline class.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        # Capture the current watchdog reference for the pipeline's finally block
        _watchdog_thread_ref = self._watchdog_thread

        def transcribe_thread():
            pipeline = DictationPipeline(app)
            pipeline.run(
                audio=audio,
                duration=duration,
                recorded_rms=recorded_rms,
                cycle_id=_captured_cycle_id,
                watchdog=None,  # RACE-013: no longer using Timer-based watchdog
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

        # ESC-FIX-001: If no recording is active, the ESC cancel is a no-op.
        # The global ESC hotkey backend fires on every Escape press regardless
        # of whether a recording is in progress.  Early-return here avoids
        # spurious CANCEL logs (which look like errors to the user) and
        # prevents unnecessary cleanup (streaming session cancel, volume
        # restore, bubble hide) when nothing is running.
        #
        # ARCH-ESC-001: in addition to the recorder.recording check, we
        # also consult the KeyboardOwnership singleton. Even if
        # recorder.recording is True (e.g. stale state from a previous
        # session that wasn't cleaned up), if the frontend is in hotkey
        # capture mode we MUST NOT fire cancel — the frontend owns the
        # keyboard during capture.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            if keyboard_ownership().is_hotkey_capture_active():
                log.debug(
                    "[CANCEL] ESC ignored — frontend hotkey capture active (cycle=%s)",
                    app._cycle_id,
                )
                return
        except Exception:
            log.debug("[CANCEL] keyboard ownership check failed", exc_info=True)

        if not app.recorder.recording:
            log.debug("[CANCEL] Cancel pressed but no recording active (cycle=%s) — no-op", app._cycle_id)
            return

        log.info("[CANCEL] Cancelling current dictation (cycle=%s)", app._cycle_id)

        # ARCH-ESC-001: release keyboard ownership back to "normal" so
        # subsequent Escape presses during the cancel cleanup don't
        # re-enter the cancel path.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            keyboard_ownership().set_owner(
                "normal", reason=f"recording cancelled (cycle={app._cycle_id})"
            )
        except Exception:
            log.debug(
                "[CANCEL] failed to reset keyboard ownership on cancel",
                exc_info=True,
            )
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
                APP_NAME,
                "No audio detected. Check your microphone is connected and working.",
            )
        except Exception:
            pass

    def on_silence_auto_stop(self) -> None:
        """Handle silence auto-stop from recorder."""
        log.warning("[DICTATION] Silence auto-stop: stopping recording due to prolonged silence")
        try:
            self._app.tray.notify_safety(
                APP_NAME,
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
                APP_NAME,
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
                    f"{APP_NAME} — Audio Issues",
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
        return self._app.config.streaming_transcription

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
        """Cancel any active hidden streaming session.

        ARCH-018: uses ``pop_streaming_session()`` (atomic get-and-clear)
        instead of the pre-fix get-then-set sequence that had a TOCTOU
        window between the two lock acquisitions.
        """
        session = self.pop_streaming_session()
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

        RACE-013: re-arming no longer creates a new Timer. The persistent
        watchdog thread loops on Event.wait(timeout=60). When it fires
        without a reset, it calls this method. If we decide not to
        force-recover yet, we simply let the loop continue (the event
        is still unset, so the next wait(timeout=60) will time out
        again after 60s).

        TRANSCRIBE-NOTIFY-FIX: the notification "Transcription is still
        running" was showing even for successful transcriptions that
        simply took longer than 60 seconds (e.g. CPU fallback or longer
        audio clips).  The first watchdog firing (60s) now silently logs
        instead of notifying the user — the notification only fires on
        the SECOND firing (120s+) when the transcription is genuinely
        taking an unusually long time.  The watchdog time for the first
        firing was also raised from 60s to 90s.
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
            # TRANSCRIBE-NOTIFY-FIX: first firing is silent — only notify
            # on the second firing (second notification = 180s+ elapsed)
            # to avoid alarming the user when transcription is simply
            # taking a bit longer than usual.
            if self._watchdog_firings >= 2:
                app.tray.notify(
                    APP_NAME,
                    "Transcription is still running.\n"
                    "Long recordings or CPU fallback can take extra time.",
                )
            # RACE-013: no need to create a new Timer. The persistent
            # watchdog thread will time out again on its next
            # Event.wait(timeout=90) cycle.
            return

        if force:
            log.warning(
                "[RECOVERY] FORCE RECOVER: watchdog fired %d times with "
                "worker still alive; assuming deadlock and resetting state",
                self._watchdog_firings,
            )
        else:
            log.warning("[RECOVERY] FORCE RECOVER: transcription watchdog fired, resetting state")
        # RACE-013: stop the persistent watchdog thread on recovery
        self._stop_watchdog_thread()
        app._busy_event.set()  # busy = False
        app.tray.set_state(AppState.IDLE, "Recovered -- transcription timed out")
        app.tray.notify(
            APP_NAME,
            "Transcription took too long and was cancelled.\n"
            "Press F2 to try again.",
        )
        app._schedule_timer(5.0, lambda: app.tray.set_state(AppState.IDLE))

    # ── Persistent watchdog thread (RACE-013) ───────────────────────────

    def _start_watchdog_thread(self) -> None:
        """Start or reset the persistent watchdog thread.

        RACE-013: replaces the old chained threading.Timer pattern. A
        single daemon thread loops on ``_watchdog_event.wait(timeout=60)``.
        When transcription completes normally, ``_reset_watchdog()`` sets
        the event, causing wait() to return early and the loop to reset
        firings + clear the event for the next cycle. When wait() times
        out (transcription hung), the watchdog fires the recovery action.
        """
        with self._watchdog_lock:
            self._watchdog_firings = 0
        # Clear any previous reset signal
        self._watchdog_event.clear()
        # If the thread is already running, just reset the counter
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_stop_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="TranscriptionWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        """Persistent watchdog loop — runs on the watchdog daemon thread.

        TRANSCRIBE-NOTIFY-FIX: initial timeout increased from 60s to 90s
        to reduce false-positive "transcription is still running"
        notifications for longer recordings or CPU fallback scenarios.
        """
        while not self._watchdog_stop_event.is_set():
            # Wait up to 90s. Returns True if the event was set (reset),
            # False if it timed out (transcription hung).
            timed_out = not self._watchdog_event.wait(timeout=90.0)
            if self._watchdog_stop_event.is_set():
                return
            if timed_out:
                with self._watchdog_lock:
                    self._watchdog_firings += 1
                    firings = self._watchdog_firings
                self._force_recover_from_stuck_transcription(
                    force=firings >= self._watchdog_max_firings,
                )
                # If force-recovery happened, the watchdog thread is
                # stopped by _stop_watchdog_thread() inside
                # _force_recover_from_stuck_transcription. Break out.
                if self._watchdog_stop_event.is_set():
                    return
            else:
                # Event was set (transcription completed or reset).
                # Reset firings and clear the event for the next cycle.
                with self._watchdog_lock:
                    self._watchdog_firings = 0
                self._watchdog_event.clear()

    def _reset_watchdog(self) -> None:
        """Signal the watchdog that transcription completed normally.

        Called from the pipeline's finally block. Setting the event
        causes the watchdog's Event.wait() to return True immediately,
        which resets the firing counter.
        """
        self._watchdog_event.set()

    def _stop_watchdog_thread(self) -> None:
        """Stop the persistent watchdog thread."""
        self._watchdog_stop_event.set()
        self._watchdog_event.set()  # break out of wait()
