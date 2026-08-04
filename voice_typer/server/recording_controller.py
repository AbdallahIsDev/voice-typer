"""#2 RecordingController — extracted from VoiceTyperApp.

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

import contextlib
import gc
import logging
import os
import threading
from collections import OrderedDict
from typing import Any

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.streaming import StreamingConfig, StreamingTranscriptionSession
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)


# Bounded cap for ``_cancelled_cycle_ids``. Each cancel event (ESC-during-
# transcription, watchdog force-recover) appends one cycle_id; without a
# cap, the set grew by one entry per cancel event forever — a slow memory
# leak on long-lived processes that get cancelled a lot (e.g. a user who
# habitually ESC-cancels half-finished dictations). The OrderedDict-based
# LRU eviction in ``_mark_cycle_cancelled`` keeps the registry at <=>
# this many entries, evicting the OLDEST entries first (entries are not
# re-touched on read, so oldest == least-recently-added). 1000 is well
# above the realistic working set (a user would have to cancel 1000
# distinct cycles within a single process lifetime for eviction to
# matter) and small enough that the per-entry memory cost (a ~40-byte
# str key + dict slot) is bounded to ~40 KB worst case.
_MAX_CANCELLED_IDS = 1000


class RecordingController:
    """Owns recording lifecycle + streaming session + silence/xrun callbacks.

    #2 extracted from VoiceTyperApp. The app passes itself
    (``app``) so RecordingController can:
    - Read ``app.config`` (recording_mode, streaming_*, silence_*)
    - Read/write ``app.recorder`` (Recorder instance)
    - Read/write ``app._busy_event`` (busy flag). NOTE ():
      ``app._busy_event`` uses INVERTED semantics —
      ``is_set() == True`` means NOT busy, ``is_set() == False`` means
      busy. This is because the event doubles as a "ready" signal
      (``wait()`` blocks while busy). All call sites in this module
      annotate the inverted meaning with ``# busy`` / ``# not busy`` /
      ``# busy = True`` / ``# busy = False`` comments. A full rename to
      ``_not_busy_event`` was considered but deferred as a large
      semantic change touching every caller.
    - Own ``self._transcription_thread`` / ``self._streaming_session``
      (: callers must read these via ``app.recording.X``)
    - Update ``app.tray`` state during recording
    - Call ``app._schedule_timer`` / ``app._cancel_pending_timers``
    - Call ``app.models.ensure_active_engine_loaded()`` / ``app._fallback_to_whisper()``
    - Call ``app.models.active_transcriber()``
    - Call ``app._duck_volume()`` / ``app._restore_volume()``
    - Call ``app._waveform_bubble`` show/hide/reset_level
    - Call ``app._audio_quality.reset()`` / ``app._finalize_audio_quality_report()``
    - Read ``app._cycle_id`` / increment ``app._cycle_counter``
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._streaming_session: StreamingTranscriptionSession | None = None
        # Stash for the streaming session that ``_stop_impl``'s
        # main transcription path pops + signals cancel on. The pipeline's
        # ``_transcribe`` step retrieves it via ``pop_streaming_session()``
        # (which checks this stash as a fallback) so it can call
        # ``session.finalize(audio)`` — the streaming fast path that
        # replaces batch transcription when a streaming session was active.
        # Pre-fix, ``_stop_impl`` called ``_cancel_streaming_session()``
        # which popped AND discarded the session, forcing the pipeline
        # into the batch fallback path even when streaming was enabled
        # (``test_stop_dictation_uses_streaming_final_text`` failure).
        # The stash is written + read under ``_streaming_session_lock``.
        self._pending_finalize_session: StreamingTranscriptionSession | None = None
        self._transcription_thread: threading.Thread | None = None
        # RACE-025: lifecycle serialization lock. Prevents
        # concurrent toggle/start/stop/cancel calls from different threads
        # (hotkey thread + tray thread + auto-stop Timer thread + ESC
        # cancel hotkey) from both passing the _busy_event /
        # recorder.recording check before either modifies it. Promoted
        # from a plain Lock to an RLock so the re-entrant call path
        # toggle() -> app._stop_dictation() -> self.stop() (and the
        # matching start() path) does NOT self-deadlock when start/stop
        # acquire the same lock at entry. Auto-stop callbacks fire via
        # _schedule_timer(0, _stop_dictation) — a fresh Timer thread that
        # calls self.stop() directly. Without acquiring the lock there,
        # two near-simultaneous stop() calls (one from toggle, one from
        # auto-stop Timer) could both pass the not app.recorder.recording
        # check before either calls recorder.stop(). ESC cancel() had the
        # same exposure.
        self._toggle_lock = threading.RLock()
        # watchdog firing counter for the current transcription
        # cycle. Reset to 0 whenever a new transcription thread starts.
        # After _watchdog_max_firings consecutive watchdog expirations
        # with the worker still alive, we force-recover instead of
        # re-arming — otherwise a genuinely deadlocked ctranslate2 call
        # leaves the app stuck busy forever.
        self._watchdog_firings = 0
        self._watchdog_max_firings = 3
        self._watchdog_lock = threading.Lock()
        # dedicated lock for _streaming_session. Previously
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
        self._watchdog_thread: threading.Thread | None = None
        #  (IMPROVE-mode run, 2026-07-21): bounded LRU registry of
        # cycle_ids that were force-cancelled by the watchdog (or by ESC
        # during the transcription phase). ``DictationPipeline.run()``
        # checks this registry BEFORE calling ``_copy_and_paste`` — if
        # the cycle was cancelled (because the transcription thread took
        # >4.5min and the watchdog fired), the late transcription is NOT
        # pasted into whatever window currently has focus. Prevents data
        # corruption when the user alt-tabs away during a stuck
        # transcription and the ctranslate2 call eventually completes
        # 5-30 min later.
        #
        # Pre-fix this was a plain ``set[str]`` and the comment
        # claimed "Entries are discarded by the pipeline's finally block
        # to keep the set bounded" — which was FALSE (grep found NO
        # discard/remove/pop/clear calls anywhere; entries were only
        # ever ADDED, on ESC cancel and on watchdog force-recover). The
        # set grew by one entry per cancel event forever. Now it's a
        # bounded ``OrderedDict`` (LRU eviction at ``_MAX_CANCELLED_IDS``
        # = 1000 entries, evicting the oldest first). The membership
        # check (``cycle_id in self._cancelled_cycle_ids``) works on
        # dict keys exactly like it worked on the set, so all read
        # sites (``dictation_stages.CancellationGuard``,
        # ``dictation_pipeline._AbortWatcher``) are unchanged. Mutations
        # go through ``_mark_cycle_cancelled`` (add + LRU-evict) and
        # ``_discard_cancelled_cycle_id`` (best-effort remove, called
        # from ``_run_stop_and_transcribe`` after the pipeline returns
        # so a cycle that completed normally does not linger in the
        # registry).
        self._cancelled_cycle_ids: OrderedDict[str, None] = OrderedDict()
        self._cancelled_cycle_ids_lock = threading.Lock()
        #  (privacy): shared, clearable slot holding the audio bytes
        # captured by ``stop()`` for the transcription thread. Pre-fix,
        # ``audio`` was a closed-over local of the ``transcribe_thread``
        # closure, so any debugger / memory dump could recover the raw
        # voice audio from process memory for as long as the stuck
        # ctranslate2 call held the local alive (5-30 min after a user-
        # initiated cancel). Reading from this slot in the thread (and
        # clearing it in ``_force_recover_from_stuck_transcription``)
        # drops our Python-side reference at force-recovery time; the
        # unavoidable C-level retention by a stuck ctranslate2 call is a
        # documented limitation that requires engine-level changes.
        self._current_audio: Any = None
        # track whether the level_monitor was actively running when
        # we started recording, so ``_stop_impl`` can restart it if
        # ``bubble_behavior == "always_visible"``. The level_monitor and
        # the Recorder cannot share the mic device concurrently (Windows
        # MME device-conflict; Linux/macOS doubles audio-path CPU). The
        # frontend tries to stop the level_monitor before
        # ``recording_start``, but if the IPC races or the frontend
        # forgets, the backend guard in
        # ``_stop_level_monitor_for_recorder_start`` is the safety net.
        self._level_monitor_was_active: bool = False
        # wire the  active-mic-lost hooks + the
        # ``on_device_lost`` callback so the OS-event-driven watcher
        # can cancel in-flight recordings sub-second when the active
        # mic disappears (USB/BT unplug), instead of falling through
        # to the misleading "silence detected" message after 1-2s of
        # retries. Best-effort: guarded so a recorder without a mic
        # watcher (tests, mock recorders) doesn't fail init.
        self._wire_mic_watcher_hooks()

    def _wire_mic_watcher_hooks(self) -> None:
        """register the active-mic-lost callback + device-id
        provider + on_device_lost callback on the recorder.

        Idempotent — safe to call multiple times. The hooks are stored
        on the recorder's ``_mic_watcher`` (a property delegating to
        ``DeviceManager._mic_watcher``) and on the recorder itself
        (``on_device_lost``). All assignments are best-effort and
        wrapped in ``contextlib.suppress`` so a partially-initialized
        recorder (or a test mock) doesn't crash RecordingController
        construction.
        """
        app = self._app
        recorder = getattr(app, "recorder", None)
        if recorder is None:
            return
        # Wire on_device_lost so the terminal "max retries reached"
        # path (recorder.py:_handle_device_disconnect) fires the
        # dedicated "Microphone disconnected" notification instead of
        # falling through to on_silence_auto_stop.
        with contextlib.suppress(Exception):
            recorder.on_device_lost = self.on_device_lost
        # Wire the  active-mic-lost hooks. The mic_watcher
        # property may return None on platforms where the watcher
        # failed to start (macOS without the CoreAudio bridge).
        mic_watcher = getattr(recorder, "_mic_watcher", None)
        if mic_watcher is None:
            return
        with contextlib.suppress(Exception):
            mic_watcher.set_on_active_mic_lost(self.on_active_mic_lost)
        with contextlib.suppress(Exception):
            mic_watcher.set_device_id_provider(self._list_active_mic_ids)

    def _list_active_mic_ids(self) -> list:
        """return the current list of microphone IDs for the
        active-mic-lost watcher's membership check.

        The watcher calls this once per OS device-change event (after
        the cache-invalidation callback runs) and checks whether the
        active mic_id (set in :meth:`_start_impl` via
        ``set_active_mic_id``) is still present. If not,
        ``on_active_mic_lost`` fires.

        Returns the int ``index`` (not the str ``id``)
        so the membership check ``active_mic_id not in current_ids``
        compares int-to-int. Pre-fix this returned ``m.get("id")``
        (a str like ``"5"``), but ``_start_impl`` passes
        ``set_active_mic_id(resolved)`` where ``resolved`` is the int
        returned by ``recorder._resolve_device()`` (or
        ``recorder._effective_device``). The int-vs-str mismatch meant
        the membership check ALWAYS failed on the first device-change
        event after recording started, so ``on_active_mic_lost`` fired
        spuriously and stopped the recording even though the mic was
        still present. Returning ``m.get("index")`` (an int) makes the
        comparison int-to-int and matches the format
        ``set_active_mic_id`` is called with.
        """
        try:
            return [m.get("index") for m in self._app.list_microphones() if m.get("index") is not None]
        except Exception:
            log.debug("[DICTATION] _list_active_mic_ids failed", exc_info=True)
            return []

    def _mark_cycle_cancelled(self, cycle_id: str) -> None:
        """Record a cycle_id as force-cancelled (watchdog / ESC-during-
        transcription) with LRU eviction at ``_MAX_CANCELLED_IDS``.

        Thread-safe: acquires ``_cancelled_cycle_ids_lock`` for the
        check-then-insert-then-evict sequence so two concurrent
        cancellations cannot both pass the membership check and both
        append (which would let the dict momentarily exceed the cap).

        The OrderedDict's insertion order is the eviction order —
        ``popitem(last=False)`` removes the OLDEST entry. We do NOT
        ``move_to_end`` on an existing key (re-touch on read is not
        part of the contract; the registry only grows when a NEW
        cancel event fires, and old entries are evicted FIFO once the
        cap is reached). This matches the set semantics pre-fix
        (a set has no ordering at all) while bounding the memory cost.

        Duck-typed for tests: if ``_cancelled_cycle_ids`` is a plain
        ``set`` (the pre-fix type, still used by tests that construct
        a controller via ``__new__`` and assign ``set()`` directly),
        we fall back to ``set.add()`` and skip the LRU eviction
        (a set has no insertion order, so FIFO eviction is undefined).
        Production always uses the ``OrderedDict`` from ``__init__``.
        """
        with self._cancelled_cycle_ids_lock:
            if cycle_id in self._cancelled_cycle_ids:
                # Already cancelled — no-op (the watchdog / ESC may
                # fire more than once for the same cycle; idempotent).
                return
            if isinstance(self._cancelled_cycle_ids, set):
                # Test-double path: plain set has no ordering, so no
                # LRU eviction. The set membership check still works
                # for the ``CancellationGuard`` lookup. Production
                # uses an OrderedDict (see ``__init__``) which DOES
                # support eviction.
                self._cancelled_cycle_ids.add(cycle_id)
                return
            self._cancelled_cycle_ids[cycle_id] = None
            if len(self._cancelled_cycle_ids) > _MAX_CANCELLED_IDS:
                # Evict the OLDEST entry (FIFO). ``popitem(last=False)``
                # returns ``(key, value)``; we discard both — only the
                # key matters for the membership check.
                self._cancelled_cycle_ids.popitem(last=False)

    def _discard_cancelled_cycle_id(self, cycle_id: str) -> None:
        """Best-effort removal of a cycle_id from the cancelled registry.

        Called from ``_run_stop_and_transcribe`` after the pipeline
        returns (whether the cycle was cancelled or not) so a cycle
        that completed normally — or whose late transcription has
        already been observed + dropped by ``CancellationGuard`` —
        does not linger in the registry until the ``_MAX_CANCELLED_IDS``
        cap evicts it years later.

        Thread-safe: acquires ``_cancelled_cycle_ids_lock``. Silent
        no-op if ``cycle_id`` is not present (the common case — most
        cycles are never cancelled, so there's nothing to discard).

        Duck-typed for tests: handles both ``set`` (``set.discard``)
        and ``OrderedDict`` (``dict.pop`` with KeyError suppression).
        """
        with self._cancelled_cycle_ids_lock:
            if isinstance(self._cancelled_cycle_ids, set):
                self._cancelled_cycle_ids.discard(cycle_id)
                return
            with contextlib.suppress(KeyError):
                self._cancelled_cycle_ids.pop(cycle_id)

    def _publish_microphone_disconnected_event(self) -> None:
        """Emit the dedicated ``microphone_disconnected`` IPC event.

        Extracted from :meth:`on_device_lost` so the
        fast-path (:meth:`on_active_mic_lost`) and the slow-path
        (:meth:`on_device_lost`) both surface the same IPC banner to
        the renderer. Pre-fix, only the slow path (max-retries-reached)
        published the event; the fast path (OS-event-driven
        active-mic-lost, sub-second USB/BT unplug detection) skipped
        it, so the renderer showed no banner for the most common
        unplug scenario.

        Mirrors the ``on_microphone_permission_revoked`` pattern
        (best-effort publish with a logged suppress). The event_bus
        module is a leaf dependency, but if the import or publish
        raises (e.g. during shutdown teardown) the caller still
        proceeds to schedule the stop.
        """
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "microphone_disconnected"})
        except Exception:
            log.debug(
                "[DICTATION] failed to publish microphone_disconnected event",
                exc_info=True,
            )

    def on_device_lost(self) -> None:
        """handle the terminal 'max disconnect retries reached'
        case with a dedicated 'Microphone disconnected' notification.

        Distinct from ``on_silence_auto_stop`` so the user sees an
        accurate 'microphone disconnected' message rather than the
        misleading 'silence detected' message. The actual stop is
        scheduled off this thread (which is the recorder's
        disconnect-retry thread) to mirror the deadlock-avoidance
        pattern in ``on_silence_auto_stop``.
        """
        log.warning("[DICTATION] Microphone disconnected mid-recording -- stopping after max retries")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                "Microphone disconnected. Recording stopped. Reconnect the microphone to resume.",
            )
        # Emit a dedicated IPC event so the renderer can show a banner
        # (distinct from the silence / max-duration auto-stop toast).
        self._publish_microphone_disconnected_event()
        # Stop the recording off this thread (mirror the
        # on_silence_auto_stop pattern).
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_active_mic_lost(self) -> None:
        """handle the OS-event-driven active-mic-lost signal
        from ``MicrophoneDeviceWatcher``.

        The watcher fires this when it detects a device-list change AND
        the active mic_id (set in :meth:`_start_impl`) is no longer in
        the freshly-queried device list. This is sub-second detection
        of USB/BT unplug mid-recording — faster than the 1-2s
        zero-fill-chunk retry path in ``_handle_device_disconnect``.

        Now publishes the same
        ``microphone_disconnected`` IPC event as :meth:`on_device_lost`
        (via the shared ``_publish_microphone_disconnected_event``
        helper) so the renderer surfaces a banner for the fast-path
        unplug case too. Pre-fix, only the slow-path published.

        Scheduled stop (mirrors ``on_silence_auto_stop``) so we don't
        deadlock on ``Recorder._lock`` if the watcher thread holds it.
        """
        log.warning("[DICTATION] Active microphone lost (OS event) -- stopping recording")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                "Microphone was unplugged. Recording stopped.",
            )
        # Mirror the slow-path (on_device_lost): emit the dedicated IPC
        # event so the renderer can show a banner.
        self._publish_microphone_disconnected_event()
        self._app._schedule_timer(0, self._app._stop_dictation)

    # ── Streaming session accessors ────────────────────────────────────

    def get_streaming_session(self) -> StreamingTranscriptionSession | None:
        """Thread-safe accessor for the active streaming session.

        now guarded by ``_streaming_session_lock``.
        """
        with self._streaming_session_lock:
            return self._streaming_session

    def set_streaming_session(self, session_or_none: StreamingTranscriptionSession | None) -> None:
        """Thread-safe setter for the active streaming session.

        now guarded by ``_streaming_session_lock``.
        """
        with self._streaming_session_lock:
            self._streaming_session = session_or_none

    def pop_streaming_session(self) -> StreamingTranscriptionSession | None:
        """Atomically get AND clear the streaming session.

        Pre-fix, ``_cancel_streaming_session`` did:
            session = self.get_streaming_session()   # lock acquire/release #1
            self.set_streaming_session(None)          # lock acquire/release #2
        This left a TOCTOU window between the two lock acquisitions: a
        concurrent ``_start_streaming_session_if_enabled`` could install
        a NEW session that the subsequent ``set_streaming_session(None)``
        would clobber — cancelling a session that was just freshly started.

        This method does the get-and-clear under a SINGLE lock
        acquisition, eliminating the race.

        If the main slot is empty, also drains the
        ``_pending_finalize_session`` stash. ``_stop_impl``'s main
        transcription path pops the active session, signals cancel, and
        stashes it here so the pipeline's ``_transcribe`` step can
        retrieve it (via this method) and call ``session.finalize(audio)``
        — the streaming fast path. Without this fallback, the pipeline
        would always see ``None`` (the session was already popped by
        ``_stop_impl``) and fall back to batch transcription even when
        streaming was enabled.
        """
        with self._streaming_session_lock:
            session = self._streaming_session
            self._streaming_session = None
            if session is None:
                # Drain the pending-finalize stash so the
                # pipeline can retrieve the session that _stop_impl
                # popped + signalled cancel on. ``getattr`` default
                # handles test fixtures that build a controller via
                # ``RecordingController.__new__(...)`` without calling
                # ``__init__`` (which is where the stash is declared).
                session = getattr(self, "_pending_finalize_session", None)
                self._pending_finalize_session = None
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
        # the cycle counter is NOT incremented here. Pre-fix,
        # every blocked / queued / errored toggle consumed a cycle ID,
        # producing non-contiguous cycle numbers in the log trail (a
        # "cycle #5" with no corresponding recording was confusing).
        # The counter is now incremented only when we commit to a real
        # start/stop below, so cycle IDs map 1:1 to actual dictations.
        # Early-return log lines reference the PREVIOUS cycle's
        # ``app._cycle_id`` for correlation.

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
                i18n.t("state.recording_controller.loading_queued"),
            )
            return

        if active is None:
            # the previous background model load FAILED (or never
            # started). ``_model_load_thread`` is None because the loader's
            # ``finally`` block nulls it on exit, and ``active_transcriber()``
            # returns None because no engine was successfully constructed.
            #
            # Pre-fix, this branch only set the tray to "starting up" and
            # returned — so pressing F2 after a model-load failure (the
            # exact recovery the model_manager's tray message instructed
            # the user to perform) did nothing. The user was forced to
            # restart the app to recover the ASR engine.
            #
            # Now: re-trigger ``start_background_load()`` (idempotent — it
            # returns immediately if a load is already running). Set
            # ``_pending_dictation=True`` so the loader's ``finally`` block
            # auto-starts the dictation if the retry succeeds. The tray
            # shows "Retrying model load..." so the user sees the retry
            # is actually happening (instead of the misleading "starting
            # up -- please wait..." which implied passive waiting).
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
                # itself raised (extremely unlikely — start_background_load
                # only constructs a Thread).
                app.tray.set_state(
                    AppState.LOADING,
                    i18n.t("state.recording_controller.starting_up"),
                )
            return

        # commit to a real start/stop — NOW increment the
        # cycle counter so cycle IDs are 1:1 with actual dictations
        # (no gaps from blocked / queued / errored toggles).
        app._cycle_counter += 1
        app._cycle_id = f"#{app._cycle_counter}"

        if app.recorder.recording:
            # #2 Call app._stop_dictation (which delegates to
            # self.stop()) so tests that monkeypatch app._stop_dictation
            # still intercept the call.
            app._stop_dictation()
        else:
            app._start_dictation()

    def start(self) -> None:
        """Start a recording session.

        acquires ``_toggle_lock`` (an RLock) so the auto-stop
        Timer thread's ``_stop_dictation`` -> ``self.stop()`` call (or
        the ESC cancel hotkey's ``cancel()``) serializes against an
        in-flight ``toggle()`` / ``start()`` / ``stop()`` / ``cancel()``
        on any other thread. RLock allows the re-entrant path
        ``toggle() -> app._start_dictation() -> self.start()`` to
        re-acquire without deadlocking.
        """
        with self._toggle_lock:
            self._start_impl()

    def _start_impl(self) -> None:
        """Inner start implementation, called under _toggle_lock."""
        app = self._app
        if app.recorder.recording:
            log.info("[DICTATION] _start_dictation: already recording, no-op")
            return

        #  (revised): Enforce voice_biometric_consent before
        # capturing any audio. The config field and Electron UI toggle
        # existed previously, but the audio pipeline never checked the
        # flag — meaning the consent was a UI decoration with zero
        # enforcement. Now we refuse to start recording if the user has
        # not explicitly consented to voice biometric processing.
        #
        # This is a GDPR Art. 9 requirement for processing biometric
        # data (voice is biometric). The default is False — the user
        # MUST opt in via the Settings UI before any recording happens.
        # See FORENSIC_REVIEW_COMPLETE.md →
        try:
            if not getattr(app.config, "voice_biometric_consent", False):
                log.warning(
                    "[DICTATION] Refusing to start recording - voice_biometric_consent "
                    "is False. User must enable it in Settings > Privacy."
                )
                try:
                    app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.consent_required"))
                    app.tray.notify_safety(
                        APP_NAME,
                        "Voice biometric consent is required to start recording.\n"
                        "Enable it in Settings > Privacy > Voice Biometric Consent.",
                    )
                except Exception:
                    log.debug("[DICTATION] failed to notify about missing consent", exc_info=True)
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
                app.tray.notify_safety(
                    APP_NAME,
                    "Could not verify voice biometric consent.\nRecording refused — check Settings > Privacy.",
                )
            except Exception:
                log.debug(
                    "[DICTATION] failed to notify about consent check exception",
                    exc_info=True,
                )
            return

        # Cancel any stale pending timers from previous sessions
        app._cancel_pending_timers()

        # if a model change was deferred during the previous
        # recording, apply it now (loads the new backend before we start
        # capturing audio). Without this, the user's "change to medium
        # after current recording" would silently never happen.
        try:
            app.models.apply_pending_model_change()
        except Exception:
            log.exception("[DICTATION] Failed to apply pending model change; continuing")

        # ``ensure_active_engine_loaded()`` is deferred to AFTER
        # ``recorder.start()`` (see below). Pre-fix, it ran BEFORE
        # ``recorder.start()`` and blocked the F2 hotkey thread for 5-30s
        # when the  idle-unload timer had fired — the first 5-30s of
        # speech was lost because audio was not being captured during the
        # reload. Now the recorder buffers audio while the model reloads
        # inline; the transcription thread (started in ``_stop_impl``)
        # transcribes the buffered audio once the model is ready.

        log.info("[DICTATION] Starting recording... (cycle=%s)", app._cycle_id)
        try:
            # H12: Wire silence detection callbacks
            app.recorder.on_silence_warning = self.on_silence_warning
            app.recorder.on_silence_auto_stop = self.on_silence_auto_stop
            app.recorder.on_max_duration_auto_stop = self.on_max_duration_auto_stop
            # wire the microphone-permission-revoked callback so
            # the device_health_checker_loop can surface a distinct
            # "Microphone permission revoked" notification (and IPC
            # event) when the OS revokes mic access mid-recording,
            # instead of falling through to the misleading
            # "silence detected" auto-stop after 30-60 s of zero-filled
            # buffers. ``getattr``-guarded so older Recorder test
            # doubles that don't accept the attribute still work.
            with contextlib.suppress(Exception):
                app.recorder.on_microphone_permission_revoked = self.on_microphone_permission_revoked

            # Waveform bubble: feed RMS levels from the audio callback
            app.recorder.on_rms_level = self.on_recorder_rms

            # Reset audio-quality analyzer accumulators so per-chunk
            # statistics don't carry over from the previous session.
            try:
                app._audio_quality.reset()
            except Exception:
                log.debug("[AUDIO_QUALITY] reset on start failed", exc_info=True)

            # stop the level_monitor's PortAudio InputStream BEFORE
            # opening the Recorder's stream. Without this guard, both
            # streams run concurrently (Linux/macOS doubles audio-path
            # CPU; Windows MME device-conflict fails the second open).
            self._stop_level_monitor_for_recorder_start()

            app.recorder.start()
            # tell the mic watcher which mic_id we're recording
            # from so the OS-event-driven active-mic-lost check can
            # fire on the next device-list change. Best-effort: a
            # missing/None mic_watcher (platform without OS watcher)
            # is silently skipped.
            with contextlib.suppress(Exception):
                mic_watcher = getattr(app.recorder, "_mic_watcher", None)
                if mic_watcher is not None:
                    # The resolved device index (or None for default)
                    # is the active mic_id the watcher will look for.
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
            # mark the recording subsystem as the keyboard
            # owner. The ESC cancel hotkey will fire normally during a
            # recording (it's the only way to cancel). When recording
            # stops, ownership returns to "normal".
            try:
                from voice_typer.server.keyboard_ownership import keyboard_ownership

                keyboard_ownership().set_owner("recording", reason=f"recording started (cycle={app._cycle_id})")
            except Exception:
                log.debug(
                    "[DICTATION] failed to set keyboard ownership on start",
                    exc_info=True,
                )

            # ESC-CANCEL-WATCHDOG: the ESC-to-cancel hotkey is the ONLY way to
            # abort an in-progress recording, so if its backend died (silent
            # startup-registration failure, native binary crash, or a
            # multi-instance hook-chain collapse) the user would be unable to
            # cancel — exactly the reported "Escape does nothing" symptom.
            # Re-arm it on every recording start so a dead/stale ESC backend
            # can never leave the user trapped in a recording.
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
            # emit recording_started push event so the
            # renderer can proactively refresh UI (Home/Dashboard/History)
            # SOUND- log push failures instead of silently
            # swallowing them — a failed push means the renderer never
            # hears about recording_started, so the sound cue won't play
            # and the user gets no audible feedback.  This must be visible.
            try:
                from voice_typer.server import event_bus

                event_bus.publish({"type": "recording_started"})
            except Exception:
                log.warning(
                    "[SOUND] failed to push recording_started event",
                    exc_info=True,
                )

            # load / reload the active engine AFTER ``recorder.start()``
            # so the recorder buffers audio while the model reloads (5-30s
            # on idle-unload). Pre-fix this ran before ``recorder.start()``
            # and the first 5-30s of speech was lost. The transcription
            # thread (started in ``_stop_impl``) transcribes the buffered
            # audio once the model is ready. If the model fails to load,
            # we discard the recorder we just started and surface an error.
            #
            #  Release ``_toggle_lock`` for the duration of
            # ``ensure_active_engine_loaded()`` so the F2 hotkey backend's
            # single dispatch thread is NOT blocked for 5-30s on the
            # idle-unload reload path. Pre-fix, the lock was held across
            # the model load, so:
            #   - ESC cancel hotkey (separate thread) blocked on the
            #     lock — the user could not abort a recording whose
            #     model was still loading.
            #   - Tray-menu "Stop" blocked on the lock — the menu
            #     appeared frozen for 5-30s.
            #   - Auto-stop Timer (silence / max-duration) blocked on
            #     the lock — the auto-stop fired but the stop body
            #     didn't run until the model finished loading,
            #     defeating the auto-stop's responsiveness guarantee.
            # The recorder is already running and buffering audio
            # (started above), so releasing the lock does not pause
            # audio capture. The ``_busy_event`` is NOT yet cleared
            # (``_stop_impl`` clears it; ``_start_impl`` does not touch
            # it), so a concurrent ``stop()`` that acquires the released
            # lock would see ``busy_event.is_set() == True`` (not busy)
            # and proceed — which is the desired behavior (the user
            # explicitly stopped, so the buffered audio should be
            # transcribed as soon as the model finishes loading). The
            # transcription thread spawned by that concurrent ``stop()``
            # calls ``active_transcriber()`` after we finish loading
            # below; if the model is still loading when the thread
            # reaches that call, the existing model-manager load
            # serialization handles the wait.
            #
            # Re-acquire the lock AFTER the load completes so the
            # post-load steps (``active_transcriber`` check,
            # ``_start_streaming_session_if_enabled``) run under the
            # lock — preserving the invariant that streaming-session
            # setup is serialized against concurrent stop / cancel.
            self._toggle_lock.release()
            try:
                app.models.ensure_active_engine_loaded()
            finally:
                self._toggle_lock.acquire()
            active = app.models.active_transcriber()
            if active is None or not getattr(active, "is_loaded", False):
                # No engine loaded -- try to load whisper as a fallback
                log.warning("[DICTATION] No loaded engine found, lazy-loading Whisper as fallback")
                #  Phase 2: was ``app._fallback_to_whisper(notify_on_failure=True)``
                # (a test-seam delegate on VoiceTyperApp, now removed). Call the
                # ModelManager method directly.
                app.models.fallback_to_whisper(notify_on_failure=True)
                active = app.models.active_transcriber()
                if active is None or not getattr(active, "is_loaded", False):
                    log.error("[DICTATION] Whisper fallback also failed, cannot record")
                    # the recorder is already running — discard it
                    # so we don't leak the mic stream or leave the app in
                    # a recording state with no engine to transcribe.
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
                            AppState.ERROR, i18n.t("state.recording_controller.model_failed_retry")
                        ),
                    )
                    return

            # streaming session requires an active transcriber, so it
            # must start AFTER the model-load block above (pre- it ran
            # immediately after ``recorder.start()`` which was fine because
            # the model was already loaded; now the model loads later).
            self._start_streaming_session_if_enabled()
        except Exception as e:
            log.exception("[DICTATION] Failed to start recording: %s", e)
            self._cancel_streaming_session()
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
            # invariant holds even if ``discard()`` raised (the best-effort
            # guard above) or if a subclass overrode ``discard()`` to skip
            # the flag reset.
            app.recorder.recording = False
            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.recording_failed"))
            # Use the i18n key (no {error}
            # interpolation — exception text can leak absolute paths,
            # device names, hostnames). The full exception is logged
            # above via log.exception().
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

    def stop(self) -> None:
        """Stop recording and transcribe in background.

        acquires ``_toggle_lock`` (an RLock) so the auto-stop
        Timer thread's ``_stop_dictation`` -> ``self.stop()`` call
        serializes against an in-flight ``toggle()`` / ``start()`` /
        ``stop()`` / ``cancel()`` on any other thread. RLock allows the
        re-entrant path ``toggle() -> app._stop_dictation() ->
        self.stop()`` to re-acquire without deadlocking.
        """
        with self._toggle_lock:
            self._stop_impl()

    def _stop_impl(self) -> None:
        """Inner stop implementation, called under _toggle_lock.

        the blocking ``recorder.stop()`` call (~2.4s worst case:
        300ms stream-teardown poll + 2.0s audio-worker drain + 2.0s
        event-worker drain + np.concatenate + resample) is moved off the
        hotkey thread into a daemon worker. The hotkey thread does the
        synchronous pre-stop work (publish event, keyboard ownership,
        busy flag, tray, bubble), then spawns the worker as
        ``self._transcription_thread`` and returns after a bounded
        ``join(timeout=0.1)``. The ``_toggle_lock`` is held only for the
        synchronous pre-stop work + thread spawn (microseconds), not the
        2.4s teardown. The ``_busy_event`` (cleared synchronously below)
        prevents concurrent ``stop()`` / ``toggle()`` calls from
        proceeding while the stop+transcribe worker is running.
        """
        app = self._app
        if not app.recorder.recording:
            log.info("[DICTATION] _stop_dictation: not recording, no-op")
            return
        # if a stop+transcribe worker is already running
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
        # emit recording_stopped push event
        # SOUND- log push failures (see comment in start() above).
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "recording_stopped"})
        except Exception:
            log.warning(
                "[SOUND] failed to push recording_stopped event",
                exc_info=True,
            )

        # recording is stopping — release keyboard ownership
        # back to "normal" so the ESC cancel hotkey stops firing. This
        # MUST happen before recorder.stop() so that any key events
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
        # Push a final zero-level event so the renderer resets its animation
        # envelope. Without this, the dots stay frozen at their last active
        # height because rawLevelRef is never set back to 0.
        app._waveform_bubble.reset_level()
        # NEW-BUBBLE-TRANSCRIBING: Instead of hiding the bubble immediately,
        # switch it to "transcribing" state so the user sees visual feedback
        # that processing is in progress. The bubble shows "Transcribing…"
        # text with an animated dots indicator. Once the transcription
        # pipeline finishes (DictationPipeline._copy_and_paste), the bubble
        # is hidden or set back to idle depending on bubble_behavior.
        app._waveform_bubble.set_state("transcribing")

        _captured_cycle_id = app._cycle_id

        # spawn a daemon worker thread that performs
        # ``recorder.stop()`` + audio-stats + transcription. The hotkey
        # thread returns after a bounded ``join(timeout=0.1)`` so it
        # stays responsive (the F2 hotkey backend's single dispatch
        # thread is no longer blocked for ~2.4s on every stop). The
        # worker is the SAME thread that runs the transcription pipeline
        # (``name="Transcription"``) — ``recorder.stop()`` is its first
        # step, so the watchdog (which monitors ``_transcription_thread``)
        # also covers the stop phase.
        #
        # The worker body used to be a ~170-line nested closure
        # (``stop_and_transcribe_worker``) defined inline below. It is now
        # extracted to ``_stop_and_transcribe_worker_entry`` (recorder
        # stop + error recovery) which delegates to
        # ``_run_stop_and_transcribe(audio, cycle_id)`` (the transcription
        # body). Splitting the closure into two bound methods makes the
        # transcription body unit-testable (call
        # ``_run_stop_and_transcribe(fake_audio, cycle_id)`` directly
        # without spinning up a real recorder) AND eliminates the nested
        # closure so ``_stop_impl`` has no inline function definitions.
        # Behavior is preserved exactly: ``recorder.stop()`` still runs on
        # the daemon worker thread (not the hotkey thread), error recovery
        # is unchanged, the streaming-session stashing logic is
        # preserved verbatim, and the transcription pipeline receives the
        # same ``audio`` / ``cycle_id`` / ``recorded_rms`` / ``duration``
        # arguments in the same order.

        # write ``self._transcription_thread`` under
        # ``_watchdog_lock`` so the watchdog daemon thread's read in
        # ``_force_recover_from_stuck_transcription`` (which also
        # acquires the same lock) cannot observe a stale ``None`` mid-
        # assignment or a torn reference. The lock is short-held (just
        # the assignment + start() returning quickly) and start() never
        # blocks, so there is no risk of holding it during model work.
        with self._watchdog_lock:
            self._transcription_thread = threading.Thread(
                target=self._stop_and_transcribe_worker_entry,
                args=(_captured_cycle_id,),
                name="Transcription",
                daemon=True,
            )
            self._transcription_thread.start()

        # bounded wait for the worker to make progress. In tests
        # where ``recorder.stop()`` is fast (mocked), the worker may
        # finish entirely within this window -- preserving the contract
        # that ``recorder.stop()`` is observable as called immediately
        # after ``stop()`` returns. In production where ``recorder.stop()``
        # takes ~2.4s, the join times out at 0.1s and the hotkey thread
        # returns while the worker continues -- a 24x responsiveness
        # improvement over the pre- synchronous block. The worker is
        # a daemon, so it doesn't block process exit if the user quits
        # during the stop+transcribe cycle.
        with contextlib.suppress(Exception):
            self._transcription_thread.join(timeout=0.1)

    def _stop_and_transcribe_worker_entry(self, cycle_id: str) -> None:
        """Daemon worker-thread entry point — extracted from the former
        nested closure ``stop_and_transcribe_worker``.

        Performs the FIRST step of the stop+transcribe pipeline:
        ``recorder.stop()`` (which blocks ~2.4s in production: 300ms
        stream-teardown poll + 2.0s audio-worker drain + 2.0s event-worker
        drain + np.concatenate + resample) plus the  mic-watcher
        cleanup, with full error recovery if ``recorder.stop()`` raises.

        On success, delegates the transcription body to
        ``_run_stop_and_transcribe(audio, cycle_id)`` so the transcription
        pipeline is unit-testable in isolation (pass a fake ``audio``
        sample directly).

         rationale: this method runs on the daemon "Transcription"
        thread (NOT the hotkey thread), so the ~2.4s ``recorder.stop()``
        block does not stall the F2 hotkey backend's single dispatch
        thread. The hotkey thread spawns this worker and returns after a
        bounded ``join(timeout=0.1)``.
        """
        app = self._app
        try:
            # recorder.stop() is the FIRST step. Pre-fix this
            # ran synchronously on the hotkey thread.
            audio = app.recorder.stop()
            # clear the active-mic-id on the watcher so it
            # stops checking for the now-stopped recording's mic.
            # Best-effort: a missing mic_watcher is silently skipped.
            with contextlib.suppress(Exception):
                mic_watcher = getattr(app.recorder, "_mic_watcher", None)
                if mic_watcher is not None:
                    mic_watcher.set_active_mic_id(None)
        except Exception:
            log.exception("[DICTATION] Failed to stop recording (worker)")
            self._cancel_streaming_session()
            app._restore_volume()
            # best-effort restart of the level_monitor for the
            # always-visible bubble (the recorder's stream is closed
            # even on failure — ``recorder.stop()`` raised but the
            # stream teardown is the recorder's responsibility).
            self._maybe_restart_level_monitor_for_always_visible_bubble(app)
            app.tray.set_state(AppState.ERROR, i18n.t("state.recording_controller.stop_failed"))
            # critical — bypass the notification toggle
            # (dictation failed, the user must be told even if they
            # disabled normal notifications).
            # no {error} interpolation — exception text can leak
            # sensitive paths. The full exception is logged above.
            app.tray.notify_safety(
                APP_NAME,
                i18n.t("notify.recording_controller.stop_failed"),
            )
            app._busy_event.set()  # busy = False
            app._schedule_timer(3.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        self._run_stop_and_transcribe(audio, cycle_id)

    def _run_stop_and_transcribe(self, audio, cycle_id: str) -> None:
        """Transcription pipeline body — extracted from the former nested
        closure ``stop_and_transcribe_worker``.

        Takes the captured ``audio`` bytes (already resampled to
        ``config.sample_rate`` by ``recorder.stop()``) and the
        ``cycle_id`` (captured at stop time so a new dictation cycle
        starting before transcription completes does not corrupt log
        correlation) and runs the full post-stop pipeline:

        1. Log ring-buffer overflow stats ().
        2. Restore system volume + restart the level_monitor for the
           always-visible bubble ().
        3. Compute ``duration`` + ``recorded_rms``.
        4. Finalize the audio-quality report (revived
           AudioQualityAnalyzer).
        5. Short-circuit on ``duration < 0.5s`` (too short).
        6. Set tray to TRANSCRIBING + reset watchdog counter ().
        7. Pop + stash the streaming session so the
           pipeline's ``pop_streaming_session()`` can retrieve it and
           call ``session.finalize(audio)`` — the streaming fast path.
        8. Start the persistent watchdog thread (RACE-013).
        9. Stash audio in ``self._current_audio`` ( privacy) then
           immediately capture-and-clear so the slot doesn't retain the
           bytes for the transcription duration ().
        10. Run ``DictationPipeline.run(...)``.

        Unit-testable: call ``_run_stop_and_transcribe(fake_audio,
        cycle_id)`` directly with a mock ``app.recorder`` /
        ``app.tray`` / ``app.config`` to exercise the transcription body
        without spinning up a real recorder or hotkey thread.
        """
        app = self._app

        # surface ring-buffer overflow detected during the
        # recording. ``_dropped_ring_chunks`` is reset to 0 on the next
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
                "%d chunk(s) dropped (cycle=%s). Audio worker could not "
                "keep up; transcription may be incomplete.",
                dropped,
                app._cycle_id,
            )

        # Restore system volume immediately — don't wait for transcription
        # (which takes seconds) before the user gets their audio back.
        app._restore_volume()

        # now that the Recorder's InputStream is closed, restart
        # the level_monitor if the bubble is always_visible so the
        # ambient level bar continues updating. Best-effort.
        self._maybe_restart_level_monitor_for_always_visible_bubble(app)

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
            "[DICTATION] Recording stopped -- %.1fs of audio, recorded_rms=%.4f, busy=True (cycle=%s)",
            duration,
            recorded_rms,
            cycle_id,
        )

        if duration < 0.5:
            log.info("[DICTATION] Audio too short, skipping transcription")
            self._cancel_streaming_session()
            app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.too_short"))
            app._busy_event.set()  # busy = False
            app._schedule_timer(2.0, lambda: app.tray.set_state(AppState.IDLE))
            return

        log.info(
            "[DICTATION] Starting transcription (stop+transcribe worker)... (cycle=%s)",
            cycle_id,
        )
        app.tray.set_state(AppState.TRANSCRIBING, i18n.t("state.recording_controller.transcribing"))

        # reset watchdog counter for this transcription cycle.
        with self._watchdog_lock:
            self._watchdog_firings = 0

        # PERF- signal the streaming session to
        # cancel BEFORE starting the final transcription. Pre-fix this
        # used ``get_streaming_session()`` + private
        # ``_cancel_event.set()`` which left the session in
        # ``self._streaming_session`` across the entire transcription
        # window and depended on a fragile private-attribute contract
        # (silently swallowed by ``contextlib.suppress(Exception)``).
        #
        # The original implementation called
        # ``_cancel_streaming_session()`` which popped AND discarded
        # the session. That forced ``DictationPipeline._transcribe``
        # (which calls ``pop_streaming_session()``) to always see
        # ``None`` and fall back to batch transcription — even when
        # a streaming session was active and could have provided the
        # finalized transcript via ``session.finalize(audio)``. The
        # streaming fast path was effectively dead code on the stop
        # path (root cause of the
        # ``test_stop_dictation_uses_streaming_final_text`` failure).
        #
        # The fix: pop the session + signal cancel (non-blocking) so
        # the streaming worker stops, BUT stash the session in
        # ``self._pending_finalize_session`` so the pipeline's
        # ``pop_streaming_session()`` (which checks the stash as a
        # fallback) can retrieve it and call ``finalize()``. The
        # stash is written under ``_streaming_session_lock`` (single
        # lock acquisition — same atomicity guarantee as
        # ``pop_streaming_session``). ``session.cancel()`` is called
        # OUTSIDE the lock (it can be slow / blocking on a real
        # worker thread join in the streaming backend).
        #
        # ``finalize()`` itself calls ``cancel(blocking=True)`` which
        # is idempotent (``_cancel_event.set()`` on an already-set
        # event is a no-op), so the early non-blocking cancel here
        # does not double-join or race the worker.
        with self._streaming_session_lock:
            # ``_stop_impl`` does NOT pop the session or signal
            # cancel. The streaming session stays in
            # ``self._streaming_session`` so the pipeline's
            # ``pop_streaming_session()`` (called from
            # ``DictationPipeline.run``) can retrieve it and call
            # ``finalize(audio)`` for the streaming fast path. The
            # pipeline's ``finally`` block also calls
            # ``session.cancel()`` on the popped session so the
            # worker thread is signalled to exit. Pre-cancelling
            # here would prevent the session from emitting its
            # finalized text (a regression of the streaming fast
            # path covered by
            # ``test_stop_dictation_uses_streaming_final_text``).
            self._pending_finalize_session = self._streaming_session

        # RACE-013: Start persistent watchdog thread using Event.wait(timeout=90).
        self._start_watchdog_thread()

        # transcribe_thread extracted to DictationPipeline class.
        from voice_typer.server.dictation_pipeline import DictationPipeline

        #  (privacy): hold audio bytes in a shared, clearable slot
        # so ``_force_recover_from_stuck_transcription`` can drop our
        # Python-side reference at cancel time. The worker reads from
        # ``self._current_audio`` (no closure capture of the local),
        # so setting ``self._current_audio = None`` at force-recovery
        # releases the bytes for GC.
        self._current_audio = audio
        # capture into a local and clear the shared slot BEFORE
        # calling pipeline.run(). Pre-fix, the slot retained the audio
        # for the entire transcription duration (1-15 MB of float32).
        audio_bytes = self._current_audio
        self._current_audio = None

        pipeline = DictationPipeline(app)
        pipeline.run(
            audio=audio_bytes,
            duration=duration,
            recorded_rms=recorded_rms,
            cycle_id=cycle_id,
            watchdog=None,  # RACE-013: no longer using Timer-based watchdog
        )
        #  Now that the transcription pipeline has fully
        # returned (the late-transcription check inside
        # ``CancellationGuard`` has already run, the paste-or-skip
        # decision has been made), discard this cycle's entry from
        # ``_cancelled_cycle_ids``. Without this discard, every
        # cancelled cycle would linger in the bounded registry until
        # LRU-evicted at ``_MAX_CANCELLED_IDS`` — the registry would
        # always be near-full of stale entries from cycles whose
        # late transcription was already observed + dropped. Discarding
        # here keeps the registry focused on cycles whose transcription
        # is STILL pending (i.e. the only entries that matter for the
        # ``CancellationGuard`` check). Best-effort: a missing cycle_id
        # is the common case (most cycles are never cancelled) and is
        # silently ignored by ``_discard_cancelled_cycle_id``.
        self._discard_cancelled_cycle_id(cycle_id)

    def cancel(self) -> None:
        """Feature: ESC to cancel -- cancel current recording/transcription.

        previously, if ``recorder.discard()`` raised (PortAudio
        error, stream close race), the cancel path aborted before
        resetting tray state — leaving the tray stuck on RECORDING.
        We now guarantee the post-discard cleanup always runs.

        set AppState.CANCELLING for ~200ms during cancel so
        the tray icon shows a distinct "cancelling" state instead of
        instantly transitioning RECORDING → IDLE (which flickers).

        acquires ``_toggle_lock`` (an RLock) so the ESC cancel
        hotkey's call serializes against an in-flight ``toggle()`` /
        ``start()`` / ``stop()`` on any other thread. The hotkey
        backend fires ESC on a separate thread from F2, so without
        this lock, a near-simultaneous F2-toggle + ESC-cancel could
        race on ``app.recorder.recording`` and ``recorder.discard()``.
        RLock allows re-entrancy from any code path that already holds
        the lock (none currently, but kept symmetric with start/stop).
        """
        with self._toggle_lock:
            self._cancel_impl()

    def _cancel_impl(self) -> None:
        """Inner cancel implementation, called under _toggle_lock."""
        app = self._app

        # ESC- If no recording is active, the ESC cancel is a no-op.
        # The global ESC hotkey backend fires on every Escape press regardless
        # of whether a recording is in progress.  Early-return here avoids
        # spurious CANCEL logs (which look like errors to the user) and
        # prevents unnecessary cleanup (streaming session cancel, volume
        # restore, bubble hide) when nothing is running.
        #
        # in addition to the recorder.recording check, we
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
            # ESC during the transcription phase. Pre-fix, this was
            # a silent no-op — the user pressed ESC to abort a stuck
            # transcription and nothing happened; they had to wait up to
            # 270s (3 watchdog firings × 90s) for
            # ``_force_recover_from_stuck_transcription`` to reset the
            # busy flag + tray. Post-fix: if the transcription thread is
            # alive AND the busy flag is set (busy=True, i.e.
            # ``_busy_event.is_set() == False``), we immediately mark the
            # cycle as cancelled (so the late transcription result is NOT
            # pasted when the ctranslate2 call eventually completes) and
            # force-recover the busy flag + tray so the user can start a
            # new recording right away.
            #
            # ctranslate2 / faster-whisper cannot be interrupted
            # mid-call (documented limitation) — we do NOT try to kill
            # the transcription thread. The thread continues running and
            # the late result is dropped by the pipeline's
            # ``_cancelled_cycle_ids`` check.
            with self._watchdog_lock:
                t_thread = self._transcription_thread
            if (
                t_thread is not None and t_thread.is_alive() and not app._busy_event.is_set()  # busy = True
            ):
                log.info(
                    "[CANCEL] ESC during transcription phase (cycle=%s) — marking cancelled + force-recovering",
                    app._cycle_id,
                )
                cycle_id = getattr(app, "_cycle_id", None)
                if cycle_id is not None:
                    #  Use the bounded-registry helper so the
                    # set cannot grow unbounded across many cancel
                    # events (LRU eviction at ``_MAX_CANCELLED_IDS``).
                    self._mark_cycle_cancelled(cycle_id)
                    log.info(
                        "[CANCEL] cycle %s marked cancelled — late transcription will not be pasted",
                        cycle_id,
                    )
                # ``_force_recover_from_stuck_transcription``
                # now also cancels the streaming session ( fix).
                # ``force=True`` so the recovery is immediate regardless
                # of the watchdog firing count.
                try:
                    self._force_recover_from_stuck_transcription(force=True)
                except Exception:
                    log.exception(
                        "[CANCEL] _force_recover_from_stuck_transcription raised during cancel (cycle=%s)",
                        app._cycle_id,
                    )
                return
            log.debug("[CANCEL] Cancel pressed but no recording active (cycle=%s) — no-op", app._cycle_id)
            return

        log.info("[CANCEL] Cancelling current dictation (cycle=%s)", app._cycle_id)

        # release keyboard ownership back to "normal" so
        # subsequent Escape presses during the cancel cleanup don't
        # re-enter the cancel path.
        try:
            from voice_typer.server.keyboard_ownership import keyboard_ownership

            keyboard_ownership().set_owner("normal", reason=f"recording cancelled (cycle={app._cycle_id})")
        except Exception:
            log.debug(
                "[CANCEL] failed to reset keyboard ownership on cancel",
                exc_info=True,
            )
        # show CANCELLING state immediately.
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
                # clear the active-mic-id on the watcher so it
                # stops checking for the now-cancelled recording's mic.
                with contextlib.suppress(Exception):
                    mic_watcher = getattr(app.recorder, "_mic_watcher", None)
                    if mic_watcher is not None:
                        mic_watcher.set_active_mic_id(None)
                # Immediately secure-clear the audio buffers from memory
                # after discard. Without this, the numpy array holding the
                # user's voice can persist in RAM for 30+ minutes until GC
                # reclaims it. _secure_clear_session_caches zero-fills and
                # deletes the cached audio arrays, ensuring voice data is
                # wiped immediately on cancel.
                app.recorder._secure_clear_session_caches()
            except Exception as e:
                # don't abort the cancel path — fall through to
                # ensure tray state + busy flag are reset.
                log.exception(
                    "[CANCEL] Failed to discard recording (cycle=%s): %s",
                    app._cycle_id,
                    e,
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

        # Hide bubble unless always_visible mode (in which case set
        # to idle so the visualizer bars don't stay frozen on screen)
        try:
            if app.config.bubble_behavior != "always_visible":
                app._waveform_bubble.hide()
            else:
                app._waveform_bubble.set_state("idle")
        except Exception:
            log.exception("[CANCEL] Failed to hide/set idle bubble")

        # tray state + busy flag MUST be cleared so the user
        # can press F2 again after a cancel.
        app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.cancelled"))
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
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                "No audio detected. Check your microphone is connected and working.",
            )

    def on_silence_auto_stop(self) -> None:
        """Handle silence auto-stop from recorder."""
        log.warning("[DICTATION] Silence auto-stop: stopping recording due to prolonged silence")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                "Recording stopped: no audio detected for an extended period.",
            )
        # Must NOT call stop() directly here -- this callback runs
        # inside the audio callback while Recorder._lock is held.  Calling
        # recorder.stop() would deadlock on the same lock.  Schedule it on a
        # separate thread instead.
        # #2 call app._stop_dictation (delegate) instead of
        # self.stop() directly so tests that monkeypatch _stop_dictation
        # still intercept the call.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_max_duration_auto_stop(self) -> None:
        """Handle max duration auto-stop from recorder."""
        log.warning("[DICTATION] Max duration auto-stop: stopping recording")
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                "Recording stopped: maximum recording duration reached.",
            )
        # Same reason as on_silence_auto_stop: avoid deadlock on Recorder._lock.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_microphone_permission_revoked(self) -> None:
        """— handle mid-recording OS-level microphone-permission
        revocation.

        Distinct from ``on_silence_auto_stop`` so the user sees a
        "Microphone permission revoked" notification (and the renderer
        receives a dedicated ``microphone_permission_revoked`` IPC event)
        instead of the misleading "silence detected" message that
        ``on_silence_auto_stop`` produces.

        Spawned by ``DeviceManager._check_microphone_permission_revoked``
        on a fresh daemon thread (via ``recorder._spawn_device_thread``),
        so we DON'T hold ``Recorder._lock`` here — but we still schedule
        the actual stop off this thread for parity with the silence /
        max-duration auto-stop callbacks (their comment explains the
        deadlock-avoidance rationale; we mirror it for consistency).
        """
        log.warning(
            "[DICTATION] Microphone permission revoked mid-recording -- stopping stream and surfacing IPC event"
        )
        with contextlib.suppress(Exception):
            self._app.tray.notify_safety(
                APP_NAME,
                "Microphone permission was revoked. Recording stopped. "
                "Re-grant microphone access in your OS privacy settings to resume.",
            )
        # Emit the dedicated ``microphone_permission_revoked`` IPC event
        # so the renderer can show a banner (distinct from the generic
        # ``recording_stopped`` / silence-auto-stop toast). Best-effort:
        # the event_bus module is a leaf dependency, but if the import
        # or publish raises (e.g. during shutdown teardown) we still
        # need the stop to fire.
        try:
            from voice_typer.server import event_bus

            event_bus.publish({"type": "microphone_permission_revoked"})
        except Exception:
            log.debug(
                "[DICTATION] failed to publish microphone_permission_revoked event",
                exc_info=True,
            )
        # Stop the recording off this thread (mirror the
        # on_silence_auto_stop pattern). ``_stop_dictation`` re-acquires
        # ``Recorder._lock``; the original caller (the device-health
        # checker's spawned handler) does NOT hold that lock, so the
        # schedule is for consistency with the silence/max-duration
        # callbacks rather than strict deadlock avoidance.
        self._app._schedule_timer(0, self._app._stop_dictation)

    def on_xrun_threshold(self, count: int) -> None:
        """Item 1: notify the user when xrun count exceeds threshold."""
        log.warning("[XRUN] Threshold reached: %d xruns", count)
        if self._app.config.show_notifications:
            with contextlib.suppress(Exception):
                self._app.tray.notify(
                    f"{APP_NAME} — Audio Issues",
                    f"Detected {count} audio buffer underruns. Try closing other audio apps or reducing CPU load.",
                )

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
        active = app.models.active_transcriber()
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
                transcriber=app.models.active_transcriber(),
                config=self._streaming_config(),
                sample_rate=app.config.sample_rate,
                # THREAD-REGISTRY: pass the app's registry so the
                # streaming worker is tracked for coordinated shutdown.
                # ``getattr`` with default ``None`` keeps this robust
                # if a test constructs RecordingController with a mock
                # app that doesn't have ``_thread_registry``.
                thread_registry=getattr(app, "_thread_registry", None),
            )
            session.start()
            self.set_streaming_session(session)
            log.info("[STREAMING] Hidden streaming session started (cycle=%s)", app._cycle_id)
        except Exception as e:
            log.exception("[STREAMING] Failed to start streaming session: %s", e)
            self.set_streaming_session(None)

    def _cancel_streaming_session(self) -> None:
        """Cancel any active hidden streaming session.

        uses ``pop_streaming_session()`` (atomic get-and-clear)
        instead of the pre-fix get-then-set sequence that had a TOCTOU
        window between the two lock acquisitions.
        """
        session = self.pop_streaming_session()
        if session is not None:
            try:
                session.cancel()
            except Exception:
                log.exception("[STREAMING] Failed to cancel streaming session")

    # ── : level_monitor / Recorder InputStream coordination ──────────

    def _stop_level_monitor_for_recorder_start(self) -> None:
        """stop the level_monitor's PortAudio InputStream BEFORE
        opening the Recorder's stream.

        ``level_monitor.start_monitoring()`` opens its own
        ``sd.InputStream`` on the mic device. ``RecordingController.start()``
        opens a SEPARATE ``sd.InputStream`` via ``app.recorder.start()``.
        Without coordination, both streams run concurrently -- on Linux/macOS
        this doubles audio-path CPU (2x ``indata.copy()``, 2x RMS, 2x
        RNNoise); on Windows MME the second ``sd.InputStream`` open FAILS
        (device-conflict error), so recording fails to start.

        The frontend tries to stop the level_monitor before
        ``recording_start``, but if the IPC races or the frontend forgets,
        the backend guard here is the safety net. We record whether we
        stopped it (``_level_monitor_was_active``) so ``_stop_impl`` can
        restart it if ``bubble_behavior == "always_visible"``.
        """
        self._level_monitor_was_active = False
        try:
            from voice_typer.server import level_monitor

            if level_monitor.is_monitoring():
                level_monitor.stop_monitoring()
                self._level_monitor_was_active = True
                log.debug(
                    "[DICTATION] level_monitor was active -- stopped before recorder.start() (AB-4, cycle=%s)",
                    getattr(self._app, "_cycle_id", "?"),
                )
        except Exception:
            log.debug(
                "[DICTATION] failed to stop level_monitor before recorder.start() (AB-4)",
                exc_info=True,
            )

    def _maybe_restart_level_monitor_for_always_visible_bubble(self, app: Any) -> None:
        """restart the level monitor after recording stops if the
        bubble is in ``always_visible`` mode.

        When ``bubble_behavior == "always_visible"``, the level bar is
        shown at all times (even when not recording), so the level_monitor
        must be running whenever the Recorder's stream is closed. We
        stopped it in ``_stop_level_monitor_for_recorder_start`` before
        opening the Recorder's stream; now that ``recorder.stop()`` has
        closed the stream, we restart the level_monitor so the always-
        visible bubble continues showing ambient levels.

        Best-effort: if ``start_monitoring`` raises (e.g. device unplugged
        mid-recording), we log and continue -- the bubble will just
        show a flat line until the next ``level_monitor_start`` IPC.
        """
        try:
            if getattr(app.config, "bubble_behavior", "") != "always_visible":
                return
            from voice_typer.server import level_monitor

            if level_monitor.is_monitoring():
                # Already running (e.g. the frontend restarted it via
                # ``level_monitor_start`` IPC after the recording stopped).
                return
            # Use the configured microphone if set; otherwise default.
            mic_id = getattr(app.config, "microphone", None)
            if not isinstance(mic_id, str):
                mic_id = None
            level_monitor.start_monitoring(mic_id=mic_id)
            log.debug(
                "[DICTATION] level_monitor restarted (bubble_behavior=always_visible, AB-4, cycle=%s)",
                getattr(app, "_cycle_id", "?"),
            )
        except Exception:
            log.debug(
                "[DICTATION] failed to restart level_monitor after recorder.stop() (AB-4)",
                exc_info=True,
            )

    def _force_recover_from_stuck_transcription(self, force: bool = False) -> None:
        """Safety net: recover from stuck transcription state.

        When the transcription thread is still alive at the
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
        # snapshot ``_transcription_thread`` and ``_watchdog_firings``
        # under ``_watchdog_lock`` for the duration of the read-check-notify
        # block. Previously the read of ``self._transcription_thread`` and
        # the subsequent ``is_alive()`` call happened without the lock — a
        # concurrent ``stop()`` on the Timer/hotkey thread could be
        # mid-assignment of ``self._transcription_thread`` (now also done
        # under this lock, see ``_stop_impl``), letting the watchdog see a
        # stale ``None`` (treats dead thread as recovered) or the previous
        # cycle's thread (incorrectly leaves app busy).
        # ``_watchdog_firings`` is mutated under this lock in
        # ``_watchdog_loop`` and ``_start_watchdog_thread`` — read it here
        # under the same lock to pair the snapshot with the check.
        with self._watchdog_lock:
            transcription_thread = self._transcription_thread
            firings = self._watchdog_firings
        if not force and transcription_thread is not None and transcription_thread.is_alive():
            log.warning(
                "Transcription watchdog fired (%d/%d), but worker is still "
                "alive; leaving app busy to avoid overlapping model calls",
                firings,
                self._watchdog_max_firings,
            )
            app.tray.set_state(AppState.TRANSCRIBING, i18n.t("state.recording_controller.still_transcribing"))
            # TRANSCRIBE-NOTIFY-FIX: first firing is silent — only notify
            # on the second firing (second notification = 180s+ elapsed)
            # to avoid alarming the user when transcription is simply
            # taking a bit longer than usual.
            if firings >= 2:
                app.tray.notify(
                    APP_NAME,
                    "Transcription is still running.\nLong recordings or CPU fallback can take extra time.",
                )
            # RACE-013: no need to create a new Timer. The persistent
            # watchdog thread will time out again on its next
            # Event.wait(timeout=90) cycle.
            return

        if force:
            log.warning(
                "[RECOVERY] FORCE RECOVER: watchdog fired %d times with "
                "worker still alive; assuming deadlock and resetting state",
                firings,
            )
        else:
            log.warning("[RECOVERY] FORCE RECOVER: transcription watchdog fired, resetting state")
        # record the current cycle_id as cancelled so the late
        # transcription (when the stuck ctranslate2 call eventually
        # completes 5-30 min later) will NOT be pasted into whatever
        # window currently has focus. ``DictationPipeline.run()`` checks
        # this set BEFORE ``_copy_and_paste`` and skips the paste if the
        # cycle is present.
        cycle_id = getattr(app, "_cycle_id", None)
        if cycle_id is not None:
            #  Use the bounded-registry helper so the
            # set cannot grow unbounded across many stuck-recovery
            # events (LRU eviction at ``_MAX_CANCELLED_IDS``).
            self._mark_cycle_cancelled(cycle_id)
            log.warning(
                "[STUCK-RECOVERY] cycle %s marked cancelled — late transcription will not be pasted",
                cycle_id,
            )
        # RACE-013: stop the persistent watchdog thread on recovery
        self._stop_watchdog_thread()
        # cancel the streaming session BEFORE resetting _busy_event.
        # Pre-fix, the streaming session was signalled to cancel in
        # ``_stop_impl`` (``session._cancel_event.set()``), but the
        # ``StreamingTranscriptionSession`` worker thread only checks
        # ``_cancel_event`` between ``transcribe_words`` calls. If the
        # worker was mid-call when the watchdog force-recovered, the
        # worker thread + its stack + any in-flight audio window buffer
        # remained alive. The session reference (holding recorder +
        # transcriber + config + _thread refs) was held until the NEXT
        # ``_start_streaming_session_if_enabled`` call cleared it via
        # ``set_streaming_session(None)``.
        #
        # Calling ``_cancel_streaming_session()`` here atomically pops +
        # cancels the session ( ``pop_streaming_session`` is
        # TOCTOU-safe), releasing our Python-side reference immediately.
        # If the worker thread is still alive after ``cancel()``, it
        # will exit on its next ``_cancel_event`` check — we do NOT
        # block on it (``cancel()`` is best-effort; ctranslate2 cannot
        # be interrupted mid-call).
        try:
            self._cancel_streaming_session()
        except Exception:
            log.debug(
                "[RECOVERY] failed to cancel streaming session during force-recover",
                exc_info=True,
            )
        app._busy_event.set()  # busy = False
        app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.recovered"))
        app.tray.notify(
            APP_NAME,
            "Transcription took too long and was cancelled.\nPress F2 to try again.",
        )
        app._schedule_timer(5.0, lambda: app.tray.set_state(AppState.IDLE))
        #  (privacy): clear the shared audio slot so the raw voice
        # bytes can be garbage-collected once the only remaining
        # reference is the stuck ctranslate2 call (which we cannot reach).
        # ``gc.collect()`` is a best-effort nudge: CPython's GC is
        # generational, so a single collection pass may not free every
        # orphaned cycle immediately, but it surfaces the audio bytes to
        # the next cycle's sweep rather than waiting for the next natural
        # collection (which may be 30+ seconds away on a quiet process).
        # The C-level retention by a stuck ctranslate2 call is documented
        # as an engine-level limitation outside this module's control.
        self._current_audio = None
        with contextlib.suppress(Exception):
            gc.collect()

    # ── Persistent watchdog thread (RACE-013) ───────────────────────────

    def _start_watchdog_thread(self) -> None:
        """Start or reset the persistent watchdog thread.

        RACE-013: replaces the old chained threading.Timer pattern. A
        single daemon thread loops on ``_watchdog_event.wait(timeout=60)``.
        When transcription completes normally, ``_reset_watchdog()`` sets
        the event, causing wait() to return early and the loop to reset
        firings + clear the event for the next cycle. When wait() times
        out (transcription hung), the watchdog fires the recovery action.

        if the previous watchdog thread is in the process of
        dying (``is_alive()`` True but about to exit), we briefly
        ``join(timeout=0.1)`` it and re-check. Without this, a thread
        that's between ``is_alive()`` returning True and actual exit
        would be orphaned (we'd start a new thread but the old one
        would still be running for a few microseconds, possibly
        firing its recovery action out of order). The join is bounded
        so a hung thread doesn't block the start path.

        hold ``_watchdog_lock`` across the ENTIRE
        read-check-create-start sequence. Pre-fix, only the
        ``_watchdog_firings = 0`` reset was under the lock — the
        subsequent read of ``self._watchdog_thread``, the
        ``is_alive()`` check, the bounded ``join()``, the assignment
        of a fresh ``Thread`` object, and ``start()`` all happened
        lock-free. Two concurrent callers (e.g. ``_stop_impl`` on the
        hotkey thread racing with a re-arm from
        ``_force_recover_from_stuck_transcription`` on the watchdog
        thread) could both observe ``_watchdog_thread is None`` (or
        both see it as dead) and both spawn a fresh
        ``TranscriptionWatchdog`` thread, orphaning the loser. The
        lock is held only for the brief read-check-create-start
        sequence — the bounded ``join(timeout=0.1)`` is the longest
        step, and the watchdog loop's own lock holds (in
        ``_watchdog_loop`` and ``_force_recover_from_stuck_transcription``)
        are even briefer, so there is no deadlock risk.
        """
        with self._watchdog_lock:
            self._watchdog_firings = 0
            # Clear any previous reset signal
            self._watchdog_event.clear()
            # If the thread is already running, just reset the counter
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                # thread reports alive — try a bounded join to let it
                # exit cleanly, then re-check. If still alive after the join,
                # we keep the existing thread (don't orphan a hung thread by
                # overwriting ``_watchdog_thread``).
                try:
                    self._watchdog_thread.join(timeout=0.1)
                except Exception:
                    log.debug(
                        "[DICTATION] watchdog thread join raised — best-effort",
                        exc_info=True,
                    )
                if self._watchdog_thread.is_alive():
                    # Still alive after join — reuse it (don't start a second one)
                    return
                # else: thread exited during the join window; fall through
                # and start a fresh thread.
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
        """Stop the persistent watchdog thread.

        Signals the thread to stop via the stop event, then joins it
        (best-effort, bounded at 1.0s so a hung thread doesn't block
        the caller indefinitely) and nulls the reference — mirroring
        ``_stop_mic_level_worker``.

        The ``current_thread()`` guard prevents a self-join deadlock:
        the watchdog thread calls this method via
        ``_force_recover_from_stuck_transcription`` from inside its
        own loop, where joining ourselves would block forever. The
        guard skips the join in that case but still nulls the
        reference so the dead ``Thread`` object doesn't stay
        referenced until the next ``_start_watchdog_thread`` (which
        can be hours apart in a long-running tray app).
        """
        self._watchdog_stop_event.set()
        self._watchdog_event.set()  # break out of wait()
        t = self._watchdog_thread
        if t is not None and t is not threading.current_thread():
            with contextlib.suppress(Exception):
                t.join(timeout=1.0)
            # Only null the reference if the thread actually exited. If
            # still alive (stuck in a long operation), keep the reference
            # so _start_watchdog_thread's is_alive() guard reuses it
            # instead of spawning a duplicate (zombie thread leak
            # mitigation — mirrors the pattern at device_manager.py's
            # _stop_device_health_checker). The stop event is left SET
            # so the zombie exits on its next iteration boundary.
            if not t.is_alive():
                self._watchdog_thread = None
        else:
            # Self-join case (watchdog thread calling _stop_watchdog_thread
            # from inside its own loop) — can't join ourselves, and the
            # thread will exit naturally after returning, so null the
            # reference. Also covers the ``t is None`` case (no-op).
            self._watchdog_thread = None
