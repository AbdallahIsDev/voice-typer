"""Recording lifecycle — extracted from ``RecordingController``
(Phase 4.5 split).

Owns the toggle / start / stop / cancel state machine and the
stop+transcribe worker entry point. The actual recording flag, busy
event, transcription thread handle, and toggle lock all live on
``RecordingController`` (shared state) — this helper operates on them
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
call the controller's ``_toggle_impl`` / ``_start_impl`` /
``_stop_impl`` / ``_cancel_impl`` (which are themselves 1-line
delegators back to this helper). The re-entrant hop
``lifecycle.toggle → controller._toggle_impl → lifecycle._toggle_impl``
preserves the existing test contract: tests that monkeypatch
``ctrl._stop_impl`` / ``ctrl._toggle_impl`` (etc.) still see their
mock invoked, because ``lifecycle.stop`` calls
``controller._stop_impl()`` (the controller's method — possibly
monkeypatched), not ``self._stop_impl()`` (the helper's method).

Originally lines 469–1500 of ``recording_controller.py``.
"""

from __future__ import annotations

import contextlib
import logging
import threading

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.duration import format_duration
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


#: Stable ``consent_field`` id the backend publishes for the voice-
#: biometric dictation gate. The renderer's ``consent_required`` push
#: handler (App.tsx) branches on this id so it only surfaces the
#: in-app consent prompt for the biometric gate — the HuggingFace
#: consent shape (``provider`` / ``model``) is handled by the
#: model-download flow.
VOICE_BIOMETRIC_CONSENT_FIELD = "voice_biometric_consent"


#: The exact RuntimeError message raised by the recording pipeline when
#: no input device could be opened at all (``_recorder_split.py``,
#: ``start_recording`` — "No input device could be opened"). Matched by
#: substring so a future message tweak that appends context still maps.
_NO_INPUT_DEVICE_MARKER = "No input device could be opened"


def _recording_start_failure_message(exc: BaseException) -> str:
    """Map a ``recorder.start()`` failure to a safe, user-friendly message.

    The typed exceptions the recording pipeline raises carry the backend's
    reason (microphone permission denied, no input device); map those to
    specific messages so the tray tooltip / ``status_change`` push shows
    WHY recording failed instead of the bare "Recording failed" label.
    Everything else falls back to the generic label — raw exception text
    is deliberately NOT surfaced (it can leak absolute paths, device
    names, hostnames; same policy as the ``notify.recording_controller.*``
    keys).
    """
    from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

    if isinstance(exc, MicrophonePermissionDeniedError):
        return i18n.t("state.recording_controller.recording_failed_permission")
    if isinstance(exc, RuntimeError) and _NO_INPUT_DEVICE_MARKER in str(exc):
        return i18n.t("state.recording_controller.recording_failed_no_device")
    return i18n.t("state.recording_controller.recording_failed")


def _notify_consent_gate(title: str, message: str) -> bool:
    """Prefer a CLICKABLE host notification over the pystray balloon.

    pystray Win32 balloons (``tray.notify_safety``) cannot carry click
    handlers — clicking them does nothing, so a refused recording
    leaves the user with no path to fix it. When a live Electron /
    Tauri host is connected, publish a ``notification`` event carrying
    ``click_consent_field`` (the voice-biometric field): the Electron
    main-process ``notification`` handler wires ``Notification.on("click")``
    → show window + broadcast ``navigate {path:"/settings",
    consent_field}``, so clicking the toast lands the user on the
    EXACT Settings consent row (mirrors the ``/models`` click_path
    pattern in ``model_manager.py``).

    Returns True when the clickable event was published (the caller
    should NOT also show the non-clickable balloon), False otherwise
    (no live transport / publish failed → the caller falls back to the
    balloon so the refusal is still visible).
    """
    try:
        from voice_typer.server import event_bus

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
    """Best-effort publish of a ``consent_required`` push event.

    The renderer subscribes to this event (App.tsx) to surface an
    in-app consent prompt + Settings → Privacy deep-link when dictation
    start is refused for missing ``voice_biometric_consent``. This
    covers entry points the renderer cannot gate client-side (the F2
    hotkey, the tray click action, and the sandboxed bubble window) —
    without it, those paths only showed a tray notification and the
    renderer got zero feedback.

    The event carries only the stable ``consent_field`` id; the
    renderer localizes the message itself (mirrors the HuggingFace
    ``consent_required`` publish in ``service/model.py``).
    """
    try:
        from voice_typer.server import event_bus

        event_bus.publish(
            {
                "type": "consent_required",
                "data": {"consent_field": VOICE_BIOMETRIC_CONSENT_FIELD},
            }
        )
    except Exception:
        log.debug("[DICTATION] consent_required event push failed", exc_info=True)


