"""Transcription watchdog, extracted from ``RecordingController``"""

from __future__ import annotations

import contextlib
import gc
import logging
import threading
import weakref

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.tray_hotkey import notification_hotkey_label
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)

# Registry of controllers with a live watchdog thread. Lets the test
_LIVE_WATCHDOG_CONTROLLERS: weakref.WeakSet = weakref.WeakSet()


# Bounded cap for ``_cancelled_cycle_ids``. Each cancel event (ESC-during-
_MAX_CANCELLED_IDS = 1000


class TranscriptionWatchdog:
    """Persistent transcription-watchdog thread + force-recover + cancelled-"""

    def __init__(self) -> None:
        # Stateless helper, all state lives on the controller.
        pass

    def mark_cycle_cancelled(self, controller, cycle_id: str) -> None:
        """Record a cycle_id as force-cancelled (watchdog / ESC-during-"""
        with controller._cancelled_cycle_ids_lock:
            if cycle_id in controller._cancelled_cycle_ids:
                # Already cancelled, no-op (the watchdog / ESC may fire
                return
            if isinstance(controller._cancelled_cycle_ids, set):
                # Test-double path: plain set has no ordering, so no LRU
                controller._cancelled_cycle_ids.add(cycle_id)
                return
            controller._cancelled_cycle_ids[cycle_id] = None
            if len(controller._cancelled_cycle_ids) > _MAX_CANCELLED_IDS:
                # Evict the OLDEST entry (FIFO). ``popitem(last=False)``
                controller._cancelled_cycle_ids.popitem(last=False)

    def discard_cancelled_cycle_id(self, controller, cycle_id: str) -> None:
        """Best-effort removal of a cycle_id from the cancelled registry."""
        with controller._cancelled_cycle_ids_lock:
            if isinstance(controller._cancelled_cycle_ids, set):
                controller._cancelled_cycle_ids.discard(cycle_id)
                return
            with contextlib.suppress(KeyError):
                controller._cancelled_cycle_ids.pop(cycle_id)

    def force_recover(self, controller, force: bool = False) -> None:
        """Safety net: recover from stuck transcription state.

        When the transcription thread is still alive at the time the
        watchdog fires, we used to leave the app busy and return. That
        meant a genuinely deadlocked worker (e.g. ctranslate2 stuck in
        CUDA) would never recover. We now re-arm the watchdog up to
        ``_watchdog_max_firings`` times; once the counter exceeds the
        threshold (or ``force=True`` is passed), we unconditionally clear
        the busy flag and reset the tray state.

        RACE-013: re-arming no longer creates a new Timer. The persistent
        watchdog thread loops on Event.wait(timeout=60). When it fires
        without a reset, it calls this method. If we decide not to
        force-recover yet, we simply let the loop continue (the event is
        still unset, so the next wait(timeout=60) will time out again
        after 60s).

        TRANSCRIBE-NOTIFY-FIX: the notification "Transcription is still
        running" was showing even for successful transcriptions that
        simply took longer than 60 seconds (e.g. CPU fallback or longer
        audio clips). The first watchdog firing (60s) now silently logs
        instead of notifying the user, the notification only fires on
        the SECOND firing (120s+) when the transcription is genuinely
        taking an unusually long time. The watchdog time for the first
        firing was also raised from 60s to 90s.
        """
        app = controller._app
        if app._busy_event.is_set():  # not busy
            return  # Already recovered, nothing to do
        # Snapshot ``_transcription_thread`` and ``_watchdog_firings``
        with controller._watchdog_lock:
            transcription_thread = controller._transcription_thread
            firings = controller._watchdog_firings
        if not force and transcription_thread is not None and transcription_thread.is_alive():
            log.warning(
                "Transcription watchdog fired (%d/%d), but worker is still "
                "alive; leaving app busy to avoid overlapping model calls",
                firings,
                controller._watchdog_max_firings,
            )
            app.tray.set_state(AppState.TRANSCRIBING, i18n.t("state.recording_controller.still_transcribing"))
            # TRANSCRIBE-NOTIFY-FIX: first firing is silent, only notify
            if firings >= 2:
                app.tray.notify(
                    APP_NAME,
                    i18n.t("notify.recording_controller.still_running"),
                )
            # RACE-013: no need to create a new Timer. The persistent
            return

        if force:
            log.warning(
                "[RECOVERY] FORCE RECOVER: watchdog fired %d times with "
                "worker still alive; assuming deadlock and resetting state",
                firings,
            )
        else:
            log.warning("[RECOVERY] FORCE RECOVER: transcription watchdog fired, resetting state")
        # Record the current cycle_id as cancelled so the late
        cycle_id = getattr(app, "_cycle_id", None)
        if cycle_id is not None:
            # Use the bounded-registry helper so the set cannot grow
            self.mark_cycle_cancelled(controller, cycle_id)
            log.warning(
                "[STUCK-RECOVERY] cycle %s marked cancelled, late transcription will not be pasted",
                cycle_id,
            )
        # RACE-013: stop the persistent watchdog thread on recovery
        controller._stop_watchdog_thread()
        # Cancel the streaming session BEFORE resetting _busy_event.
        try:
            controller._cancel_streaming_session()
        except Exception:
            log.debug(
                "[RECOVERY] failed to cancel streaming session during force-recover",
                exc_info=True,
            )
        # Forced recovery cannot kill the stuck worker thread (threads
        if transcription_thread is not None and transcription_thread.is_alive():
            try:
                models = getattr(app, "models", None)
                if models is not None:
                    models.force_unload_active()
            except Exception:
                log.debug(
                    "[RECOVERY] failed to eject ASR backend during force-recover",
                    exc_info=True,
                )
        app._busyness.set_idle()  # busy = False (coordinator-routed)
        app.tray.set_state(AppState.IDLE, i18n.t("state.recording_controller.recovered"))
        # HOTKEY-THREAD: name the user's ACTUAL configured hotkey in the
        app.tray.notify(
            APP_NAME,
            i18n.t(
                "notify.recording_controller.cancelled_timeout",
                hotkey=notification_hotkey_label(app.config.hotkey),
            ),
        )
        app._schedule_timer(5.0, lambda: app.tray.set_state(AppState.IDLE))
        # Privacy: clear the shared audio slot so the raw voice bytes can
        controller._current_audio = None
        with contextlib.suppress(Exception):
            gc.collect()

    # ── Persistent watchdog thread (RACE-013) ──────────────────────────

    def start_thread(self, controller) -> None:
        """Start or reset the persistent watchdog thread.

        RACE-013: replaces the old chained threading.Timer pattern. A
        single daemon thread loops on ``_watchdog_event.wait(timeout=60)``.
        When transcription completes normally, ``reset_watchdog()`` sets
        the event, causing wait() to return early and the loop to reset
        firings + clear the event for the next cycle. When wait() times
        out (transcription hung), the watchdog fires the recovery action.

        If the previous watchdog thread is in the process of dying
        (``is_alive()`` True but about to exit), we briefly
        ``join(timeout=0.1)`` it and re-check. Without this, a thread
        that's between ``is_alive()`` returning True and actual exit would
        be orphaned (we'd start a new thread but the old one would still
        be running for a few microseconds, possibly firing its recovery
        action out of order). The join is bounded so a hung thread
        doesn't block the start path.

        Hold ``_watchdog_lock`` across the ENTIRE read-check-create-start
        sequence. Pre-fix, only the ``_watchdog_firings = 0`` reset was
        under the lock, the subsequent read of ``controller._watchdog_thread``,
        the ``is_alive()`` check, the bounded ``join()``, the assignment
        of a fresh ``Thread`` object, and ``start()`` all happened
        lock-free. Two concurrent callers (e.g. ``_stop_impl`` on the
        hotkey thread racing with a re-arm from
        ``_force_recover_from_stuck_transcription`` on the watchdog
        thread) could both observe ``_watchdog_thread is None`` (or both
        see it as dead) and both spawn a fresh ``TranscriptionWatchdog``
        thread, orphaning the loser. The lock is held only for the brief
        read-check-create-start sequence, the bounded ``join(timeout=0.1)``
        is the longest step, and the watchdog loop's own lock holds (in
        ``watchdog_loop`` and ``_force_recover_from_stuck_transcription``)
        are even briefer, so there is no deadlock risk.
        """
        with controller._watchdog_lock:
            controller._watchdog_firings = 0
            # Clear any previous reset signal
            controller._watchdog_event.clear()
            # If the thread is already running, just reset the counter
            if controller._watchdog_thread is not None and controller._watchdog_thread.is_alive():
                # Thread reports alive, try a bounded join to let it
                try:
                    controller._watchdog_thread.join(timeout=0.1)
                except Exception:
                    log.debug(
                        "[DICTATION] watchdog thread join raised, best-effort",
                        exc_info=True,
                    )
                if controller._watchdog_thread.is_alive():
                    # Still alive after join, reuse it (don't start a second one)
                    return
                # else: thread exited during the join window; fall through
            controller._watchdog_stop_event.clear()
            controller._watchdog_thread = threading.Thread(
                target=controller._watchdog_loop,
                name="TranscriptionWatchdog",
                daemon=True,
            )
            controller._watchdog_thread.start()
            _LIVE_WATCHDOG_CONTROLLERS.add(controller)

    def loop(self, controller) -> None:
        """Persistent watchdog loop, runs on the watchdog daemon thread."""
        while not controller._watchdog_stop_event.is_set():
            # Wait up to 90s. Returns True if the event was set (reset),
            timed_out = not controller._watchdog_event.wait(timeout=90.0)
            if controller._watchdog_stop_event.is_set():
                return
            if timed_out:
                with controller._watchdog_lock:
                    controller._watchdog_firings += 1
                    firings = controller._watchdog_firings
                controller._force_recover_from_stuck_transcription(
                    force=firings >= controller._watchdog_max_firings,
                )
                # If force-recovery happened, the watchdog thread is
                if controller._watchdog_stop_event.is_set():
                    return
            else:
                # Event was set (transcription completed or reset).
                with controller._watchdog_lock:
                    controller._watchdog_firings = 0
                controller._watchdog_event.clear()

    def reset_watchdog(self, controller) -> None:
        """Signal the watchdog that transcription completed normally."""
        controller._watchdog_event.set()

    def stop_thread(self, controller) -> None:
        """Stop the persistent watchdog thread."""
        controller._watchdog_stop_event.set()
        controller._watchdog_event.set()  # break out of wait()
        t = controller._watchdog_thread
        if t is not None and t is not threading.current_thread():
            with contextlib.suppress(Exception):
                t.join(timeout=1.0)
            # Only null the reference if the thread actually exited. If
            if not t.is_alive():
                controller._watchdog_thread = None
        else:
            # Self-join case (watchdog thread calling ``stop_thread`` from
            controller._watchdog_thread = None
        # Test-harness registry: the controller's watchdog is (about to
        _LIVE_WATCHDOG_CONTROLLERS.discard(controller)
