"""VolumeDucker — orchestrates system audio volume ducking during dictation.

When dictation starts, system volume is reduced (ducked) to a
configurable level (default 25%).  When dictation stops, the original
volume — including mute state — is restored with a short fade ramp.

Key behaviours:

- **Mute-state preservation**: if the system was muted before ducking,
  ``restore()`` re-mutes it.  Without this, a muted user would end up
  unmuted after each dictation.

- **Manual-volume-override detection**: if the user manually changes
  the volume while ducked (current volume differs from the ducked level
  by more than 5%), ``restore()`` respects the manual change and
  restores to the *current* volume rather than the saved original.
  This prevents the app from "slamming" the volume back if the user
  intentionally turned it up to hear a notification.

- **Crash recovery**: the pre-duck state is persisted to
  ``duck_crash_recovery.json``.  If the app crashes while ducked, the
  next launch detects the stale file and restores the volume before
  any new ducking occurs.

- **Fade ramp**: volume transitions are smoothed over 150 ms (default)
  to avoid audio clicks/pops on some DACs and to feel less jarring.

- **Smart duck** (v2.2): when enabled, ``duck()`` first calls
  ``backend.is_speaker_active()`` and skips the volume change if no
  application is currently playing audio.  Avoids a pointless
  speaker-icon animation during silent dictation.

- **Smart-duck background monitor** (v2.3): if smart-duck skipped the
  duck (no audio at start), a background daemon thread polls
  ``is_speaker_active()`` every ``poll_interval_ms`` (default 500ms)
  during dictation.  If audio starts playing mid-dictation, the monitor
  retroactively applies the duck — closing the gap where speaker bleed
  could leak into the mic.  The monitor stops automatically when
  ``restore()`` is called or smart-duck is disabled.  See
  :meth:`_smart_duck_monitor_loop`.

- **Thread safety**: all public methods hold ``self._lock``.
  ``_cancel_dictation`` (ESC hotkey thread) and ``_stop_dictation``
  (background thread) can fire concurrently; the lock serialises them
  and the second call is a no-op.

Platform backends are selected by ``platform.get_volume_backend()`` and
implement the :class:`VolumeBackend` ABC.  If no backend is available,
ducking is a silent no-op — the app continues normally.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
from voice_typer.server.volume_backend import VolumeBackend, VolumeState

log = logging.getLogger(__name__)

# If the current volume differs from the ducked level by more than this
# fraction, we assume the user manually changed it during dictation.
_MANUAL_OVERRIDE_THRESHOLD = 0.05

# Default polling interval for the smart-duck background monitor.
# 500ms is a balance: fast enough to catch audio within half a second,
# slow enough to not spam the backend (macOS osascript is 200-500ms
# per call; Windows IAudioMeterInformation is ~0ms; Linux pactl is
# ~50ms).  Override via set_smart_duck_poll_interval().
_DEFAULT_SMART_DUCK_POLL_MS = 500


class VolumeDucker:
    """Manages system audio volume ducking during dictation.

    Platform-agnostic: delegates to a :class:`VolumeBackend` selected by
    ``platform.get_volume_backend()``.
    """

    def __init__(
        self,
        backend: Optional[VolumeBackend] = None,
        crash_recovery: Optional[DuckCrashRecovery] = None,
        on_crash_restore: Optional[Callable[[VolumeState], None]] = None,
    ) -> None:
        """Initialise the ducker.

        Parameters
        ----------
        backend:
            The volume backend to use.  If ``None``, ``initialize()``
            will auto-detect via ``platform.get_volume_backend()``.
        crash_recovery:
            Optional :class:`DuckCrashRecovery` for crash persistence.
            If ``None``, crash recovery is disabled.
        on_crash_restore:
            Optional callback invoked when a stale crash-recovery file
            is found and the volume has been restored.  Used by the app
            to show a tray notification warning the user.
        """
        self._backend: Optional[VolumeBackend] = backend
        self._crash_recovery = crash_recovery
        self._on_crash_restore = on_crash_restore
        self._saved_state: Optional[VolumeState] = None
        self._ducked_level: float = 0.25
        self._actually_ducked: bool = False  # True if the volume was actually changed
        # Smart duck: when True (default), duck() first calls
        # backend.is_speaker_active() and skips the volume change if no
        # application is currently playing audio.  Set to False to
        # always duck (the pre-smart-duck behaviour).  Wired from
        # config.volume_duck_smart by VoiceTyperApp.
        self._smart_duck_enabled: bool = True
        # Smart-duck background monitor: polls is_speaker_active()
        # during a smart-duck skip so we can retroactively duck if
        # audio starts playing mid-dictation.  See
        # _smart_duck_monitor_loop().
        self._smart_duck_poll_ms: int = _DEFAULT_SMART_DUCK_POLL_MS
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop: threading.Event = threading.Event()
        self._lock = threading.Lock()
        self._initialized: bool = False
        self._ready: bool = False  # True only if initialize() succeeded

    # ── Lifecycle ───────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Detect platform, set up the backend, and check for crash recovery.

        Returns ``True`` if a working backend is available, ``False``
        otherwise (unsupported platform / missing library → graceful
        no-op).  Safe to call multiple times.
        """
        if self._initialized:
            return self._backend is not None

        if self._backend is None:
            from voice_typer.server.platform import get_volume_backend

            self._backend = get_volume_backend()

        if self._backend is None:
            log.info("[VOLUME] No volume backend available — ducking disabled")
            self._initialized = True
            self._ready = False
            return False

        ok = self._backend.initialize()
        self._initialized = True

        if not ok:
            log.warning(
                "[VOLUME] Backend %s failed to initialise", self._backend.name
            )
            self._ready = False
            return False
        self._ready = True

        log.info(
            "[VOLUME] Backend ready: %s (per_session=%s)",
            self._backend.name,
            self._backend.supports_per_session,
        )

        # Crash recovery: restore stale state from a previous crash.
        if self._crash_recovery is not None:
            stale = self._crash_recovery.load_stale()
            if stale is not None:
                log.warning(
                    "[VOLUME] Previous session crashed while ducked — "
                    "restoring volume to %.0f%% (muted=%s)",
                    stale.linear * 100,
                    stale.muted,
                )
                self._backend.set_linear(stale.linear, muted=stale.muted)
                self._crash_recovery.clear()
                if self._on_crash_restore is not None:
                    try:
                        self._on_crash_restore(stale)
                    except Exception:
                        log.debug("[VOLUME] crash-restore callback failed", exc_info=True)

        return True

    # ── Duck / Restore ──────────────────────────────────────────────

    def duck(
        self,
        level: float = 0.25,
        fade_ms: int = 150,
        per_session: bool = False,
    ) -> bool:
        """Reduce system volume to *level* (0.0–1.0 perceptual-linear).

        Saves the current volume + mute state before ducking so it can
        be restored exactly.  Subsequent calls update the level without
        re-saving.  Returns ``True`` on success, ``False`` if the
        backend failed or is not initialised.  Thread-safe.
        """
        if not self._initialized or self._backend is None:
            return False

        level = max(0.0, min(1.0, level))

        with self._lock:
            if self._saved_state is None:
                # First duck — save current state.
                state = self._backend.get_state()
                if state is None:
                    log.warning("[VOLUME] get_state failed — not ducking")
                    return False
                self._saved_state = state
                self._ducked_level = level

                # Smart duck: skip if no application is currently
                # playing audio through the speakers.  No point
                # animating the volume icon for silence.
                #
                # We still set _saved_state (so is_ducked reports True
                # for UI consistency and restore() knows to clean up),
                # but _actually_ducked=False tells restore() to skip
                # the fade-back.  No crash-recovery file is written
                # because we haven't actually changed the volume.
                #
                # v2.3: start a background monitor that polls
                # is_speaker_active() every poll_interval_ms.  If audio
                # starts playing mid-dictation, the monitor retroactively
                # applies the duck — closing the gap where speaker bleed
                # could leak into the mic.  See _smart_duck_monitor_loop.
                if self._smart_duck_enabled and not self._backend.is_speaker_active():
                    self._actually_ducked = False
                    log.info("[VOLUME] No audio output — duck skipped (smart duck, monitor started)")
                    self._start_smart_duck_monitor(level, fade_ms, per_session)
                    return True

                if per_session and self._backend.supports_per_session:
                    ok = self._backend.duck_other_sessions(level)
                    if not ok:
                        ok = self._backend.fade_to(level, fade_ms)
                else:
                    ok = self._backend.fade_to(level, fade_ms)
                self._actually_ducked = True

                if ok and self._crash_recovery is not None:
                    self._crash_recovery.save(state)

                log.info(
                    "[VOLUME] Duck -> %.0f%% (saved %.0f%%, muted=%s, per_session=%s)",
                    level * 100,
                    state.linear * 100,
                    state.muted,
                    per_session,
                )
                return ok
            else:
                # Already ducked — update level without re-saving.
                #
                # BUGFIX (v1.1): if smart-duck skipped the first duck
                # (_actually_ducked=False), we must NOT call fade_to
                # here — doing so would fade the user's volume down to
                # the new duck level with no saved state to restore
                # from.  Instead, just update the logical ducked_level
                # so a later restore() (if audio has since started)
                # knows the target.  The user's volume is unchanged.
                #
                # v2.3: if the smart-duck monitor is running, it will
                # pick up the new _ducked_level on its next poll — no
                # need to restart it.
                self._ducked_level = level
                if not self._actually_ducked:
                    log.info(
                        "[VOLUME] Duck level updated -> %.0f%% (smart-duck still skipping — no fade)",
                        level * 100,
                    )
                    return True
                ok = self._backend.fade_to(level, fade_ms)
                log.info("[VOLUME] Duck level updated -> %.0f%%", level * 100)
                return ok

    def restore(
        self,
        fade_ms: int = 150,
        force: bool = False,
        per_session: bool = False,
    ) -> bool:
        """Restore system volume to its pre-duck level + mute state.

        Detects manual volume changes during ducking: if the current
        volume differs from the ducked level by more than 5%, the user
        changed it intentionally → restore to the *current* value, not
        the saved one.  Use ``force=True`` to bypass this and always
        restore the saved value (used by crash recovery).

        Safe to call when not ducked (no-op success).  Thread-safe.
        """
        if not self._initialized or self._backend is None:
            return False

        # v2.3: stop the smart-duck background monitor before touching
        # state.  We do this in two phases to avoid a deadlock:
        #
        # 1. Signal the monitor to stop (thread-safe Event.set(), no
        #    lock needed).  This wakes the monitor's _monitor_stop.wait()
        #    immediately.
        # 2. Release the lock (we don't hold it yet — but we acquire it
        #    AFTER the join so the monitor can acquire it to check state
        #    and exit cleanly).
        #
        # We must NOT hold self._lock during thread.join() because the
        # monitor loop acquires self._lock to check _saved_state before
        # exiting.  Holding the lock during join would deadlock.
        self._stop_smart_duck_monitor()

        with self._lock:
            if self._saved_state is None:
                return True  # not ducked — no-op success

            if not self._actually_ducked:
                # Smart duck skipped the actual volume change because
                # no audio was playing.  Just clear the logical state.
                self._saved_state = None
                self._actually_ducked = False
                return True

            if per_session and self._backend.supports_per_session:
                self._backend.restore_other_sessions()

            current = self._backend.get_state()
            if current is None:
                log.warning("[VOLUME] get_state failed on restore — using saved value")
                target = self._saved_state
            elif not force and abs(current.linear - self._ducked_level) > _MANUAL_OVERRIDE_THRESHOLD:
                log.info(
                    "[VOLUME] Manual volume change detected during duck "
                    "(current=%.0f%%, ducked=%.0f%%) — restoring to current "
                    "instead of saved (%.0f%%)",
                    current.linear * 100,
                    self._ducked_level * 100,
                    self._saved_state.linear * 100,
                )
                target = current
            else:
                target = self._saved_state

            ok = self._backend.fade_to(target.linear, fade_ms)
            if ok:
                # Restore mute state AFTER the volume fade completes,
                # otherwise fading a muted device is a no-op.
                self._backend.set_linear(target.linear, muted=target.muted)
                if self._crash_recovery is not None:
                    self._crash_recovery.clear()

            log.info("[VOLUME] Restore -> %.0f%% (muted=%s)", target.linear * 100, target.muted)
            self._saved_state = None
            self._actually_ducked = False
            return ok

    # ── Introspection ───────────────────────────────────────────────

    @property
    def is_ducked(self) -> bool:
        """``True`` if volume is currently ducked (logically — may be a smart-duck skip)."""
        with self._lock:
            return self._saved_state is not None

    @property
    def actually_ducked(self) -> bool:
        """``True`` if the volume was actually changed (not a smart-duck skip).

        Useful for diagnostics and for tests — :attr:`is_ducked` returns
        ``True`` during a smart-duck skip (so the UI shows duck state
        consistently), but this property distinguishes "we skipped the
        fade" from "we actually lowered the volume".
        """
        with self._lock:
            return self._actually_ducked

    @property
    def smart_duck_enabled(self) -> bool:
        """``True`` if smart-duck (skip when no audio playing) is enabled."""
        return self._smart_duck_enabled

    def set_smart_duck_enabled(self, enabled: bool) -> None:
        """Enable or disable smart-duck at runtime.

        Wired from ``config.volume_duck_smart`` by :class:`VoiceTyperApp`
        on startup and whenever the config changes.  Takes effect on the
        next ``duck()`` call — does not affect an in-progress duck.

        v2.3: if smart-duck is disabled mid-dictation while the monitor
        is running, the monitor is stopped.  The current smart-duck
        skip state is left as-is (we don't retroactively duck — the
        user explicitly disabled the feature).  The next ``duck()``
        call will use the new setting.
        """
        self._smart_duck_enabled = bool(enabled)
        if not enabled:
            # Stop the monitor — the user disabled smart-duck, so we
            # shouldn't retroactively duck anymore.  We leave
            # _actually_ducked=False (if it was) so restore() is still
            # a no-op.  The volume is unchanged either way.
            self._stop_smart_duck_monitor()

    def set_smart_duck_poll_interval(self, ms: int) -> None:
        """Set the smart-duck monitor polling interval in milliseconds.

        Wired from ``config.volume_duck_smart_poll_interval_ms`` by
        :class:`VoiceTyperApp`.  Takes effect on the next monitor poll.
        Clamped to [50, 5000] — below 50ms risks starving the audio
        callback on slow backends (macOS osascript); above 5000ms is
        too slow to catch short audio bursts.
        """
        self._smart_duck_poll_ms = max(50, min(5000, int(ms)))

    # ── Smart-duck background monitor (v2.3) ────────────────────────

    def _start_smart_duck_monitor(
        self, level: float, fade_ms: int, per_session: bool
    ) -> None:
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
        """Signal the background monitor to stop and wait briefly for it.

        Called from :meth:`restore` and :meth:`set_smart_duck_enabled`
        (when disabling).  Safe to call when no monitor is running.
        Waits up to ``poll_interval_ms + 1000ms`` for the thread to
        exit so we don't race with a poll that's in flight.

        Thread-safe: captures the thread reference locally before
        joining, so a concurrent call from another thread doesn't
        race with us setting ``self._monitor_thread = None``.
        """
        # Capture the thread reference locally — another thread may
        # call _stop_smart_duck_monitor() concurrently and set
        # self._monitor_thread = None between our None-check and our
        # .join()/.is_alive() call.
        thread = self._monitor_thread
        if thread is None:
            return
        self._monitor_stop.set()
        # Wait for the thread to exit.  The poll loop sleeps in
        # _monitor_stop.wait(poll_ms) increments, so the worst-case
        # wait is poll_ms + the time for one is_speaker_active() call.
        # On macOS that's ~500ms (osascript); we cap the join at
        # poll_ms + 1000ms to avoid blocking shutdown indefinitely.
        join_timeout = (self._smart_duck_poll_ms / 1000.0) + 1.0
        thread.join(timeout=join_timeout)
        if thread.is_alive():
            log.warning(
                "[VOLUME] smart-duck monitor did not stop in %.1fs — "
                "it will exit when the next poll completes",
                join_timeout,
            )
        # Only clear self._monitor_thread if it still points at OUR
        # thread — a concurrent duck() may have started a new monitor
        # by now, and we don't want to clobber it.
        if self._monitor_thread is thread:
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
                speaker_active = self._backend.is_speaker_active()  # type: ignore[union-attr]
            except Exception as exc:
                log.debug("[VOLUME] monitor: is_speaker_active failed: %s", exc)
                speaker_active = False  # don't duck on error — try again next poll

            if not speaker_active:
                continue  # still silent — keep polling

            # Audio started!  Apply the retroactive duck under the
            # lock.  Re-check all the invariants because the state may
            # have changed between our unlocked poll and here.
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
                if per_session and self._backend is not None and self._backend.supports_per_session:
                    ok = self._backend.duck_other_sessions(level)
                    if not ok and self._backend is not None:
                        ok = self._backend.fade_to(level, fade_ms)
                elif self._backend is not None:
                    ok = self._backend.fade_to(level, fade_ms)
                else:
                    ok = False

                if ok:
                    self._actually_ducked = True
                    if self._crash_recovery is not None:
                        self._crash_recovery.save(state)
                else:
                    log.warning("[VOLUME] retroactive duck failed — will retry next poll")
                    # Leave _actually_ducked=False so we retry on the
                    # next poll.  Don't return — give it another shot.

            if ok:
                # We successfully ducked.  Monitor's job is done.
                return

    @property
    def is_monitor_running(self) -> bool:
        """``True`` if the smart-duck background monitor is currently running.

        Useful for diagnostics and tests.
        """
        return (
            self._monitor_thread is not None
            and self._monitor_thread.is_alive()
        )

    @property
    def backend_name(self) -> str:
        """Human-readable name of the active backend, or ``'disabled'``."""
        if self._backend is None:
            return "disabled"
        return self._backend.name

    @property
    def is_available(self) -> bool:
        """``True`` if a backend is initialised and ready."""
        return self._ready

    @property
    def supports_per_session(self) -> bool:
        """``True`` if the active backend supports per-session ducking."""
        if self._backend is None:
            return False
        return self._backend.supports_per_session
