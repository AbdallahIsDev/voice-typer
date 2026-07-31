"""Smart-duck background monitor — extracted from ``volume_ducker.py``.

``volume_ducker.py`` exceeded the 800-line module-size
threshold (845 LOC). The smart-duck monitor (the background thread
that polls ``backend.is_speaker_active()`` during a smart-duck skip
and retroactively ducks if audio starts mid-dictation) is a
self-contained sub-feature with its own thread, its own stop event,
and its own poll loop — extracted here as a mixin so
:class:`voice_typer.server.volume_ducker.VolumeDucker` stays under
the 800-line ceiling.

The mixin pattern preserves architecture: ``VolumeDucker`` continues
to own the lifecycle (``duck`` / ``restore`` / ``set_smart_duck_*``)
and the shared state (``_lock``, ``_monitor_thread``,
``_monitor_stop``, ``_saved_state``, ``_actually_ducked``,
``_smart_duck_enabled``, ``_smart_duck_poll_ms``, ``_backend``,
``_ducked_level``, ``_crash_recovery``); the mixin contributes ONLY
the monitor's thread-management + poll-loop methods. All ``self.X``
accesses resolve via MRO to the same instance, so there is no
behavioral change.

The mixin is intentionally NOT registered as a separate plug-in or
factory — it's a pure code-organisation extraction. Callers continue
to construct ``VolumeDucker`` (which inherits this mixin) and call
``duck``/``restore`` exactly as before.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


class SmartDuckMonitorMixin:
    """Smart-duck background monitor methods, extracted from
    :class:`VolumeDucker` for the module-size split.

    This mixin assumes the host class provides the following instance
    attributes (all initialised by ``VolumeDucker.__init__``):

    - ``_lock: threading.Lock``
    - ``_monitor_thread: threading.Thread | None``
    - ``_monitor_stop: threading.Event``
    - ``_smart_duck_poll_ms: int``
    - ``_smart_duck_enabled: bool``
    - ``_saved_state`` (a :class:`VolumeState` or ``None``)
    - ``_actually_ducked: bool``
    - ``_backend`` (a :class:`VolumeBackend` or ``None``)
    - ``_ducked_level: float``
    - ``_crash_recovery`` (a :class:`DuckCrashRecovery` or ``None``)

    The mixin owns NO state of its own — it is a pure method group.
    """

    def _start_smart_duck_monitor(self, level: float, fade_ms: int, per_session: bool) -> None:
        """Start the background speaker-activity monitor.

        Called from :meth:`duck` when smart-duck skips the initial
        duck.  The monitor polls ``backend.is_speaker_active()`` every
        ``_smart_duck_poll_ms`` and, if audio starts, retroactively
        applies the duck by calling the same fade/crash-recovery path
        that :meth:`duck` would have used.

        Thread-safe: the monitor acquires ``self._lock`` before
        touching shared state.  If a monitor is already running
        (e.g. second ``duck()`` call after a skip), it is left alone —
        it reads ``_ducked_level`` on each poll so it picks up level
        changes automatically.
        """
        # If a monitor is already running, don't start a second one.
        # The existing monitor reads _ducked_level on each poll, so it
        # will pick up the new level automatically.
        #
        # Thread-safety: we hold self._lock here (callers duck() and
        # the monitor loop both acquire it before calling us / checking
        # state), so this check + assignment is atomic with respect to
        # other duck() calls.  _stop_smart_duck_monitor captures the
        # thread reference locally before joining, so it won't race
        # with us setting self._monitor_thread.
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        # Clear the stop event from any previous monitor run.
        self._monitor_stop.clear()
        # Create + start the thread BEFORE assigning to
        # _monitor_thread — _stop_smart_duck_monitor reads this ref
        # without the lock and join()s it; if we assigned first and
        # restore() ran between assignment and .start(), the join would
        # crash with "cannot join thread before it is started".
        t = threading.Thread(
            target=self._smart_duck_monitor_loop,
            name="smart-duck-monitor",
            daemon=True,
            kwargs={"fade_ms": fade_ms, "per_session": per_session},
        )
        t.start()
        self._monitor_thread = t

    def _stop_smart_duck_monitor(self) -> None:
        """Signal the background monitor to stop WITHOUT joining.

        previously this method joined the monitor thread with a
        timeout of ``poll_ms + 1000ms``. When called from
        :meth:`set_smart_duck_enabled` (via ``config_applier`` while
        holding ``_config_mutation_lock``), that join blocked all IPC
        ``set_config`` calls for up to 6 seconds (at the 5000ms poll
        cap) — freezing the Settings UI on a volume-duck toggle.

        The fix is non-blocking: signal the ``_monitor_stop`` Event,
        clear the ``_monitor_thread`` reference, and return immediately.
        The monitor thread is a daemon, so it will exit on its own
        within one poll iteration (``poll_ms``) when it sees the Event
        is set.

        Clearing ``_monitor_thread`` makes :attr:`is_monitor_running`
        return False immediately (so the UI reflects the stop without
        waiting for the thread to wind down). We do NOT clear
        ``_monitor_stop`` itself — :meth:`_start_smart_duck_monitor`
        clears it before starting a new thread, and clearing it here
        could cancel the stop signal for a winding-down thread (the
        thread reads ``_monitor_stop`` fresh on each poll iteration).

        Safe to call when no monitor is running (early-return on None).
        """
        # Capture the thread reference locally — another thread may
        # call _stop_smart_duck_monitor() concurrently.
        thread = self._monitor_thread
        if thread is None:
            return
        # Signal the monitor to stop. The monitor's poll loop sleeps in
        # _monitor_stop.wait(poll_ms) increments, so the Event.set()
        # wakes it immediately — worst-case exit latency is one
        # is_speaker_active() call (~50ms on Linux, ~500ms on macOS).
        self._monitor_stop.set()
        # clear _monitor_thread so is_monitor_running returns
        # False immediately (non-blocking). The thread is a daemon and
        # will exit on its own; we do NOT join (joining would block the
        # caller, potentially while holding _config_mutation_lock).
        # NOTE: do NOT clear _monitor_stop — _start_smart_duck_monitor
        # clears it before starting a new thread. Clearing it here would
        # cancel the stop signal for the winding-down thread.
        self._monitor_thread = None

    def _smart_duck_monitor_loop(self, fade_ms: int, per_session: bool) -> None:
        """Background thread: poll is_speaker_active() and retroactively duck.

        Runs until ``_monitor_stop`` is set (by ``restore()`` or
        ``set_smart_duck_enabled(False)``) OR until we successfully
        apply a retroactive duck (at which point the monitor's job is
        done — there's no need to keep polling once we've ducked).

        Thread safety: every iteration acquires ``self._lock`` before
        touching shared state.  The ``is_speaker_active()`` call is
        made OUTSIDE the lock (it may block on osascript/pactl) and
        the result is re-checked under the lock before applying the
        duck — this prevents a TOCTOU race where restore() runs
        between our poll and our duck.
        """
        while not self._monitor_stop.is_set():
            # Sleep first (we already checked is_speaker_active() in
            # duck() before starting the monitor).  Read the poll
            # interval fresh each iteration so a Settings UI slider
            # change takes effect on the next poll — not just the next
            # monitor start.  Using Event.wait() instead of time.sleep()
            # so _stop_smart_duck_monitor() can wake us immediately.
            if self._monitor_stop.wait(self._smart_duck_poll_ms / 1000.0):
                return  # stop signal received

            # Check if we should still be running.  Another thread may
            # have called restore() or set _actually_ducked=True.
            with self._lock:
                if self._saved_state is None:
                    # restore() was called — exit.
                    return
                if self._actually_ducked:
                    # We already applied the duck (or duck() was called
                    # again and faded).  Monitor's job is done.
                    return
                if not self._smart_duck_enabled:
                    # User disabled smart-duck mid-dictation.
                    return

            # Query the backend OUTSIDE the lock — is_speaker_active()
            # may block (osascript: 200-500ms, pactl: ~50ms).  Holding
            # the lock during that time would block restore() and
            # set_smart_duck_enabled().
            try:
                #  (fix): explicit null check instead of type: ignore
                speaker_active = False if self._backend is None else self._backend.is_speaker_active()
            except Exception as exc:
                log.debug("[VOLUME] monitor: is_speaker_active failed: %s", exc)
                speaker_active = False  # don't duck on error — try again next poll

            if not speaker_active:
                continue  # still silent — keep polling

            # Audio started!  Apply the retroactive duck.
            #
            # Mirror the  pattern from ``duck()`` —
            # snapshot shared state under the lock, release the lock
            # for the heavy ``backend.fade_to()`` call (which can
            # block for up to 150 ms), then re-acquire the lock for
            # the post-fade state writes.  Holding the lock during the
            # fade serialises ``restore()`` (ESC cancel) behind the
            # fade — visible as a 150 ms "ESC doesn't respond" delay,
            # the same bug  fixed for ``duck()``.
            #
            # Persist the crash-recovery file BEFORE the fade,
            # under the lock — same rationale as ``duck()``: if the
            # process crashes mid-fade, the recovery file must already
            # exist for the next launch.
            #
            # Re-check all the invariants under the lock because the
            # state may have changed between our unlocked poll and
            # here (and again after the fade — ``restore()`` may run
            # while we're fading).
            with self._lock:
                if self._saved_state is None or self._actually_ducked:
                    # restore() ran, or another path already ducked.
                    return
                if not self._smart_duck_enabled:
                    return
                level = self._ducked_level
                state = self._saved_state

                log.info(
                    "[VOLUME] Audio started mid-dictation — retroactive duck -> %.0f%%",
                    level * 100,
                )
                # Persist crash-recovery BEFORE the fade (under
                # the lock so ``_saved_state`` and the file stay
                # consistent).  See ``duck()`` for the full rationale.
                # If the fade fails the file is stale but harmless —
                # ``restore()`` clears it on shutdown, and a crash
                # before ``restore()`` recovers to the saved (pre-duck)
                # volume, which is a no-op when the fade never applied.
                if self._crash_recovery is not None:
                    self._crash_recovery.save(state)
                # Snapshot for the unlocked fade.
                target_level = level
                target_fade_ms = fade_ms
                use_per_session = per_session and self._backend is not None and self._backend.supports_per_session
                backend_ref = self._backend

            # -- Heavy fade OUTSIDE the lock ( pattern) --
            if backend_ref is None:
                ok = False
            elif use_per_session:
                ok = backend_ref.duck_other_sessions(target_level)
                if not ok:
                    ok = backend_ref.fade_to(target_level, target_fade_ms)
            else:
                ok = backend_ref.fade_to(target_level, target_fade_ms)

            # -- Post-fade state writes UNDER the lock --
            with self._lock:
                if self._saved_state is None:
                    # ``restore()`` ran during the fade and cleared
                    # ``_saved_state`` (and the crash-recovery file).
                    # Don't mark ``_actually_ducked = True`` — the
                    # volume was already restored.
                    log.info("[VOLUME] restore() ran during retroactive-duck fade — skipping state update")
                    return
                if ok:
                    self._actually_ducked = True
                else:
                    log.warning("[VOLUME] retroactive duck failed — will retry next poll")
                    # The crash-recovery file was saved
                    # before the fade.  On a successful retry we'll
                    # re-save (same state — idempotent); on a clean
                    # shutdown ``restore()`` clears it.  Leave
                    # ``_actually_ducked = False`` so we retry on the
                    # next poll.  Don't return — give it another shot.

            if ok:
                # We successfully ducked.  Monitor's job is done.
                return

    @property
    def is_monitor_running(self) -> bool:
        """``True`` if the smart-duck background monitor is currently running.

        Useful for diagnostics and tests.
        """
        return self._monitor_thread is not None and self._monitor_thread.is_alive()


__all__ = ["SmartDuckMonitorMixin"]
