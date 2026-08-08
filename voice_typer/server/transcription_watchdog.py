"""Transcription watchdog — extracted from ``RecordingController``
(Phase 4.5 split).

Owns the persistent watchdog thread that monitors the transcription
worker for stuck-ctranslate2-call recovery, plus the bounded
``_cancelled_cycle_ids`` LRU registry that prevents late transcription
results from being pasted into whatever window currently has focus
after a user-initiated cancel or a watchdog force-recover.

Collaborator pattern
--------------------
:class:`TranscriptionWatchdog` is constructed by
``RecordingController.__init__`` with NO arguments (stateless). Each
method takes a back-reference to the owning ``RecordingController``
(``controller``) and reads/writes ``controller._watchdog_lock``,
``controller._watchdog_thread``, ``controller._watchdog_event``,
``controller._watchdog_stop_event``, ``controller._watchdog_firings``,
``controller._cancelled_cycle_ids``,
``controller._cancelled_cycle_ids_lock``, ``controller._current_audio``,
etc.

``RecordingController`` keeps 1-line delegator methods
(``_start_watchdog_thread``, ``_watchdog_loop``, ``_reset_watchdog``,
``_stop_watchdog_thread``, ``_force_recover_from_stuck_transcription``,
``_mark_cycle_cancelled``, ``_discard_cancelled_cycle_id``) so existing
call sites, tests that monkeypatch the controller's methods, and tests
that construct a controller via ``__new__`` (skipping ``__init__``)
keep working unchanged.

Patch-path compatibility
------------------------
Tests do ``patch("voice_typer.server.recording_controller.gc.collect")``
to spy on the GC call inside ``_force_recover_from_stuck_transcription``.
Because the ``gc`` module is a singleton (one object in ``sys.modules``),
patching ``recording_controller.gc.collect`` patches ``gc.collect``
GLOBALLY — so the call from this module's ``force_recover`` (which
imports ``gc`` directly) is still intercepted. ``recording_controller.py``
keeps ``import gc`` at module top so the patch PATH resolves cleanly.

Originally lines 261–328 and 1768–2055 of ``recording_controller.py``.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import threading
import weakref

from voice_typer.server import i18n
from voice_typer.server.branding import APP_NAME
from voice_typer.server.tray_types import AppState

log = logging.getLogger(__name__)

# Registry of controllers with a live watchdog thread. Lets the test
# harness drain leaked watchdog threads between tests (mirrors
# ``crash_recovery._LIVE_INSTANCES``); production behaviour is
# unaffected — a WeakSet drops controllers automatically on GC.
_LIVE_WATCHDOG_CONTROLLERS: weakref.WeakSet = weakref.WeakSet()


# Bounded cap for ``_cancelled_cycle_ids``. Each cancel event (ESC-during-
# transcription, watchdog force-recover) appends one cycle_id; without a
# cap, the set grew by one entry per cancel event forever — a slow memory
# leak on long-lived processes that get cancelled a lot (e.g. a user who
# habitually ESC-cancels half-finished dictations). The OrderedDict-based
# LRU eviction in ``mark_cycle_cancelled`` keeps the registry at <=
# this many entries, evicting the OLDEST entries first (entries are not
# re-touched on read, so oldest == least-recently-added). 1000 is well
# above the realistic working set (a user would have to cancel 1000
# distinct cycles within a single process lifetime for eviction to
# matter) and small enough that the per-entry memory cost (a ~40-byte
# str key + dict slot) is bounded to ~40 KB worst case.
_MAX_CANCELLED_IDS = 1000


class TranscriptionWatchdog:
    """Persistent transcription-watchdog thread + force-recover + cancelled-
    cycle LRU registry.

    Extracted from the former ``RecordingController._force_recover_from_stuck_transcription``
    / ``_start_watchdog_thread`` / ``_watchdog_loop`` / ``_reset_watchdog``
    / ``_stop_watchdog_thread`` / ``_mark_cycle_cancelled`` /
    ``_discard_cancelled_cycle_id`` methods. Each method's body is the
    moved implementation, with ``self.X`` references rewritten to
    ``controller.X`` for shared state. ``RecordingController`` keeps
    1-line delegators on each method name so existing call sites and
    tests continue to work.
    """

    def __init__(self) -> None:
        # Stateless helper — all state lives on the controller.
        pass

    # ── Cancelled-cycle LRU registry ───────────────────────────────────

    def mark_cycle_cancelled(self, controller, cycle_id: str) -> None:
        """Record a cycle_id as force-cancelled (watchdog / ESC-during-
        transcription) with LRU eviction at ``_MAX_CANCELLED_IDS``.

        Thread-safe: acquires ``_cancelled_cycle_ids_lock`` for the
        check-then-insert-then-evict sequence so two concurrent
        cancellations cannot both pass the membership check and both
        append (which would let the dict momentarily exceed the cap).

        The OrderedDict's insertion order is the eviction order —
        ``popitem(last=False)`` removes the OLDEST entry. We do NOT
        ``move_to_end`` on an existing key (re-touch on read is not part
        of the contract; the registry only grows when a NEW cancel event
        fires, and old entries are evicted FIFO once the cap is reached).
        This matches the set semantics pre-fix (a set has no ordering at
        all) while bounding the memory cost.

        Duck-typed for tests: if ``_cancelled_cycle_ids`` is a plain
        ``set`` (the pre-fix type, still used by tests that construct a
        controller via ``__new__`` and assign ``set()`` directly), we
        fall back to ``set.add()`` and skip the LRU eviction (a set has
        no insertion order, so FIFO eviction is undefined). Production
        always uses the ``OrderedDict`` from ``__init__``.
        """
        with controller._cancelled_cycle_ids_lock:
            if cycle_id in controller._cancelled_cycle_ids:
                # Already cancelled — no-op (the watchdog / ESC may fire
                # more than once for the same cycle; idempotent).
                return
            if isinstance(controller._cancelled_cycle_ids, set):
                # Test-double path: plain set has no ordering, so no LRU
                # eviction. The set membership check still works for the
                # ``CancellationGuard`` lookup. Production uses an
                # OrderedDict (see ``__init__``) which DOES support
                # eviction.
                controller._cancelled_cycle_ids.add(cycle_id)
                return
            controller._cancelled_cycle_ids[cycle_id] = None
            if len(controller._cancelled_cycle_ids) > _MAX_CANCELLED_IDS:
                # Evict the OLDEST entry (FIFO). ``popitem(last=False)``
                # returns ``(key, value)``; we discard both — only the
                # key matters for the membership check.
                controller._cancelled_cycle_ids.popitem(last=False)

    def discard_cancelled_cycle_id(self, controller, cycle_id: str) -> None:
        """Best-effort removal of a cycle_id from the cancelled registry.

        Called from ``_run_stop_and_transcribe`` after the pipeline
        returns (whether the cycle was cancelled or not) so a cycle that
        completed normally — or whose late transcription has already been
        observed + dropped by ``CancellationGuard`` — does not linger in
        the registry until the ``_MAX_CANCELLED_IDS`` cap evicts it years
        later.

        Thread-safe: acquires ``_cancelled_cycle_ids_lock``. Silent no-op
        if ``cycle_id`` is not present (the common case — most cycles are
        never cancelled, so there's nothing to discard).

        Duck-typed for tests: handles both ``set`` (``set.discard``) and
        ``OrderedDict`` (``dict.pop`` with KeyError suppression).
        """
        with controller._cancelled_cycle_ids_lock:
            if isinstance(controller._cancelled_cycle_ids, set):
                controller._cancelled_cycle_ids.discard(cycle_id)
                return
            with contextlib.suppress(KeyError):
                controller._cancelled_cycle_ids.pop(cycle_id)

    # ── Force-recover ──────────────────────────────────────────────────

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
        instead of notifying the user — the notification only fires on
        the SECOND firing (120s+) when the transcription is genuinely
        taking an unusually long time. The watchdog time for the first
        firing was also raised from 60s to 90s.
        """
        app = controller._app
        if app._busy_event.is_set():  # not busy
            return  # Already recovered, nothing to do
        # Snapshot ``_transcription_thread`` and ``_watchdog_firings``
        # under ``_watchdog_lock`` for the duration of the read-check-notify
        # block. Previously the read of ``controller._transcription_thread``
        # and the subsequent ``is_alive()`` call happened without the lock
        # — a concurrent ``stop()`` on the Timer/hotkey thread could be
        # mid-assignment of ``controller._transcription_thread`` (now also
        # done under this lock, see ``_stop_impl``), letting the watchdog
        # see a stale ``None`` (treats dead thread as recovered) or the
        # previous cycle's thread (incorrectly leaves app busy).
        # ``_watchdog_firings`` is mutated under this lock in
        # ``watchdog_loop`` and ``start_thread`` — read it here under the
        # same lock to pair the snapshot with the check.
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
        # Record the current cycle_id as cancelled so the late
        # transcription (when the stuck ctranslate2 call eventually
        # completes 5-30 min later) will NOT be pasted into whatever
        # window currently has focus. ``DictationPipeline.run()`` checks
        # this set BEFORE ``_copy_and_paste`` and skips the paste if the
        # cycle is present.
        cycle_id = getattr(app, "_cycle_id", None)
        if cycle_id is not None:
            # Use the bounded-registry helper so the set cannot grow
            # unbounded across many stuck-recovery events (LRU eviction
            # at ``_MAX_CANCELLED_IDS``).
            self.mark_cycle_cancelled(controller, cycle_id)
            log.warning(
                "[STUCK-RECOVERY] cycle %s marked cancelled — late transcription will not be pasted",
                cycle_id,
            )
        # RACE-013: stop the persistent watchdog thread on recovery
        controller._stop_watchdog_thread()
        # Cancel the streaming session BEFORE resetting _busy_event.
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
        # cancels the session (``pop_streaming_session`` is TOCTOU-safe),
        # releasing our Python-side reference immediately. If the worker
        # thread is still alive after ``cancel()``, it will exit on its
        # next ``_cancel_event`` check — we do NOT block on it
        # (``cancel()`` is best-effort; ctranslate2 cannot be interrupted
        # mid-call).
        try:
            controller._cancel_streaming_session()
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
        # Privacy: clear the shared audio slot so the raw voice bytes can
        # be garbage-collected once the only remaining reference is the
        # stuck ctranslate2 call (which we cannot reach). ``gc.collect()``
        # is a best-effort nudge: CPython's GC is generational, so a
        # single collection pass may not free every orphaned cycle
        # immediately, but it surfaces the audio bytes to the next cycle's
        # sweep rather than waiting for the next natural collection (which
        # may be 30+ seconds away on a quiet process). The C-level
        # retention by a stuck ctranslate2 call is documented as an
        # engine-level limitation outside this module's control.
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
        under the lock — the subsequent read of ``controller._watchdog_thread``,
        the ``is_alive()`` check, the bounded ``join()``, the assignment
        of a fresh ``Thread`` object, and ``start()`` all happened
        lock-free. Two concurrent callers (e.g. ``_stop_impl`` on the
        hotkey thread racing with a re-arm from
        ``_force_recover_from_stuck_transcription`` on the watchdog
        thread) could both observe ``_watchdog_thread is None`` (or both
        see it as dead) and both spawn a fresh ``TranscriptionWatchdog``
        thread, orphaning the loser. The lock is held only for the brief
        read-check-create-start sequence — the bounded ``join(timeout=0.1)``
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
                # Thread reports alive — try a bounded join to let it
                # exit cleanly, then re-check. If still alive after the
                # join, we keep the existing thread (don't orphan a hung
                # thread by overwriting ``_watchdog_thread``).
                try:
                    controller._watchdog_thread.join(timeout=0.1)
                except Exception:
                    log.debug(
                        "[DICTATION] watchdog thread join raised — best-effort",
                        exc_info=True,
                    )
                if controller._watchdog_thread.is_alive():
                    # Still alive after join — reuse it (don't start a second one)
                    return
                # else: thread exited during the join window; fall through
                # and start a fresh thread.
            controller._watchdog_stop_event.clear()
            controller._watchdog_thread = threading.Thread(
                target=controller._watchdog_loop,
                name="TranscriptionWatchdog",
                daemon=True,
            )
            controller._watchdog_thread.start()
            _LIVE_WATCHDOG_CONTROLLERS.add(controller)

    def loop(self, controller) -> None:
        """Persistent watchdog loop — runs on the watchdog daemon thread.

        TRANSCRIBE-NOTIFY-FIX: initial timeout increased from 60s to 90s
        to reduce false-positive "transcription is still running"
        notifications for longer recordings or CPU fallback scenarios.
        """
        while not controller._watchdog_stop_event.is_set():
            # Wait up to 90s. Returns True if the event was set (reset),
            # False if it timed out (transcription hung).
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
                # stopped by ``_stop_watchdog_thread()`` inside
                # ``_force_recover_from_stuck_transcription``. Break out.
                if controller._watchdog_stop_event.is_set():
                    return
            else:
                # Event was set (transcription completed or reset).
                # Reset firings and clear the event for the next cycle.
                with controller._watchdog_lock:
                    controller._watchdog_firings = 0
                controller._watchdog_event.clear()

    def reset_watchdog(self, controller) -> None:
        """Signal the watchdog that transcription completed normally.

        Called from the pipeline's finally block. Setting the event
        causes the watchdog's Event.wait() to return True immediately,
        which resets the firing counter.
        """
        controller._watchdog_event.set()

    def stop_thread(self, controller) -> None:
        """Stop the persistent watchdog thread.

        Signals the thread to stop via the stop event, then joins it
        (best-effort, bounded at 1.0s so a hung thread doesn't block the
        caller indefinitely) and nulls the reference — mirroring
        ``_stop_mic_level_worker``.

        The ``current_thread()`` guard prevents a self-join deadlock: the
        watchdog thread calls this method via
        ``_force_recover_from_stuck_transcription`` from inside its own
        loop, where joining ourselves would block forever. The guard
        skips the join in that case but still nulls the reference so the
        dead ``Thread`` object doesn't stay referenced until the next
        ``_start_watchdog_thread`` (which can be hours apart in a
        long-running tray app).
        """
        controller._watchdog_stop_event.set()
        controller._watchdog_event.set()  # break out of wait()
        t = controller._watchdog_thread
        if t is not None and t is not threading.current_thread():
            with contextlib.suppress(Exception):
                t.join(timeout=1.0)
            # Only null the reference if the thread actually exited. If
            # still alive (stuck in a long operation), keep the reference
            # so ``start_thread``'s ``is_alive()`` guard reuses it
            # instead of spawning a duplicate (zombie thread leak
            # mitigation — mirrors the pattern at device_manager.py's
            # ``_stop_device_health_checker``). The stop event is left
            # SET so the zombie exits on its next iteration boundary.
            if not t.is_alive():
                controller._watchdog_thread = None
        else:
            # Self-join case (watchdog thread calling ``stop_thread`` from
            # inside its own loop) — can't join ourselves, and the thread
            # will exit naturally after returning, so null the reference.
            # Also covers the ``t is None`` case (no-op).
            controller._watchdog_thread = None
        # Test-harness registry: the controller's watchdog is (about to
        # be) dead, so drop it from the live set (best-effort).
        _LIVE_WATCHDOG_CONTROLLERS.discard(controller)
