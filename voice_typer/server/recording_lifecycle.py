"""Recording lifecycle, extracted from ``RecordingController``
(Phase 4.5 split).

Owns the toggle / start / stop / cancel state machine and the
stop+transcribe worker entry point. The actual recording flag, busy
event, transcription thread handle, and toggle lock all live on
``RecordingController`` (shared state): this helper operates on them
through a back-reference.

Collaborator pattern
--------------------
:class:`RecordingLifecycle` is constructed by
``RecordingController.__init__`` with NO arguments (stateless). Each
method takes a back-reference to the owning ``RecordingController``
(``controller``) and reads/writes ``controller._app``,
``controller._toggle_lock``, ``controller._transcription_thread``,
``controller._busy_event``, etc.

The public lifecycle methods (``toggle`` / ``start`` / ``stop`` /
``cancel``) acquire ``controller._toggle_lock`` (an RLock) and then
call their own ``_toggle_impl`` / ``_start_impl`` / ``_stop_impl`` /
``_cancel_impl`` directly. There is no hop back through the
controller: production flow is
``lifecycle.toggle → lifecycle._toggle_impl``.

Originally lines 469–1500 of ``recording_controller.py``.
"""

from __future__ import annotations

import contextlib
import logging
import threading

from voice_typer.server import event_bus, i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.duration import format_duration
from voice_typer.server.keyboard_ownership import keyboard_ownership
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


#: Stable ``consent_field`` id the backend publishes for the voice-
VOICE_BIOMETRIC_CONSENT_FIELD = "voice_biometric_consent"


#: Default bounded-join timeout for the DictationStart worker when the
_DEFAULT_START_JOIN_TIMEOUT_S = 0.1


#: The exact RuntimeError message raised by the recording pipeline when
_NO_INPUT_DEVICE_MARKER = "No input device could be opened"


def _recording_start_failure_message(exc: BaseException) -> str:
    """Map a ``recorder.start()`` failure to a safe, user-friendly message."""
    from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

    if isinstance(exc, MicrophonePermissionDeniedError):
        return i18n.t("state.recording_controller.recording_failed_permission")
    if isinstance(exc, RuntimeError) and _NO_INPUT_DEVICE_MARKER in str(exc):
        return i18n.t("state.recording_controller.recording_failed_no_device")
    return i18n.t("state.recording_controller.recording_failed")


def _notify_consent_gate(title: str, message: str) -> bool:
    """Prefer a CLICKABLE host notification over the pystray balloon."""
    try:
        if not event_bus.has_live_transport():
            return False
        ok = event_bus.publish(
            {
                "type": "notification",
                "data": {
                    "title": title,
                    "message": message,
                    "duration_ms": 0,
                    "critical": False,
                    "click_consent_field": VOICE_BIOMETRIC_CONSENT_FIELD,
                },
            }
        )
        if ok:
            log.info("[DICTATION] published clickable consent notification (click -> Settings > Voice Biometric)")
            return True
    except Exception:
        log.debug("[DICTATION] clickable consent notification push failed", exc_info=True)
    return False


def _publish_consent_required_event() -> None:
    """Best-effort publish of a ``consent_required`` push event."""
    try:
        event_bus.publish(
            {
                "type": "consent_required",
                "data": {"consent_field": VOICE_BIOMETRIC_CONSENT_FIELD},
            }
        )
    except Exception:
        log.debug("[DICTATION] consent_required event push failed", exc_info=True)