class RecordingLifecycle:
    """Toggle / start / stop / cancel state machine for recording.

    Extracted from the former ``RecordingController.toggle`` /
    ``_toggle_impl`` / ``start`` / ``_start_impl`` / ``stop`` /
    ``_stop_impl`` / ``_stop_and_transcribe_worker_entry`` /
    ``_run_stop_and_transcribe`` / ``cancel`` / ``_cancel_impl``
    methods. Each method's body is the moved implementation, with
    ``self.X`` references rewritten to ``controller.X`` for shared state.
    ``RecordingController`` keeps 1-line delegators on each method name
    so existing call sites and tests that monkeypatch the controller's
    methods continue to work.
    """

    def __init__(self) -> None:
        # Stateless helper — all state lives on the controller.
        pass

    # ── Toggle / start / stop / cancel ─────────────────────────────────

    def toggle(self, controller) -> None:
        """Toggle recording on/off.

        RACE-025: Serializes concurrent toggle calls from different threads
        (hotkey thread + tray thread) to prevent TOCTOU where two near-
        simultaneous F2 presses both pass the _busy_event check.
        """
        with controller._toggle_lock:
            controller._toggle_impl()

    def _toggle_impl(self, controller) -> None:
        """Inner toggle implementation, called under _toggle_lock."""
        app = controller._app
        # The cycle counter is NOT incremented here. Pre-fix, every
        # blocked / queued / errored toggle consumed a cycle ID, producing
        # non-contiguous cycle numbers in the log trail (a "cycle #5" with
        # no corresponding recording was confusing). The counter is now
        # incremented only when we commit to a real start/stop below, so
        # cycle IDs map 1:1 to actual dictations. Early-return log lines
        # reference the PREVIOUS cycle's ``app._cycle_id`` for correlation.

        active = app.models.active_transcriber()
        model_loaded = active is not None and active.is_loaded
        log.info(
            "[HOTKEY FIRED] toggle_dictation called (recording=%s, busy=%s, model_loaded=%s, thread=%s, cycle=%s)",
            app.recorder.recording,
            app._busy_event.is_set(),
            model_loaded,
            threading.current_thread().name,
            app._cycle_id,
        )
        if not app._busy_event.is_set():  # busy
            log.warning("[F2 BLOCKED] Busy transcribing, ignoring toggle (cycle=%s)", app._cycle_id)
            return

        # Model still loading in the background (post-fast-startup). Queue
        # the request and let the loader auto-start recording once it
        # finishes. This makes F2 feel responsive even on a cold boot.
        #
        # Capture the loader reference ONCE: the background loader's
        # finally block sets ``self._model_load_thread = None``, and since
        # ``toggle_dictation`` runs on the hotkey thread while the loader
        # runs on its own thread, re-reading the attribute in the
        # ``is_alive()`` check is a TOCTOU race that can dereference None.
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
            # started). ``_model_load_thread`` is None because the
            # loader's ``finally`` block nulls it on exit, and
            # ``active_transcriber()`` returns None because no engine
            # was successfully constructed.
            #
            # Pre-fix, this branch only set the tray to "starting up"
            # and returned — so pressing F2 after a model-load failure
            # (the exact recovery the model_manager's tray message
            # instructed the user to perform) did nothing. The user was
            # forced to restart the app to recover the ASR engine.
            #
            # Now: re-trigger ``start_background_load()`` (idempotent —
            # it returns immediately if a load is already running). Set
            # ``_pending_dictation=True`` so the loader's ``finally``
            # block auto-starts the dictation if the retry succeeds. The
            # tray shows "Retrying model load..." so the user sees the
            # retry is actually happening (instead of the misleading
            # "starting up -- please wait..." which implied passive
            # waiting).
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
                # user still sees a loading indicator if the re-trigger
                # itself raised (extremely unlikely —
                # start_background_load only constructs a Thread).
                app.tray.set_state(
                    AppState.LOADING,
                    i18n.t("state.recording_controller.starting_up"),
                )
            return

        # Commit to a real start/stop — NOW increment the cycle counter
        # so cycle IDs are 1:1 with actual dictations (no gaps from
        # blocked / queued / errored toggles).
        app._cycle_counter += 1
        app._cycle_id = f"#{app._cycle_counter}"

        if app.recorder.recording:
            # Call ``app._stop_dictation`` (which delegates to
            # ``controller.stop()``) so tests that monkeypatch
            # ``app._stop_dictation`` still intercept the call.
            app._stop_dictation()
        else:
            app._start_dictation()

    def start(self, controller) -> None:
        """Start a recording session.

        Acquires ``_toggle_lock`` (an RLock) so the auto-stop Timer
        thread's ``_stop_dictation`` -> ``controller.stop()`` call (or
        the ESC cancel hotkey's ``cancel()``) serializes against an
        in-flight ``toggle()`` / ``start()`` / ``stop()`` / ``cancel()``
        on any other thread. RLock allows the re-entrant path
        ``toggle() -> app._start_dictation() -> controller.start()`` to
        re-acquire without deadlocking.
        """
        with controller._toggle_lock:
            controller._start_impl()

    def _start_impl(self, controller) -> None:
        """Inner start implementation, called under _toggle_lock."""
        app = controller._app
        if app.recorder.recording:
            log.info("[DICTATION] _start_dictation: already recording, no-op")
            return

        # Enforce voice_biometric_consent before capturing any audio. The
        # config field and Electron UI toggle existed previously, but the
        # audio pipeline never checked the flag — meaning the consent was
        # a UI decoration with zero enforcement. Now we refuse to start
        # recording if the user has not explicitly consented to voice
        # biometric processing.
        #
        # This is a GDPR Art. 9 requirement for processing biometric data
        # (voice is biometric). The default is False — the user MUST opt
        # in via the Settings UI before any recording happens.
        try:
            if not getattr(app.config, "voice_biometric_consent", False):
                log.warning(
                    "[DICTATION] Refusing to start recording - voice_biometric_consent "
                    "is False. User must enable it in Settings > Privacy."
                )
                try:
                    app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.consent_required"))
                    # Prefer the CLICKABLE host toast (click → Settings
                    # → exact Voice Biometric row); fall back to the
                    # non-clickable pystray balloon when no host
                    # transport is live.
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
                # renderer surfaces the in-app consent prompt + Settings
                # deep-link (the tray notification alone is invisible for
                # renderer-triggered dictation — the IPC returned ``ack``
                # with no feedback).
                _publish_consent_required_event()
                return
        except Exception:
            # GDPR Art. 9: if we cannot verify voice_biometric_consent
            # (e.g. corrupted config read), fail CLOSED — refuse to
            # record. Failing open would let the user record without
            # verified consent, which is a privacy violation. The user
            # can fix the config (or re-grant consent in Settings) and
            # try again. Log the failure for diagnosis.
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
                    "Could not verify voice biometric consent.\nRecording refused — check Settings > Privacy.",
                ):
                    app.tray.notify_safety(
                        APP_NAME,
                        "Could not verify voice biometric consent.\nRecording refused — check Settings > Privacy.",
                    )
            except Exception:
                log.debug(
                    "[DICTATION] failed to notify about consent check exception",
                    exc_info=True,
                )
            # Same ``consent_required`` push as the plain-refusal branch
            # — a corrupt-config fail-CLOSED refusal deserves the same
            # in-app feedback.
            _publish_consent_required_event()
            return

        # Cancel any stale pending timers from previous sessions
        app._cancel_pending_timers()

        # If a model change was deferred during the previous recording,
        # apply it now (loads the new backend before we start capturing
        # audio). Without this, the user's "change to medium after current
        # recording" would silently never happen.
        try:
            app.models.apply_pending_model_change()
        except Exception:
            log.exception("[DICTATION] Failed to apply pending model change; continuing")

        # ``ensure_active_engine_loaded()`` is deferred to AFTER
        # ``recorder.start()`` (see below). Pre-fix, it ran BEFORE
        # ``recorder.start()`` and blocked the F2 hotkey thread for 5-30s
        # when the idle-unload timer had fired — the first 5-30s of speech
        # was lost because audio was not being captured during the reload.
        # Now the recorder buffers audio while the model reloads inline;
        # the transcription thread (started in ``_stop_impl``)
        # transcribes the buffered audio once the model is ready.

        log.info("[DICTATION] Starting recording... (cycle=%s)", app._cycle_id)
        try:
            # H12: Wire silence detection callbacks
            app.recorder.on_silence_warning = controller.on_silence_warning
            app.recorder.on_silence_auto_stop = controller.on_silence_auto_stop
            app.recorder.on_max_duration_auto_stop = controller.on_max_duration_auto_stop
            # Wire the microphone-permission-revoked callback so the
            # device_health_checker_loop can surface a distinct
            # ``notify.recording_controller.mic_permission_revoked``
            # notification (and IPC event) when the OS revokes mic
            # access mid-recording, instead of falling through to the
            # misleading "silence detected" auto-stop after 30-60 s of
            # zero-filled buffers.
            # ``getattr``-guarded so older Recorder test doubles that
            # don't accept the attribute still work.
            with contextlib.suppress(Exception):
                app.recorder.on_microphone_permission_revoked = controller.on_microphone_permission_revoked

            # Waveform bubble: feed RMS levels from the audio callback
            app.recorder.on_rms_level = controller.on_recorder_rms

            # Reset audio-quality analyzer accumulators so per-chunk
            # statistics don't carry over from the previous session.
            try:
                app._audio_quality.reset()
            except Exception:
                log.debug("[AUDIO_QUALITY] reset on start failed", exc_info=True)

            # Stop the level_monitor's PortAudio InputStream BEFORE
            # opening the Recorder's stream. Without this guard, both
            # streams run concurrently (Linux/macOS doubles audio-path
            # CPU; Windows MME device-conflict fails the second open).
            controller._stop_level_monitor_for_recorder_start()

            app.recorder.start()
            # Tell the mic watcher which mic_id we're recording from so
            # the OS-event-driven active-mic-lost check can fire on the
            # next device-list change. Best-effort: a missing/None
            # mic_watcher (platform without OS watcher) is silently
            # skipped.
            with contextlib.suppress(Exception):
                mic_watcher = getattr(app.recorder, "_mic_watcher", None)
                if mic_watcher is not None:
                    # The resolved device index (or None for default) is
                    # the active mic_id the watcher will look for.
                    resolved = getattr(app.recorder, "_effective_device", None)
                    if resolved is None:
                        resolved = app.recorder._resolve_device()
                    mic_watcher.set_active_mic_id(resolved)
            app.tray.set_state(AppState.RECORDING, i18n.t("state.recording_controller.recording"))
            # Show the floating bubble once we know the stream is open
            app._waveform_bubble.show()
            # Duck system volume AFTER recording starts so the first
            # chunk of audio benefits from the ducked speakers.
            app._duck_volume()
            log.info("[DICTATION] Recording started OK (cycle=%s)", app._cycle_id)
            # Mark the recording subsystem as the keyboard owner. The ESC
            # cancel hotkey will fire normally during a recording (it's
            # the only way to cancel). When recording stops, ownership
            # returns to "normal".
            try:
                from voice_typer.server.keyboard_ownership import keyboard_ownership

                keyboard_ownership().set_owner("recording", reason=f"recording started (cycle={app._cycle_id})")
            except Exception:
                log.debug(
                    "[DICTATION] failed to set keyboard ownership on start",
                    exc_info=True,
                )

            # ESC-CANCEL-WATCHDOG: the ESC-to-cancel hotkey is the ONLY way
            # to abort an in-progress recording, so if its backend died
            # (silent startup-registration failure, native binary crash,
            # or a multi-instance hook-chain collapse) the user would be
            # unable to cancel — exactly the reported "Escape does
            # nothing" symptom. Re-arm it on every recording start so a
            # dead/stale ESC backend can never leave the user trapped in
            # a recording.
            try:
                if getattr(app.config, "esc_cancel_enabled", False):
                    esc_backend = getattr(app.hotkeys, "_esc_backend", None)
                    if esc_backend is None or not esc_backend.is_alive():
                        log.warning(
                            "[DICTATION] ESC cancel backend missing/dead at "
                            "recording start (backend=%r) — re-registering",
                            type(esc_backend).__name__ if esc_backend else "None",
                        )
                        app.hotkeys.register_esc()
            except Exception:
                log.warning(
                    "[DICTATION] failed to re-arm ESC cancel hotkey on start",
                    exc_info=True,
                )
            # Emit ``recording_started`` push event so the renderer can
            # proactively refresh UI (Home/Dashboard/History). Log push
            # failures instead of silently swallowing them — a failed
            # push means the renderer never hears about
            # ``recording_started``, so the sound cue won't play and the
            # user gets no audible feedback. This must be visible.
            try:
                from voice_typer.server import event_bus

                event_bus.publish({"type": "recording_started"})
            except Exception:
                log.warning(
                    "[SOUND] failed to push recording_started event",
                    exc_info=True,
                )

            # Load / reload the active engine AFTER ``recorder.start()`` so
            # the recorder buffers audio while the model reloads (5-30s
            # on idle-unload). Pre-fix this ran before ``recorder.start()``
            # and the first 5-30s of speech was lost. The transcription
            # thread (started in ``_stop_impl``) transcribes the buffered
            # audio once the model is ready. If the model fails to load,
            # we discard the recorder we just started and surface an error.
            #
            # The model load + post-load steps (active_transcriber check,
            # fallback_to_whisper, _start_streaming_session_if_enabled)
            # run on a DAEMON WORKER THREAD so the F2 hotkey backend's
            # single dispatch thread is NOT blocked for 5-30s on the
            # idle-unload reload path. Pre-fix, the lock was released
            # for the duration of ``ensure_active_engine_loaded()`` so
            # concurrent stop/cancel could proceed, but the F2 thread
            # itself still blocked for 5-30s — meaning:
            #   - The F2 hotkey backend's dispatch thread was occupied
            #     and could not process a second F2 press (e.g. to stop
            #     the recording the user just started).
            #   - On hotkey backends with a single dispatch thread
            #     (pynput), ALL hotkeys were blocked for 5-30s —
            #     including ESC cancel.
            # The F2 thread now spawns the worker and returns after a
            # bounded ``join(timeout=0.1)`` — fast enough for tests
            # with mocked models (the worker completes in <1ms), slow
            # enough to not block the dispatch thread in production
            # (5-30s idle-unload reload). The worker is a daemon so it
            # doesn't block process exit.
            #
            # The worker runs WITHOUT ``_toggle_lock`` — the F2 thread
            # holds it via the ``with`` block in ``start()`` for the
            # duration of the bounded join (0.1s), then releases it on
            # ``_start_impl`` return. The worker doesn't need the lock
            # because:
            # 1. ``ensure_active_engine_loaded()`` has its own internal
            #    lock (``_lazy_init_lock`` in ``ModelManager``).
            # 2. The post-load re-check reads atomic state
            #    (``recorder.recording``, ``_busy_event.is_set()``).
            # 3. ``_start_streaming_session_if_enabled()`` uses
            #    ``_streaming_session_lock`` for its own serialization.
            # The ``_busy_event`` is NOT cleared by ``_start_impl``
            # (``_stop_impl`` clears it), so a concurrent ``stop()``
            # that acquires the lock after the F2 thread releases it
            # would see ``busy_event.is_set() == True`` (not busy) and
            # proceed — the desired behavior (the user explicitly
            # stopped, so the buffered audio should be transcribed as
            # soon as the model finishes loading).
            #
            # The worker signals ``_start_complete_event`` in its
            # finally block so tests that need to assert model-loaded
            # state can wait on the event.
            start_complete_event = threading.Event()
            # Publish on the controller so tests can wait on it
            # (``controller._start_complete_event.wait(timeout=...)``).
            # A fresh Event is created per ``_start_impl`` call; the
            # previous event (if any) is replaced. The previous worker
            # still holds a reference to the old event and signals it
            # — the old event is garbage-collected after the worker
            # finishes.
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
            # is ADAPTIVE based on whether the model was already loaded
            # at ``_start_impl`` entry:
            #
            # * Model already loaded (common case): ``ensure_active_engine_loaded()``
            #   is a no-op → the worker finishes in <1ms → a short 0.1s
            #   timeout is plenty AND keeps the F2 dispatch thread
            #   sub-100ms (the desired responsiveness goal).
            # * Model NOT loaded (idle-unload reload / model-fail path):
            #   ``ensure_active_engine_loaded()`` may reload the model
            #   (5-30s in production) → a short timeout would always
            #   expire. We use a longer 2.0s timeout so that tests
            #   which exercise the model-fail path
            #   (``test_start_dictation_fails_gracefully_if_model_still_unavailable``)
            #   observe the discard/recording-reset side effects before
            #   the assertion. In production, the 2.0s join expires and
            #   the F2 thread returns while the worker continues —
            #   still a ~15x improvement over the pre-fix 5-30s block.
            #
            # The adaptive check reads ``active_transcriber()`` BEFORE
            # spawning the worker (the F2 thread's last read of model
            # state). The worker's own re-check after the load is the
            # authoritative one.
            _pre_load_active = app.models.active_transcriber()
            _pre_load_model_loaded = _pre_load_active is not None and getattr(_pre_load_active, "is_loaded", False)
            _join_timeout = 0.1 if _pre_load_model_loaded else 2.0
            # Release ``_toggle_lock`` for the duration of the
            # bounded worker join. The worker (model load + post-load)
            # already runs WITHOUT the lock, but the F2 dispatch thread
            # must not HOLD it during the join either — otherwise a
            # concurrent ``stop()`` / ``cancel()`` from another thread
            # (auto-stop timer, ESC cancel hotkey, tray) would block for
            # the whole join window (up to 2.0s on the idle-unload
            # reload path). The lock is re-acquired in a ``finally`` so
            # it is always restored even if the join raises, keeping the
            # ``with controller._toggle_lock:`` in ``start()`` balanced
            # (RLock — same owning thread, count 1 → 0 → 1 → 0).
            controller._toggle_lock.release()
            try:
                with contextlib.suppress(Exception):
                    worker.join(timeout=_join_timeout)
            finally:
                controller._toggle_lock.acquire()
        except Exception as e:
            log.exception("[DICTATION] Failed to start recording: %s", e)
            controller._cancel_streaming_session()
            # If ``recorder.start()`` succeeded but a later step
            # (streaming session, tray state, bubble show, volume duck)
            # raised, the PortAudio input stream is left open — call
            # ``discard()`` best-effort to release it so we don't leak
            # the mic. Guarded so a second failure during teardown
            # doesn't mask the original exception in the log.
            try:
                app.recorder.discard()
            except Exception:
                log.debug(
                    "[DICTATION] recorder.discard() during start-failure teardown raised (best-effort cleanup)",
                    exc_info=True,
                )
            # Force-reset the recording flag so the next ``start()`` call
            # doesn't no-op on a stale ``recording==True``. ``discard()``
            # normally does this, but we set it explicitly here so the
            # invariant holds even if ``discard()`` raised (the
            # best-effort guard above) or if a subclass overrode
            # ``discard()`` to skip the flag reset.
            app.recorder.recording = False
            # Surface the backend's reason when the pipeline raised a
            # typed error (permission denied / no input device); raw
            # exception text stays out of the tray (paths, device names).
            _start_fail_msg = _recording_start_failure_message(e)
            app.tray.set_state(AppState.ERROR, _start_fail_msg)
            # Notification mirrors the tooltip: for typed failures the
            # reason IS the actionable message (permission / no device);
            # for unknown failures keep the "check the log" guidance.
            # Raw exception text never reaches the user (paths, device
            # names, hostnames) — the full traceback is logged above.
            if _start_fail_msg != i18n.t("state.recording_controller.recording_failed"):
                app.tray.notify(APP_NAME, f"Could not start recording.\n{_start_fail_msg}")
            else:
                app.tray.notify(
                    APP_NAME,
                    i18n.t("notify.recording_controller.start_failed"),
                )
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {"type": "error", "data": {"message": "Could not start recording", "kind": "recording_start"}}
                )
            except Exception:
                pass
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))

    # ── Start worker (model load + post-load) ────────────────────────

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
        timer cancel, ``recorder.start()``, tray state, bubble show,
        volume duck) inside ``_start_impl``, then spawns THIS worker and
        returns after a bounded ``join(timeout=0.1)``. The worker performs
        the potentially-slow model load (5-30s on idle-unload reload) and
        the post-load steps (active_transcriber check, Whisper fallback,
        streaming-session setup).

        Rationale: pre-fix, the F2 dispatch thread blocked for 5-30s
        inside ``ensure_active_engine_loaded()`` on the idle-unload
        reload path. With a single-dispatch-thread hotkey backend
        (pynput), this blocked ALL hotkeys — including ESC cancel — for
        the entire reload window. Moving the load to a daemon worker
        frees the dispatch thread to process subsequent hotkey events
        (e.g. a second F2 to stop the recording whose model is still
        loading).

        The worker runs WITHOUT ``_toggle_lock``. The F2 thread holds the
        lock via the ``with`` block in ``start()`` for the duration of
        the bounded join (0.1s), then releases it on ``_start_impl``
        return. The worker doesn't need the lock because:
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
        try:
            # Load / reload the active engine. This is the potentially
            # 5-30s step on the idle-unload reload path. The worker
            # runs without ``_toggle_lock`` (see method docstring).
            app.models.ensure_active_engine_loaded()

            # Re-check recorder + busy state AFTER the load completes.
            # The load took 5-30s on idle-unload; a concurrent
            # ``stop()`` or ``cancel()`` could have run in that window
            # (the F2 thread released ``_toggle_lock`` after the bounded
            # join, so concurrent stop/cancel proceeded). A concurrent
            # ``_stop_impl`` calls ``recorder.stop()`` (flipping
            # ``app.recorder.recording`` to False) AND clears
            # ``_busy_event`` (busy=True) — so we check BOTH conditions.
            #
            # Polarity: ``_busy_event.is_set() == True`` means NOT busy;
            # ``is_set() == False`` means busy.
            if not app.recorder.recording or not app._busy_event.is_set():
                log.info(
                    "[DICTATION] Recorder stopped or app became busy during "
                    "model load — aborting post-load steps (cycle=%s)",
                    cycle_id,
                )
                return

            active = app.models.active_transcriber()
            if active is None or not getattr(active, "is_loaded", False):
                # No engine loaded — try to load Whisper as a fallback.
                log.warning("[DICTATION] No loaded engine found, lazy-loading Whisper as fallback")
                app.models.fallback_to_whisper(notify_on_failure=True)
                active = app.models.active_transcriber()
                if active is None or not getattr(active, "is_loaded", False):
                    log.error("[DICTATION] Whisper fallback also failed, cannot record")
                    # The recorder is already running — discard it so we
                    # don't leak the mic stream or leave the app in a
                    # recording state with no engine to transcribe.
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
            # must start AFTER the model-load block above.
            controller._start_streaming_session_if_enabled()
        except Exception as e:
            log.exception("[DICTATION] Start worker failed: %s", e)
            controller._cancel_streaming_session()
            # If ``recorder.start()`` succeeded but a later step in the
            # worker raised, the PortAudio input stream is left open —
            # call ``discard()`` best-effort to release it.
            try:
                app.recorder.discard()
            except Exception:
                log.debug(
                    "[DICTATION] recorder.discard() during start-worker teardown raised (best-effort)",
                    exc_info=True,
                )
            app.recorder.recording = False
            # Same typed-reason mapping as the ``_start_impl`` failure
            # path — the worker can fail on recorder-start-upstream
            # errors too (permission / no device), and the tooltip must
            # show why, not a bare "Recording failed".
            _start_fail_msg = _recording_start_failure_message(e)
            app.tray.set_state(AppState.ERROR, _start_fail_msg)
            if _start_fail_msg != i18n.t("state.recording_controller.recording_failed"):
                app.tray.notify(APP_NAME, f"Could not start recording.\n{_start_fail_msg}")
            else:
                app.tray.notify(
                    APP_NAME,
                    i18n.t("notify.recording_controller.start_failed"),
                )
            try:
                from voice_typer.server import event_bus

                event_bus.publish(
                    {
                        "type": "error",
                        "data": {
                            "message": "Could not start recording",
                            "kind": "recording_start",
                        },
                    }
                )
            except Exception:
                pass
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))
        finally:
            complete_event.set()

    def stop(self, controller) -> None:
        """Stop recording and transcribe in background.

        Acquires ``_toggle_lock`` (an RLock) so the auto-stop Timer
        thread's ``_stop_dictation`` -> ``controller.stop()`` call
        serializes against an in-flight ``toggle()`` / ``start()`` /
        ``stop()`` / ``cancel()`` on any other thread. RLock allows the
        re-entrant path ``toggle() -> app._stop_dictation() ->
        controller.stop()`` to re-acquire without deadlocking.
        """
        with controller._toggle_lock:
            controller._stop_impl()

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
        # If a stop+transcribe worker is already running
        # (``_busy_event`` cleared = busy=True), this is a duplicate
        # ``stop()`` call (e.g. auto-stop Timer firing while the user's
        # F2-toggle stop is still in progress). Return early — the
        # in-progress worker will complete the stop+transcribe cycle.
        if not app._busy_event.is_set():  # busy = True
            log.debug(
                "[DICTATION] _stop_dictation: stop already in progress (busy=True), no-op (cycle=%s)",
                app._cycle_id,
            )
            return
        # Emit ``recording_stopped`` push event. Log push failures (see
        # comment in start() above).
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "recording_stopped"})
        except Exception:
            log.warning(
                "[SOUND] failed to push recording_stopped event",
                exc_info=True,
            )

        # Recording is stopping — release keyboard ownership back to
        # "normal" so the ESC cancel hotkey stops firing. This MUST
        # happen before ``recorder.stop()`` so that any key events
        # processed during the stop sequence see the correct owner.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            keyboard_ownership().set_owner("normal", reason=f"recording stopped (cycle={app._cycle_id})")
        except Exception:
            log.debug(
                "[DICTATION] failed to reset keyboard ownership on stop",
                exc_info=True,
            )

        # Cancel any stale pending timers
        app._cancel_pending_timers()

        log.info("[DICTATION] Stopping recording... (cycle=%s)", app._cycle_id)

        app._busy_event.clear()  # busy = True

        # Detach the RMS callback so the audio path cannot keep pushing
        # levels after the stream is closed.
        app.recorder.on_rms_level = None
        # Push a final zero-level event so the renderer resets its
        # animation envelope. Without this, the dots stay frozen at
        # their last active height because rawLevelRef is never set
        # back to 0.
        app._waveform_bubble.reset_level()
        # NEW-BUBBLE-TRANSCRIBING: Instead of hiding the bubble
        # immediately, switch it to "transcribing" state so the user
        # sees visual feedback that processing is in progress. The
        # bubble shows "Transcribing…" text with an animated dots
        # indicator. Once the transcription pipeline finishes
        # (DictationPipeline._copy_and_paste), the bubble is hidden or
        # set back to idle depending on bubble_behavior.
        app._waveform_bubble.set_state("transcribing")

        _captured_cycle_id = app._cycle_id

        # Spawn a daemon worker thread that performs ``recorder.stop()``
        # + audio-stats + transcription. The hotkey thread returns after
        # a bounded ``join(timeout=0.1)`` so it stays responsive (the F2
        # hotkey backend's single dispatch thread is no longer blocked
        # for ~2.4s on every stop). The worker is the SAME thread that
        # runs the transcription pipeline (``name="Transcription"``) —
        # ``recorder.stop()`` is its first step, so the watchdog (which
        # monitors ``_transcription_thread``) also covers the stop phase.
        #
        # The worker body used to be a ~170-line nested closure
        # (``stop_and_transcribe_worker``) defined inline below. It is
        # now extracted to ``_stop_and_transcribe_worker_entry``
        # (recorder stop + error recovery) which delegates to
        # ``_run_stop_and_transcribe(audio, cycle_id)`` (the
        # transcription body). Splitting the closure into two bound
        # methods makes the transcription body unit-testable (call
        # ``_run_stop_and_transcribe(fake_audio, cycle_id)`` directly
        # without spinning up a real recorder) AND eliminates the nested
        # closure so ``_stop_impl`` has no inline function definitions.
        # Behavior is preserved exactly: ``recorder.stop()`` still runs
        # on the daemon worker thread (not the hotkey thread), error
        # recovery is unchanged, the streaming-session stashing logic is
        # preserved verbatim, and the transcription pipeline receives
        # the same ``audio`` / ``cycle_id`` / ``recorded_rms`` /
        # ``duration`` arguments in the same order.

        # Write ``controller._transcription_thread`` under
        # ``_watchdog_lock`` so the watchdog daemon thread's read in
        # ``_force_recover_from_stuck_transcription`` (which also
        # acquires the same lock) cannot observe a stale ``None`` mid-
        # assignment or a torn reference. The lock is short-held (just
        # the assignment + start() returning quickly) and start() never
        # blocks, so there is no risk of holding it during model work.
        with controller._watchdog_lock:
            controller._transcription_thread = threading.Thread(
                target=controller._stop_and_transcribe_worker_entry,
                args=(_captured_cycle_id,),
                name="Transcription",
                daemon=True,
            )
            controller._transcription_thread.start()

        # Bounded wait for the worker to make progress. In tests where
        # ``recorder.stop()`` is fast (mocked), the worker may finish
        # entirely within this window — preserving the contract that
        # ``recorder.stop()`` is observable as called immediately after
        # ``stop()`` returns. In production where ``recorder.stop()``
        # takes ~2.4s, the join times out at 0.1s and the hotkey thread
        # returns while the worker continues — a 24x responsiveness
        # improvement over the pre- synchronous block. The worker is a
        # daemon, so it doesn't block process exit if the user quits
        # during the stop+transcribe cycle.
        with contextlib.suppress(Exception):
            controller._transcription_thread.join(timeout=0.1)

    def _stop_and_transcribe_worker_entry(self, controller, cycle_id: str) -> None:
        """Daemon worker-thread entry point — extracted from the former
        nested closure ``stop_and_transcribe_worker``.

        Performs the FIRST step of the stop+transcribe pipeline:
        ``recorder.stop()`` (which blocks ~2.4s in production: 300ms
        stream-teardown poll + 2.0s audio-worker drain + 2.0s event-worker
        drain + np.concatenate + resample) plus the mic-watcher cleanup,
        with full error recovery if ``recorder.stop()`` raises.

        On success, delegates the transcription body to
        ``controller._run_stop_and_transcribe(audio, cycle_id)`` so the
        transcription pipeline is unit-testable in isolation (pass a
        fake ``audio`` sample directly).

        Rationale: this method runs on the daemon "Transcription" thread
        (NOT the hotkey thread), so the ~2.4s ``recorder.stop()`` block
        does not stall the F2 hotkey backend's single dispatch thread.
        The hotkey thread spawns this worker and returns after a bounded
        ``join(timeout=0.1)``.
        """
        app = controller._app
        try:
            # ``recorder.stop()`` is the FIRST step. Pre-fix this ran
            # synchronously on the hotkey thread.
            audio = app.recorder.stop()
            # Clear the active-mic-id on the watcher so it stops
            # checking for the now-stopped recording's mic. Best-effort:
            # a missing mic_watcher is silently skipped.
            with contextlib.suppress(Exception):
                mic_watcher = getattr(app.recorder, "_mic_watcher", None)
                if mic_watcher is not None:
                    mic_watcher.set_active_mic_id(None)
        except Exception:
            log.exception("[DICTATION] Failed to stop recording (worker)")
            controller._cancel_streaming_session()
            app._restore_volume()
            # Best-effort restart of the level_monitor for the
            # always-visible bubble (the recorder's stream is closed even
            # on failure — ``recorder.stop()`` raised but the stream
            # teardown is the recorder's responsibility).
            controller._maybe_restart_level_monitor_for_always_visible_bubble(app)
            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.stop_failed"))
            # Critical — bypass the notification toggle (dictation failed,
            # the user must be told even if they disabled normal
            # notifications). No {error} interpolation — exception text
            # can leak sensitive paths. The full exception is logged
            # above.
            app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.stop_failed"),
            )
            app._busy_event.set()  # busy = False
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        controller._run_stop_and_transcribe(audio, cycle_id)

    def _run_stop_and_transcribe(self, controller, audio, cycle_id: str) -> None:
        """Transcription pipeline body — extracted from the former nested
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
           ``session.finalize(audio)`` — the streaming fast path.
        8. Start the persistent watchdog thread (RACE-013).
        9. Stash audio in ``controller._current_audio`` (privacy) then
           immediately capture-and-clear so the slot doesn't retain the
           bytes for the transcription duration.
        10. Run ``DictationPipeline.run(...)``.

        Unit-testable: call ``controller._run_stop_and_transcribe(fake_audio,
        cycle_id)`` directly with a mock ``app.recorder`` / ``app.tray``
        / ``app.config`` to exercise the transcription body without
        spinning up a real recorder or hotkey thread.
        """
        app = controller._app

        # Surface ring-buffer overflow detected during the recording.
        # ``_dropped_ring_chunks`` is reset to 0 on the next
        # ``recorder.start()`` (``recording/session_state.py``), so this
        # is the last chance to log it for the session that just ended.
        # A non-zero value means the audio worker couldn't keep up with
        # the callback — chunks were silently dropped, which can cause
        # incomplete or corrupted transcriptions. ``getattr``-guarded so
        # mock recorders (or older subclasses) without the attribute do
        # not crash the stop path.
        dropped = getattr(app.recorder, "_dropped_ring_chunks", 0)
        if dropped:
            log.warning(
                "[DICTATION] Ring buffer overflow during recording: "
                "%d chunks dropped (cycle=%s). Audio worker could not "
                "keep up; transcription may be incomplete.",
                dropped,
                app._cycle_id,
            )

        # Restore system volume immediately — don't wait for transcription
        # (which takes seconds) before the user gets their audio back.
        app._restore_volume()

        # Now that the Recorder's InputStream is closed, restart the
        # level_monitor if the bubble is always_visible so the ambient
        # level bar continues updating. Best-effort.
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
            "[DICTATION] Recording stopped%s of audio, recorded_rms=%.4f, busy=True (cycle=%s)",
            format_duration(duration),
            recorded_rms,
            cycle_id,
        )

        if duration < 0.5:
            log.info("[DICTATION] Audio too short, skipping transcription")
            controller._cancel_streaming_session()
            app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.too_short"))
            app._busy_event.set()  # busy = False
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
        # the final transcription. Pre-fix this used
        # ``get_streaming_session()`` + private ``_cancel_event.set()``
        # which left the session in ``controller._streaming_session``
        # across the entire transcription window and depended on a
        # fragile private-attribute contract (silently swallowed by
        # ``contextlib.suppress(Exception)``).
        #
        # The original implementation called
        # ``_cancel_streaming_session()`` which popped AND discarded the
        # session. That forced ``DictationPipeline._transcribe`` (which
        # calls ``pop_streaming_session()``) to always see ``None`` and
        # fall back to batch transcription — even when a streaming
        # session was active and could have provided the finalized
        # transcript via ``session.finalize(audio)``. The streaming fast
        # path was effectively dead code on the stop path (root cause of
        # the ``test_stop_dictation_uses_streaming_final_text`` failure).
        #
        # The fix: pop the session + signal cancel (non-blocking) so the
        # streaming worker stops, BUT stash the session in
        # ``controller._pending_finalize_session`` so the pipeline's
        # ``pop_streaming_session()`` (which checks the stash as a
        # fallback) can retrieve it and call ``finalize()``. The stash
        # is written under ``_streaming_session_lock`` (single lock
        # acquisition — same atomicity guarantee as
        # ``pop_streaming_session``). ``session.cancel()`` is called
        # OUTSIDE the lock (it can be slow / blocking on a real worker
        # thread join in the streaming backend).
        #
        # ``finalize()`` itself calls ``cancel(blocking=True)`` which is
        # idempotent (``_cancel_event.set()`` on an already-set event is
        # a no-op), so the early non-blocking cancel here does not
        # double-join or race the worker.
        with controller._streaming_session_lock:
            # ``_stop_impl`` does NOT pop the session or signal cancel.
            # The streaming session stays in ``controller._streaming_session``
            # so the pipeline's ``pop_streaming_session()`` (called from
            # ``DictationPipeline.run``) can retrieve it and call
            # ``finalize(audio)`` for the streaming fast path. The
            # pipeline's ``finally`` block also calls ``session.cancel()``
            # on the popped session so the worker thread is signalled to
            # exit. Pre-cancelling here would prevent the session from
            # emitting its finalized text (a regression of the streaming
            # fast path covered by
            # ``test_stop_dictation_uses_streaming_final_text``).
            controller._pending_finalize_session = controller._streaming_session

        # RACE-013: Start persistent watchdog thread using Event.wait(timeout=90).
        controller._start_watchdog_thread()

        # ``transcribe_thread`` extracted to ``DictationPipeline`` class.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        # Privacy: hold audio bytes in a shared, clearable slot so
        # ``_force_recover_from_stuck_transcription`` can drop our
        # Python-side reference at cancel time. The worker reads from
        # ``controller._current_audio`` (no closure capture of the
        # local), so setting ``controller._current_audio = None`` at
        # force-recovery releases the bytes for GC.
        controller._current_audio = audio
        # Capture into a local and clear the shared slot BEFORE calling
        # ``pipeline.run()``. Pre-fix, the slot retained the audio for
        # the entire transcription duration (1-15 MB of float32).
        audio_bytes = controller._current_audio
        controller._current_audio = None

        pipeline = DictationPipeline(app)
        pipeline.run(
            audio=audio_bytes,
            duration=duration,
            recorded_rms=recorded_rms,
            cycle_id=cycle_id,
            watchdog=None,  # RACE-013: no longer using Timer-based watchdog
        )
        # Now that the transcription pipeline has fully returned (the
        # late-transcription check inside ``CancellationGuard`` has
        # already run, the paste-or-skip decision has been made),
        # discard this cycle's entry from ``_cancelled_cycle_ids``.
        # Without this discard, every cancelled cycle would linger in
        # the bounded registry until LRU-evicted at
        # ``_MAX_CANCELLED_IDS`` — the registry would always be
        # near-full of stale entries from cycles whose late
        # transcription was already observed + dropped. Discarding here
        # keeps the registry focused on cycles whose transcription is
        # STILL pending (i.e. the only entries that matter for the
        # ``CancellationGuard`` check). Best-effort: a missing cycle_id
        # is the common case (most cycles are never cancelled) and is
        # silently ignored by ``_discard_cancelled_cycle_id``.
        controller._discard_cancelled_cycle_id(cycle_id)

    def cancel(self, controller) -> None:
        """Feature: ESC to cancel -- cancel current recording/transcription.

        Previously, if ``recorder.discard()`` raised (PortAudio error,
        stream close race), the cancel path aborted before resetting tray
        state — leaving the tray stuck on RECORDING. We now guarantee the
        post-discard cleanup always runs.

        Set ``AppState.CANCELLING`` for ~200ms during cancel so the tray
        icon shows a distinct "cancelling" state instead of instantly
        transitioning RECORDING → IDLE (which flickers).

        Acquires ``_toggle_lock`` (an RLock) so the ESC cancel hotkey's
        call serializes against an in-flight ``toggle()`` / ``start()`` /
        ``stop()`` on any other thread. The hotkey backend fires ESC on
        a separate thread from F2, so without this lock, a
        near-simultaneous F2-toggle + ESC-cancel could race on
        ``app.recorder.recording`` and ``recorder.discard()``. RLock
        allows re-entrancy from any code path that already holds the lock
        (none currently, but kept symmetric with start/stop).
        """
        with controller._toggle_lock:
            controller._cancel_impl()

    def _cancel_impl(self, controller) -> None:
        """Inner cancel implementation, called under _toggle_lock."""
        app = controller._app

        # ESC: If no recording is active, the ESC cancel is a no-op. The
        # global ESC hotkey backend fires on every Escape press
        # regardless of whether a recording is in progress. Early-return
        # here avoids spurious CANCEL logs (which look like errors to
        # the user) and prevents unnecessary cleanup (streaming session
        # cancel, volume restore, bubble hide) when nothing is running.
        #
        # In addition to the ``recorder.recording`` check, we also
        # consult the KeyboardOwnership singleton. Even if
        # ``recorder.recording`` is True (e.g. stale state from a
        # previous session that wasn't cleaned up), if the frontend is
        # in hotkey capture mode we MUST NOT fire cancel — the frontend
        # owns the keyboard during capture.
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
            # ESC during the transcription phase. Pre-fix, this was a
            # silent no-op — the user pressed ESC to abort a stuck
            # transcription and nothing happened; they had to wait up to
            # 270s (3 watchdog firings × 90s) for
            # ``_force_recover_from_stuck_transcription`` to reset the
            # busy flag + tray. Post-fix: if the transcription thread is
            # alive AND the busy flag is set (busy=True, i.e.
            # ``_busy_event.is_set() == False``), we immediately mark
            # the cycle as cancelled (so the late transcription result
            # is NOT pasted when the ctranslate2 call eventually
            # completes) and force-recover the busy flag + tray so the
            # user can start a new recording right away.
            #
            # ctranslate2 / faster-whisper cannot be interrupted
            # mid-call (documented limitation) — we do NOT try to kill
            # the transcription thread. The thread continues running and
            # the late result is dropped by the pipeline's
            # ``_cancelled_cycle_ids`` check.
            with controller._watchdog_lock:
                t_thread = controller._transcription_thread
            if (
                t_thread is not None and t_thread.is_alive() and not app._busy_event.is_set()  # busy = True
            ):
                log.info(
                    "[CANCEL] ESC during transcription phase (cycle=%s) — marking cancelled + force-recovering",
                    app._cycle_id,
                )
                cycle_id = getattr(app, "_cycle_id", None)
                if cycle_id is not None:
                    # Use the bounded-registry helper so the set cannot
                    # grow unbounded across many cancel events (LRU
                    # eviction at ``_MAX_CANCELLED_IDS``).
                    controller._mark_cycle_cancelled(cycle_id)
                    log.info(
                        "[CANCEL] cycle %s marked cancelled — late transcription will not be pasted",
                        cycle_id,
                    )
                # ``_force_recover_from_stuck_transcription`` now also
                # cancels the streaming session. ``force=True`` so the
                # recovery is immediate regardless of the watchdog
                # firing count.
                try:
                    controller._force_recover_from_stuck_transcription(force=True)
                except Exception:
                    log.exception(
                        "[CANCEL] _force_recover_from_stuck_transcription raised during cancel (cycle=%s)",
                        app._cycle_id,
                    )
                return
            log.debug("[CANCEL] Cancel pressed but no recording active (cycle=%s) — no-op", app._cycle_id)
            return

        log.info("[CANCEL] Cancelling current dictation (cycle=%s)", app._cycle_id)

        # Release keyboard ownership back to "normal" so subsequent
        # Escape presses during the cancel cleanup don't re-enter the
        # cancel path.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

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
                # checking for the now-cancelled recording's mic.
                with contextlib.suppress(Exception):
                    mic_watcher = getattr(app.recorder, "_mic_watcher", None)
                    if mic_watcher is not None:
                        mic_watcher.set_active_mic_id(None)
                # Immediately secure-clear the audio buffers from memory
                # after discard. Without this, the numpy array holding
                # the user's voice can persist in RAM for 30+ minutes
                # until GC reclaims it. ``_secure_clear_session_caches``
                # zero-fills and deletes the cached audio arrays,
                # ensuring voice data is wiped immediately on cancel.
                app.recorder._secure_clear_session_caches()
            except Exception as e:
                # Don't abort the cancel path — fall through to ensure
                # tray state + busy flag are reset.
                log.exception(
                    "[CANCEL] Failed to discard recording (cycle=%s): %s",
                    app._cycle_id,
                    e,
                )

        # Always run these — even if discard failed.
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
        # idle so the visualizer bars don't stay frozen on screen)
        try:
            if app.config.bubble_behavior != "always_visible":
                app._waveform_bubble.hide()
            else:
                app._waveform_bubble.set_state("idle")
        except Exception:
            log.exception("[CANCEL] Failed to hide/set idle bubble")

        # Tray state + busy flag MUST be cleared so the user can press
        # F2 again after a cancel.
        app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.cancelled"))
        app._busy_event.set()
