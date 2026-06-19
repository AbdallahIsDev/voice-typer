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

                if per_session and self._backend.supports_per_session:
                    ok = self._backend.duck_other_sessions(level)
                    if not ok:
                        ok = self._backend.fade_to(level, fade_ms)
                else:
                    ok = self._backend.fade_to(level, fade_ms)

                if ok and self._crash_recovery is not None:
                    self._crash_recovery.save(state)

                log.info(
                    "[VOLUME] Duck → %.0f%% (saved %.0f%%, muted=%s, per_session=%s)",
                    level * 100,
                    state.linear * 100,
                    state.muted,
                    per_session,
                )
                return ok
            else:
                # Already ducked — update level without re-saving.
                self._ducked_level = level
                ok = self._backend.fade_to(level, fade_ms)
                log.info("[VOLUME] Duck level updated → %.0f%%", level * 100)
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

        with self._lock:
            if self._saved_state is None:
                return True  # not ducked — no-op success

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

            log.info("[VOLUME] Restore → %.0f%% (muted=%s)", target.linear * 100, target.muted)
            self._saved_state = None
            return ok

    # ── Introspection ───────────────────────────────────────────────

    @property
    def is_ducked(self) -> bool:
        """``True`` if volume is currently ducked."""
        with self._lock:
            return self._saved_state is not None

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