class RecordingLifecycle:
    """Toggle / start / stop / cancel state machine for recording."""

    def __init__(self) -> None:
        # Stateless helper, all recording state lives on the controller.
        self._public_entry_depth = threading.local()

    def _run_public_entry(self, controller, impl_method) -> None:
        """Run a public lifecycle entry (``toggle`` / ``start``)."""
        outermost = not getattr(self._public_entry_depth, "active", False)
        if outermost:
            self._public_entry_depth.active = True
        prior_start_worker = getattr(controller, "_start_worker_thread", None)
        try:
            with controller._toggle_lock:
                impl_method()
        finally:
            if outermost:
                self._public_entry_depth.active = False
        if not outermost:
            return
        worker = getattr(controller, "_start_worker_thread", None)
        if worker is None or worker is prior_start_worker:
            return
        join_timeout = getattr(controller, "_start_worker_join_timeout", _DEFAULT_START_JOIN_TIMEOUT_S)
        with contextlib.suppress(Exception):
            worker.join(timeout=join_timeout)

    def toggle(self, controller) -> None:
        """Toggle recording on/off.

        RACE-025: Serializes concurrent toggle calls from different threads
        (hotkey thread + tray thread) to prevent TOCTOU where two near-
        simultaneous F2 presses both pass the _busy_event check.

        The lock is acquired exactly once (via ``_run_public_entry``)
        around the toggle decision. When the decision starts a dictation,
        the bounded join of the DictationStart worker happens AFTER the
        lock is released so a concurrent ``stop()`` / ``cancel()"
        (auto-stop timer, ESC hotkey) never waits behind the join.
        """
        self._run_public_entry(controller, lambda: self._toggle_impl(controller))

    def _toggle_impl(self, controller) -> None:
        """Inner toggle implementation, called under _toggle_lock."""
        app = controller._app
        # The cycle counter is NOT incremented here. Pre-fix, every

        active = app.models.active_transcriber()
        model_loaded = active is not None and active.is_loaded
        # ``_busy_event`` is inverted (SET == idle); log the natural
        busy = not app._busy_event.is_set()
        log.info(
            "[HOTKEY FIRED] toggle_dictation called (recording=%s, busy=%s, model_loaded=%s | thread=%s | cycle=%s)",
            app.recorder.recording,
            busy,
            model_loaded,
            threading.current_thread().name,
            app._cycle_id,
        )
        if not app._busy_event.is_set():  # busy
            log.warning("[F2 BLOCKED] Busy transcribing, ignoring toggle (cycle=%s)", app._cycle_id)
            return

        # Model still loading in the background (post-fast-startup). Queue
        loader = app.models._model_load_thread
        if loader is not None and loader.is_alive():
            log.info(
                "[HOTKEY FIRED] Model still loading -- queuing dictation (cycle=%s)",
                app._cycle_id,
            )
            app.models._pending_dictation = True
            app.tray.set_state(
                AppState.LOADING,
                i18n.t("state.recording_controller.loading_queued"),
            )
            return

        if active is None:
            # The previous background model load FAILED (or never
            log.info(
                "[HOTKEY FIRED] No active transcriber and no live loader -- "
                "re-triggering background model load (cycle=%s)",
                app._cycle_id,
            )
            try:
                app.models._pending_dictation = True
                app.models.start_background_load()
                app.tray.set_state(
                    AppState.LOADING,
                    "Retrying model load...",
                )
            except Exception:
                log.exception(
                    "[HOTKEY FIRED] start_background_load re-trigger failed (cycle=%s)",
                    app._cycle_id,
                )
                # Fall back to the original "starting up" message so the
                app.tray.set_state(
                    AppState.LOADING,
                    i18n.t("state.recording_controller.starting_up"),
                )
            return

        # Commit to a real start/stop. NOW increment the cycle counter
        app._cycle_counter += 1
        app._cycle_id = f"#{app._cycle_counter}"

        if app.recorder.recording:
            # Call ``app._stop_dictation`` (which delegates to
            app._stop_dictation()
        else:
            app._start_dictation()

    def start(self, controller) -> None:
        """Start a recording session."""
        self._run_public_entry(controller, lambda: self._start_impl(controller))

    def _gate_voice_biometric_consent(self, app) -> bool:
        """Enforce voice_biometric_consent before capturing any audio.

        Returns True when start may proceed, False when the caller must
        """
        try:
            if getattr(app.config, "voice_biometric_consent", False):
                return True
            log.warning(
                "[DICTATION] Refusing to start recording - voice_biometric_consent "
                "is False. User must enable it in Settings > Privacy."
            )
            try:
                app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.consent_required"))
                # Prefer the CLICKABLE host toast (click → Settings
                if not _notify_consent_gate(
                    APP_NAME,
                    i18n.t("notify.recording_controller.consent_required"),
                ):
                    app.tray.notify_safety(
                        APP_NAME,
                        i18n.t("notify.recording_controller.consent_required"),
                    )
            except Exception:
                log.debug("[DICTATION] failed to notify about missing consent", exc_info=True)
            # Publish the ``consent_required`` push event so the
            _publish_consent_required_event()
            return False
        except Exception:
            # GDPR Art. 9: if we cannot verify voice_biometric_consent
            log.exception(
                "[DICTATION] Failed to check voice_biometric_consent - "
                "failing CLOSED (refusing to record) per GDPR Art. 9"
            )
            try:
                app.tray.set_state(
                    AppState.ERROR,
                    i18n.t("state.recording_controller.consent_required"),
                )
                if not _notify_consent_gate(
                    APP_NAME,
                    "Could not verify voice biometric consent.\nRecording refused. Check Settings > Privacy.",
                ):
                    app.tray.notify_safety(
                        APP_NAME,
                        "Could not verify voice biometric consent.\nRecording refused. Check Settings > Privacy.",
                    )
            except Exception:
                log.debug(
                    "[DICTATION] failed to notify about consent check exception",
                    exc_info=True,
                )
            # Same ``consent_required`` push as the plain-refusal branch
            _publish_consent_required_event()
            return False

    def _wire_recorder_start_callbacks(self, controller, app) -> None:
        """Wire recorder callbacks + pre-start cleanup before open."""
        app.recorder.on_silence_warning = controller.on_silence_warning
        app.recorder.on_silence_auto_stop = controller.on_silence_auto_stop
        app.recorder.on_max_duration_auto_stop = controller.on_max_duration_auto_stop
        # Wire the microphone-permission-revoked callback so the
        with contextlib.suppress(Exception):
            app.recorder.on_microphone_permission_revoked = controller.on_microphone_permission_revoked

        # Waveform bubble: feed RMS levels from the audio callback
        app.recorder.on_rms_level = controller.on_recorder_rms

        # Reset audio-quality analyzer accumulators so per-chunk
        try:
            app._audio_quality.reset()
        except Exception:
            log.debug("[AUDIO_QUALITY] reset on start failed", exc_info=True)

        controller._stop_level_monitor_for_recorder_start()

    def _notify_mic_watcher_of_active_device(self, app) -> None:
        """Tell the mic watcher which mic_id we're recording from."""
        with contextlib.suppress(Exception):
            mic_watcher = getattr(getattr(app.recorder, "_devices", None), "_mic_watcher", None)
            if mic_watcher is not None:
                # The resolved device index (or None for default) is
                resolved = getattr(app.recorder, "_effective_device", None)
                if resolved is None:
                    resolved = app.recorder._devices._resolve_device()
                mic_watcher.set_active_mic_id(resolved)

    def _claim_keyboard_ownership_for_recording(self, app) -> None:
        """Mark the recording subsystem as the keyboard owner."""
        try:
            keyboard_ownership().set_owner("recording", reason=f"recording started (cycle={app._cycle_id})")
        except Exception:
            log.debug(
                "[DICTATION] failed to set keyboard ownership on start",
                exc_info=True,
            )

    def _rearm_esc_cancel_if_enabled(self, app) -> None:
        """ESC-CANCEL-WATCHDOG: re-arm a dead/stale ESC backend on start."""
        try:
            if getattr(app.config, "esc_cancel_enabled", False):
                esc_backend = getattr(app.hotkeys, "_esc_backend", None)
                if esc_backend is None or not esc_backend.is_alive():
                    log.warning(
                        "[DICTATION] ESC cancel backend missing/dead at recording start (backend=%r), re-registering",
                        type(esc_backend).__name__ if esc_backend else "None",
                    )
                    app.hotkeys.register_esc()
        except Exception:
            log.warning(
                "[DICTATION] failed to re-arm ESC cancel hotkey on start",
                exc_info=True,
            )

    def _publish_recording_started_event(self) -> None:
        """Emit ``recording_started`` push so the renderer can refresh UI."""
        try:
            event_bus.publish({"type": "recording_started"})
        except Exception:
            log.warning(
                "[SOUND] failed to push recording_started event",
                exc_info=True,
            )

    def _spawn_dictation_start_worker(self, controller, app) -> None:
        """Spawn the DictationStart daemon worker + publish adaptive join timeout.

        Load / reload the active engine AFTER ``recorder.start()`` so
        the recorder buffers audio while the model reloads (5-30s
        on idle-unload). Pre-fix this ran before ``recorder.start()``
        and the first 5-30s of speech was lost. The transcription
        thread (started in ``_stop_impl``) transcribes the buffered
        audio once the model is ready. If the model fails to load,
        the worker discards the recorder and surfaces an error.

        The model load + post-load steps (active_transcriber check,
        fallback_to_whisper, _start_streaming_session_if_enabled)
        run on a DAEMON WORKER THREAD so the F2 hotkey backend's
        single dispatch thread is NOT blocked for 5-30s on the
        idle-unload reload path. Pre-fix, the lock was released
        for the duration of ``ensure_active_engine_loaded()`` so
        concurrent stop/cancel could proceed, but the F2 thread
        itself still blocked for 5-30s: meaning:
          - The F2 hotkey backend's dispatch thread was occupied
            and could not process a second F2 press (e.g. to stop
            the recording the user just started).
          - On hotkey backends with a single dispatch thread
            (pynput), ALL hotkeys were blocked for 5-30s —
            including ESC cancel.
        The F2 thread now spawns the worker and returns after a
        bounded ``join(timeout=0.1)``: fast enough for tests
        with mocked models (the worker completes in <1ms), slow
        enough to not block the dispatch thread in production
        (5-30s idle-unload reload). The worker is a daemon so it
        doesn't block process exit.

        The worker runs WITHOUT ``_toggle_lock``: the public entry
        (``toggle`` / ``start`` via ``_run_public_entry``) releases
        the lock BEFORE the bounded join, so neither the worker NOR
        the join holds it. The worker doesn't need the lock
        because:
        1. ``ensure_active_engine_loaded()`` has its own internal
           lock (``_lazy_init_lock`` in ``ModelManager``).
        2. The post-load re-check reads atomic state
           (``recorder.recording``, ``_busy_event.is_set()``).
        3. ``_start_streaming_session_if_enabled()`` uses
           ``_streaming_session_lock`` for its own serialization.
        The ``_busy_event`` is NOT cleared by ``_start_impl``
        (``_stop_impl`` clears it), so a concurrent ``stop()``
        that acquires the lock after the public entry releases it
        would see ``busy_event.is_set() == True`` (not busy) and
        proceed, the desired behavior (the user explicitly
        stopped, so the buffered audio should be transcribed as
        soon as the model finishes loading).

        The worker signals ``_start_complete_event`` in its
        finally block so tests that need to assert model-loaded
        state can wait on the event.
        """
        start_complete_event = threading.Event()
        # Publish on the controller so tests can wait on it
        controller._start_complete_event = start_complete_event
        worker = threading.Thread(
            target=controller._lifecycle._start_dictation_worker_entry,
            args=(controller, app._cycle_id, start_complete_event),
            name="DictationStart",
            daemon=True,
        )
        # Store on controller so tests / watchdog can inspect / join.
        controller._start_worker_thread = worker
        worker.start()
        # Bounded wait for the worker to make progress. The timeout
        _pre_load_active = app.models.active_transcriber()
        _pre_load_model_loaded = _pre_load_active is not None and getattr(_pre_load_active, "is_loaded", False)
        _join_timeout = _DEFAULT_START_JOIN_TIMEOUT_S if _pre_load_model_loaded else 2.0
        # Publish the join timeout next to the worker thread so the
        controller._start_worker_join_timeout = _join_timeout

    def _teardown_failed_recorder_start(
        self,
        controller,
        app,
        *,
        restart_level_monitor: bool,
        discard_log_label: str,
    ) -> None:
        """Release the recorder/streaming state after a failed start."""
        controller._cancel_streaming_session()
        # If ``recorder.start()`` succeeded but a later step raised, the
        try:
            app.recorder.discard()
        except Exception:
            log.debug(
                "[DICTATION] recorder.discard() during %s raised (best-effort cleanup)",
                discard_log_label,
                exc_info=True,
            )
        # Force-reset the recording flag so the next ``start()`` call
        app.recorder.recording = False
        if restart_level_monitor:
            # Best-effort, mirroring the stop paths.
            controller._maybe_restart_level_monitor_for_always_visible_bubble(app)

    def _publish_start_failure_notification(self, app, exc: BaseException) -> None:
        """Surface a start failure in tray / toast / push / idle-timer."""
        _start_fail_msg = _recording_start_failure_message(exc)
        app.tray.set_state(AppState.ERROR, _start_fail_msg)
        # Notification mirrors the tooltip: for typed failures the
        if _start_fail_msg != i18n.t("state.recording_controller.recording_failed"):
            app.tray.notify(
                APP_NAME,
                i18n.t("notify.recording_controller.start_failed_with_reason", reason=_start_fail_msg),
            )
        else:
            app.tray.notify(
                APP_NAME,
                i18n.t("notify.recording_controller.start_failed"),
            )
        with contextlib.suppress(Exception):
            event_bus.publish(
                {"type": "error", "data": {"message": "Could not start recording", "kind": "recording_start"}}
            )
        app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))

    def _start_impl(self, controller) -> None:
        """Inner start implementation, called under _toggle_lock."""
        app = controller._app
        # Reset the published join timeout to the default BEFORE any of
        controller._start_worker_join_timeout = _DEFAULT_START_JOIN_TIMEOUT_S
        if app.recorder.recording:
            log.info("[DICTATION] _start_dictation: already recording, no-op")
            return

        if not self._gate_voice_biometric_consent(app):
            return

        # Cancel any stale pending timers from previous sessions
        app._cancel_pending_timers()

        # If a model change was deferred during the previous recording,
        try:
            app.models.apply_pending_model_change()
        except Exception:
            log.exception("[DICTATION] Failed to apply pending model change; continuing")

        # ``ensure_active_engine_loaded()`` is deferred to AFTER

        log.info("[DICTATION] Starting recording... (cycle=%s)", app._cycle_id)
        try:
            self._wire_recorder_start_callbacks(controller, app)

            app.recorder.start()
            self._notify_mic_watcher_of_active_device(app)
            app.tray.set_state(AppState.RECORDING, i18n.t("state.recording_controller.recording"))
            # Show the floating bubble once we know the stream is open
            app._waveform_bubble.show()
            # System-volume ducking moved OFF this thread: it now runs at
            log.info("[DICTATION] Recording started OK (cycle=%s)", app._cycle_id)
            self._claim_keyboard_ownership_for_recording(app)
            self._rearm_esc_cancel_if_enabled(app)
            self._publish_recording_started_event()
            self._spawn_dictation_start_worker(controller, app)
        except Exception as e:
            log.exception("[DICTATION] Failed to start recording: %s", e)
            self._teardown_failed_recorder_start(
                controller,
                app,
                restart_level_monitor=True,
                discard_log_label="start-failure teardown",
            )
            self._publish_start_failure_notification(app, e)

    def _start_dictation_worker_entry(
        self,
        controller,
        cycle_id: str,
        complete_event: threading.Event,
    ) -> None:
        """Daemon worker-thread entry point for the model-load + post-load
        phase of ``_start_impl``.

        Mirrors the ``_stop_and_transcribe_worker_entry`` pattern: the F2
        hotkey thread does the synchronous pre-start work (consent check,
        timer cancel, ``recorder.start()``, tray state, bubble show) inside
        ``_start_impl``, then spawns THIS worker and returns after a
        bounded join performed by the public entry (``toggle`` / ``start``
        via ``_run_public_entry``). OUTSIDE ``_toggle_lock``. The worker
        ducks the system volume first (still after ``recorder.start()``,
        so the first buffered chunks capture with the speakers already
        fading down) and then performs the potentially-slow model load
        (5-30s on idle-unload reload) and the post-load steps
        (active_transcriber check, Whisper fallback, streaming-session
        setup).

        Rationale: pre-fix, the F2 dispatch thread blocked for 5-30s
        inside ``ensure_active_engine_loaded()`` on the idle-unload
        reload path. With a single-dispatch-thread hotkey backend
        (pynput), this blocked ALL hotkeys: including ESC cancel, for
        the entire reload window. Moving the load to a daemon worker
        frees the dispatch thread to process subsequent hotkey events
        (e.g. a second F2 to stop the recording whose model is still
        loading).

        The worker runs WITHOUT ``_toggle_lock``. The public entry
        (``toggle`` / ``start`` via ``_run_public_entry``) releases the
        lock before its bounded join, so neither the worker nor the join
        holds it. The worker doesn't need the lock because:
        1. ``ensure_active_engine_loaded()`` has its own internal
           ``_lazy_init_lock`` in ``ModelManager``.
        2. The post-load re-check reads atomic state
           (``recorder.recording``, ``_busy_event.is_set()``).
        3. ``_start_streaming_session_if_enabled()`` uses
           ``_streaming_session_lock`` for its own serialization.

        The worker signals ``complete_event`` in its ``finally`` block so
        tests that assert model-loaded state can wait on the event.

        Parameters
        ----------
        controller:
            The owning :class:`RecordingController` (back-reference).
        cycle_id:
            The cycle ID captured at ``_start_impl`` entry (e.g.
            ``"#42"``). Used for log correlation.
        complete_event:
            A :class:`threading.Event` that the worker signals in its
            ``finally`` block. The F2 thread stores this on the
            controller as ``controller._start_complete_event`` so tests
            can wait on it.
        """
        app = controller._app
        # Duck system volume HERE, not on the hotkey thread: a duck costs
        if app.recorder.recording:
            app._duck_volume()
            # Compensate the off-lock ordering: a concurrent ``cancel()``
            if not app.recorder.recording:
                app._restore_volume()
        try:
            # Load / reload the active engine. This is the potentially
            app.models.ensure_active_engine_loaded()

            # ``_busy_event`` (busy=True), so we check BOTH conditions.
            if not app.recorder.recording or not app._busy_event.is_set():
                log.info(
                    "[DICTATION] Recorder stopped or app became busy during "
                    "model load, aborting post-load steps (cycle=%s)",
                    cycle_id,
                )
                return

            active = app.models.active_transcriber()
            if active is None or not getattr(active, "is_loaded", False):
                # No engine loaded, try to load Whisper as a fallback.
                log.warning("[DICTATION] No loaded engine found, lazy-loading Whisper as fallback")
                app.models.fallback_to_whisper(notify_on_failure=True)
                active = app.models.active_transcriber()
                if active is None or not getattr(active, "is_loaded", False):
                    log.error("[DICTATION] Whisper fallback also failed, cannot record")
                    # The recorder is already running, discard it so we
                    try:
                        app.recorder.discard()
                    except Exception:
                        log.debug(
                            "[DICTATION] recorder.discard() during model-fail teardown raised (best-effort)",
                            exc_info=True,
                        )
                    app.recorder.recording = False
                    app.tray.set_state(
                        AppState.ERROR,
                        i18n.t("state.recording_controller.model_failed_retry"),
                    )
                    app._schedule_timer(
                        3.0,
                        lambda: app.tray.set_state(
                            AppState.ERROR,
                            i18n.t("state.recording_controller.model_failed_retry"),
                        ),
                    )
                    return

            # Streaming session requires an active transcriber, so it
            controller._start_streaming_session_if_enabled()
        except Exception as e:
            log.exception("[DICTATION] Start worker failed: %s", e)
            # Same teardown + typed-reason mapping as the ``_start_impl``
            self._teardown_failed_recorder_start(
                controller,
                app,
                restart_level_monitor=False,
                discard_log_label="start-worker teardown",
            )
            self._publish_start_failure_notification(app, e)
        finally:
            complete_event.set()

    def stop(self, controller) -> None:
        """Stop recording and transcribe in background."""
        with controller._toggle_lock:
            self._stop_impl(controller)

    def _stop_impl(self, controller) -> None:
        """Inner stop implementation, called under _toggle_lock.

        The blocking ``recorder.stop()`` call (~2.4s worst case: 300ms
        stream-teardown poll + 2.0s audio-worker drain + 2.0s event-worker
        drain + np.concatenate + resample) is moved off the hotkey thread
        into a daemon worker. The hotkey thread does the synchronous
        pre-stop work (publish event, keyboard ownership, busy flag, tray,
        bubble), then spawns the worker as ``controller._transcription_thread``
        and returns after a bounded ``join(timeout=0.1)``. The
        ``_toggle_lock`` is held only for the synchronous pre-stop work +
        thread spawn (microseconds), not the 2.4s teardown. The
        ``_busy_event`` (cleared synchronously below) prevents concurrent
        ``stop()`` / ``toggle()`` calls from proceeding while the
        stop+transcribe worker is running.
        """
        app = controller._app
        if not app.recorder.recording:
            log.info("[DICTATION] _stop_dictation: not recording, no-op")
            return
        # (``_busy_event`` cleared = busy=True), this is a duplicate
        if not app._busy_event.is_set():  # busy = True
            log.debug(
                "[DICTATION] _stop_dictation: stop already in progress (busy=True) | no-op (cycle=%s)",
                app._cycle_id,
            )
            return
        # Emit ``recording_stopped`` push event. Log push failures (see
        try:
            event_bus.publish({"type": "recording_stopped"})
        except Exception:
            log.warning(
                "[SOUND] failed to push recording_stopped event",
                exc_info=True,
            )

        # Recording is stopping, release keyboard ownership back to
        try:
            keyboard_ownership().set_owner("normal", reason=f"recording stopped (cycle={app._cycle_id})")
        except Exception:
            log.debug(
                "[DICTATION] failed to reset keyboard ownership on stop",
                exc_info=True,
            )

        # Cancel any stale pending timers
        app._cancel_pending_timers()

        log.info("[DICTATION] Stopping recording... (cycle=%s)", app._cycle_id)

        # legacy ``_busy_event.clear()``: the inverted primitive stays
        app._busyness.set_busy()

        # Detach the RMS callback so the audio path cannot keep pushing
        app.recorder.on_rms_level = None
        # Push a final zero-level event so the renderer resets its
        app._waveform_bubble.reset_level()
        # NEW-BUBBLE-TRANSCRIBING: Instead of hiding the bubble
        app._waveform_bubble.set_state("transcribing")

        _captured_cycle_id = app._cycle_id

        # Spawn a daemon worker thread that performs ``recorder.stop()``

        # Write ``controller._transcription_thread`` under
        with controller._watchdog_lock:
            controller._transcription_thread = threading.Thread(
                target=self._stop_and_transcribe_worker_entry,
                args=(controller, _captured_cycle_id),
                name="Transcription",
                daemon=True,
            )
            controller._transcription_thread.start()

        # Bounded wait for the worker to make progress. In tests where
        with contextlib.suppress(Exception):
            controller._transcription_thread.join(timeout=0.1)

    def _stop_and_transcribe_worker_entry(self, controller, cycle_id: str) -> None:
        """Daemon worker-thread entry point, extracted from the former"""
        app = controller._app
        try:
            # ``recorder.stop()`` is the FIRST step. Pre-fix this ran
            audio = app.recorder.stop()
            # Clear the active-mic-id on the watcher so it stops
            with contextlib.suppress(Exception):
                mic_watcher = getattr(getattr(app.recorder, "_devices", None), "_mic_watcher", None)
                if mic_watcher is not None:
                    mic_watcher.set_active_mic_id(None)
        except Exception:
            log.exception("[DICTATION] Failed to stop recording (worker)")
            controller._cancel_streaming_session()
            app._restore_volume()
            # Best-effort restart of the level_monitor for the
            controller._maybe_restart_level_monitor_for_always_visible_bubble(app)
            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.stop_failed"))
            # Critical, bypass the notification toggle (dictation failed,
            app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.stop_failed"),
            )
            app._busyness.set_idle()  # busy = False (BP-90 coordinator)
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        self._run_stop_and_transcribe(controller, audio, cycle_id)

    def _run_stop_and_transcribe(self, controller, audio, cycle_id: str) -> None:
        """Transcription pipeline body, extracted from the former nested
        closure ``stop_and_transcribe_worker``.

        Takes the captured ``audio`` bytes (already resampled to
        ``config.sample_rate`` by ``recorder.stop()``) and the
        ``cycle_id`` (captured at stop time so a new dictation cycle
        starting before transcription completes does not corrupt log
        correlation) and runs the full post-stop pipeline:

        1. Log ring-buffer overflow stats.
        2. Restore system volume + restart the level_monitor for the
           always-visible bubble.
        3. Compute ``duration`` + ``recorded_rms``.
        4. Finalize the audio-quality report (revived
           AudioQualityAnalyzer).
        5. Short-circuit on ``duration < 0.5s`` (too short).
        6. Set tray to TRANSCRIBING + reset watchdog counter.
        7. Pop + stash the streaming session so the pipeline's
           ``pop_streaming_session()`` can retrieve it and call
           ``session.finalize(audio)``: the streaming fast path.
        8. Start the persistent watchdog thread (RACE-013).
        9. Stash audio in ``controller._current_audio`` (privacy) then
           immediately capture-and-clear so the slot doesn't retain the
           bytes for the transcription duration.
        10. Run ``DictationPipeline.run(...)``.

        Unit-testable: call
        ``self._run_stop_and_transcribe(controller, fake_audio, cycle_id)``
        directly with a mock ``app.recorder`` / ``app.tray``
        / ``app.config`` to exercise the transcription body without
        spinning up a real recorder or hotkey thread.
        """
        app = controller._app

        # Surface ring-buffer overflow detected during the recording.
        dropped = getattr(app.recorder, "_dropped_ring_chunks", 0)
        if dropped:
            log.warning(
                "[DICTATION] Ring buffer overflow during recording: "
                "%d chunks dropped (cycle=%s). Audio worker could not "
                "keep up; transcription may be incomplete.",
                dropped,
                app._cycle_id,
            )

        # Restore system volume immediately, don't wait for transcription
        app._restore_volume()

        # Now that the Recorder's InputStream is closed, restart the
        controller._maybe_restart_level_monitor_for_always_visible_bubble(app)

        # Audio has already been resampled to config.sample_rate by Recorder.stop()
        duration = len(audio) / app.config.sample_rate if len(audio) > 0 else 0
        # Capture RMS before starting transcription (race-safe).
        recorded_rms = app.recorder.last_rms

        # Run the revived AudioQualityAnalyzer on the captured audio.
        if duration > 0:
            try:
                app._finalize_audio_quality_report(audio)
            except Exception:
                log.debug("[AUDIO_QUALITY] finalize failed", exc_info=True)
        log.info(
            "[DICTATION] Recording stopped%s of audio | recorded_rms=%.4f | busy=True (cycle=%s)",
            format_duration(duration),
            recorded_rms,
            cycle_id,
        )

        if duration < 0.5:
            log.info("[DICTATION] Audio too short, skipping transcription")
            controller._cancel_streaming_session()
            app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.too_short"))
            app._busyness.set_idle()  # busy = False (BP-90 coordinator)
            app._schedule_timer(2.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        log.info(
            "[DICTATION] Starting transcription (stop+transcribe worker)... (cycle=%s)",
            cycle_id,
        )
        app.tray.set_state(AppState.TRANSCRIBING, i18n.t("state.recording_controller.transcribing"))

        # Reset watchdog counter for this transcription cycle.
        with controller._watchdog_lock:
            controller._watchdog_firings = 0

        # PERF- signal the streaming session to cancel BEFORE starting
        with controller._streaming_session_lock:
            # ``_stop_impl`` does NOT pop the session or signal cancel.
            controller._pending_finalize_session = controller._streaming_session

        # RACE-013: Start persistent watchdog thread using Event.wait(timeout=90).
        controller._start_watchdog_thread()

        # ``transcribe_thread`` extracted to ``DictationPipeline`` class.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        # Privacy: hold audio bytes in a shared, clearable slot so
        controller._current_audio = audio
        # Capture into a local and clear the shared slot BEFORE calling
        audio_bytes = controller._current_audio
        controller._current_audio = None

        pipeline = DictationPipeline(app)
        # ``run()`` (sole caller always passed ``None``; RACE-013: no
        pipeline.run(
            audio=audio_bytes,
            duration=duration,
            recorded_rms=recorded_rms,
            cycle_id=cycle_id,
        )
        # Now that the transcription pipeline has fully returned (the
        controller._discard_cancelled_cycle_id(cycle_id)

    def cancel(self, controller) -> None:
        """Feature: ESC to cancel -- cancel current recording/transcription."""
        with controller._toggle_lock:
            self._cancel_impl(controller)

    def _cancel_impl(self, controller) -> None:
        """Inner cancel implementation, called under _toggle_lock."""
        app = controller._app

        # ESC: If no recording is active, the ESC cancel is a no-op. The
        try:
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug(
                    "[CANCEL] ESC ignored, frontend hotkey capture active (cycle=%s)",
                    app._cycle_id,
                )
                return
        except Exception:
            log.debug("[CANCEL] keyboard ownership check failed", exc_info=True)

        if not app.recorder.recording:
            # ``_busy_event.is_set() == False``), we immediately mark
            with controller._watchdog_lock:
                t_thread = controller._transcription_thread
            if (
                t_thread is not None and t_thread.is_alive() and not app._busy_event.is_set()  # busy = True
            ):
                log.info(
                    "[CANCEL] ESC during transcription phase (cycle=%s), marking cancelled + force-recovering",
                    app._cycle_id,
                )
                cycle_id = getattr(app, "_cycle_id", None)
                if cycle_id is not None:
                    # Use the bounded-registry helper so the set cannot
                    controller._mark_cycle_cancelled(cycle_id)
                    log.info(
                        "[CANCEL] cycle %s marked cancelled, late transcription will not be pasted",
                        cycle_id,
                    )
                # ``_force_recover_from_stuck_transcription`` now also
                try:
                    controller._force_recover_from_stuck_transcription(force=True)
                except Exception:
                    log.exception(
                        "[CANCEL] _force_recover_from_stuck_transcription raised during cancel (cycle=%s)",
                        app._cycle_id,
                    )
                return
            log.debug("[CANCEL] Cancel pressed but no recording active (cycle=%s), no-op", app._cycle_id)
            return

        log.info("[CANCEL] Cancelling current dictation (cycle=%s)", app._cycle_id)

        # Release keyboard ownership back to "normal" so subsequent
        try:
            keyboard_ownership().set_owner("normal", reason=f"recording cancelled (cycle={app._cycle_id})")
        except Exception:
            log.debug(
                "[CANCEL] failed to reset keyboard ownership on cancel",
                exc_info=True,
            )
        # Show CANCELLING state immediately.
        try:
            app.tray.set_state(AppState.CANCELLING, i18n.t("state.recording_controller.cancelling"))
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
                # Clear the active-mic-id on the watcher so it stops
                with contextlib.suppress(Exception):
                    mic_watcher = getattr(getattr(app.recorder, "_devices", None), "_mic_watcher", None)
                    if mic_watcher is not None:
                        mic_watcher.set_active_mic_id(None)
                # Immediately secure-clear the audio buffers from memory
                app.recorder._secure_clear_session_caches()
            except Exception as e:
                # Don't abort the cancel path, fall through to ensure
                log.exception(
                    "[CANCEL] Failed to discard recording (cycle=%s): %s",
                    app._cycle_id,
                    e,
                )

        # Always run these, even if discard failed.
        try:
            controller._cancel_streaming_session()
        except Exception:
            log.exception("[CANCEL] Failed to cancel streaming session")

        # Restore system volume on cancel
        try:
            app._restore_volume()
        except Exception:
            log.exception("[CANCEL] Failed to restore volume")

        # Hide bubble unless always_visible mode (in which case set to
        try:
            if app.config.bubble_behavior != "always_visible":
                app._waveform_bubble.hide()
            else:
                app._waveform_bubble.set_state("idle")
        except Exception:
            log.exception("[CANCEL] Failed to hide/set idle bubble")

        # Tray state + busy flag MUST be cleared so the user can press
        app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.cancelled"))
        app._busyness.set_idle()  # BP-90: coordinator-routed
