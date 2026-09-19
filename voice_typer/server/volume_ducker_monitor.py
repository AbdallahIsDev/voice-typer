"""Smart-duck background monitor mixin."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


class SmartDuckMonitorMixin:
    """Smart-duck background monitor methods, extracted from"""

    def _start_smart_duck_monitor(self, level: float, fade_ms: int, per_session: bool) -> None:
        """Start the background speaker-activity monitor."""
        # If a monitor is already running, don't start a second one.
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        # Clear the stop event from any previous monitor run.
        self._monitor_stop.clear()
        # Create + start the thread BEFORE assigning to
        t = threading.Thread(
            target=self._smart_duck_monitor_loop,
            name="smart-duck-monitor",
            daemon=True,
            kwargs={"fade_ms": fade_ms, "per_session": per_session},
        )
        t.start()
        self._monitor_thread = t

    def _stop_smart_duck_monitor(self) -> None:
        """Signal the background monitor to stop WITHOUT joining."""
        # Capture the thread reference locally, another thread may
        thread = self._monitor_thread
        if thread is None:
            return
        # Signal the monitor to stop. The monitor's poll loop sleeps in
        self._monitor_stop.set()
        # clear _monitor_thread so is_monitor_running returns
        self._monitor_thread = None

    def _smart_duck_monitor_loop(self, fade_ms: int, per_session: bool) -> None:
        """Background thread: poll is_speaker_active() and retroactively duck."""
        while not self._monitor_stop.is_set():
            # Sleep first (we already checked is_speaker_active() in
            if self._monitor_stop.wait(self._smart_duck_poll_ms / 1000.0):
                return  # stop signal received

            # Check if we should still be running.  Another thread may
            with self._lock:
                if self._saved_state is None:
                    # restore() was called, exit.
                    return
                if self._actually_ducked:
                    # We already applied the duck (or duck() was called
                    return
                if not self._smart_duck_enabled:
                    # User disabled smart-duck mid-dictation.
                    return

            # Query the backend OUTSIDE the lock, is_speaker_active()
            try:
                #  (fix): explicit null check instead of type: ignore
                speaker_active = False if self._backend is None else self._backend.is_speaker_active()
            except Exception as exc:
                log.debug("[VOLUME] monitor: is_speaker_active failed: %s", exc)
                speaker_active = False  # don't duck on error, try again next poll

            if not speaker_active:
                continue  # still silent, keep polling

            # Audio started!  Apply the retroactive duck.
            with self._lock:
                if self._saved_state is None or self._actually_ducked:
                    # restore() ran, or another path already ducked.
                    return
                if not self._smart_duck_enabled:
                    return
                level = self._ducked_level
                state = self._saved_state

                log.info(
                    "[VOLUME] Audio started mid-dictation, retroactive duck -> %.0f%%",
                    level * 100,
                )
                # Persist crash-recovery BEFORE the fade (under
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
                    log.info("[VOLUME] restore() ran during retroactive-duck fade, skipping state update")
                    return
                if ok:
                    self._actually_ducked = True
                else:
                    log.warning("[VOLUME] retroactive duck failed, will retry next poll")
                    # The crash-recovery file was saved

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
