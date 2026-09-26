"""VolumeDucker — system audio ducking during dictation."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from voice_typer.server._audio_constants import _DEFAULT_SMART_DUCK_POLL_MS
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_ducker_monitor import SmartDuckMonitorMixin

log = logging.getLogger(__name__)

# If the current volume differs from the ducked level by more than this
_MANUAL_OVERRIDE_THRESHOLD = 0.05


# Default duck target level (0.0-1.0 perceptual-linear). Single source
DEFAULT_DUCK_LEVEL: float = 0.20


# Default polling interval for the smart-duck background monitor.
class VolumeDucker(SmartDuckMonitorMixin):
    """Manages system audio volume ducking during dictation."""

    def __init__(
        self,
        backend: VolumeBackend | None = None,
        crash_recovery: DuckCrashRecovery | None = None,
        on_crash_restore: Callable[[VolumeState], None] | None = None,
    ) -> None:
        """Initialise the ducker."""
        self._backend: VolumeBackend | None = backend
        self._crash_recovery = crash_recovery
        self._on_crash_restore = on_crash_restore
        self._saved_state: VolumeState | None = None
        self._ducked_level: float = DEFAULT_DUCK_LEVEL
        self._actually_ducked: bool = False  # True if the volume was actually changed
        # Duck fades currently running OUTSIDE ``self._lock`` —
        self._duck_fades_in_flight: int = 0
        # Smart duck: when True (default), duck() first calls
        self._smart_duck_enabled: bool = True
        # Smart-duck background monitor: polls is_speaker_active()
        self._smart_duck_poll_ms: int = _DEFAULT_SMART_DUCK_POLL_MS
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop: threading.Event = threading.Event()
        self._lock = threading.Lock()
        self._initialized: bool = False
        self._ready: bool = False  # True only if initialize() succeeded

    def _clamp_poll_interval(self, value: int) -> int:
        """apply backend ``min_poll_interval_ms`` floor on every"""
        if self._backend is not None:
            try:
                min_poll = int(getattr(self._backend, "min_poll_interval_ms", 0) or 0)
            except (TypeError, ValueError):
                min_poll = 0
            if min_poll > value:
                return min_poll
        return value

    def initialize(self) -> bool:
        """Detect platform, set up the backend, and check for crash recovery.

        Returns ``True`` if a working backend is available, ``False``
        """
        if self._initialized:
            return self._backend is not None

        if self._backend is None:
            # Lazy import from the OWNING submodule (still resolved at
            from voice_typer.server.server_platform.volume_factory import get_volume_backend

            self._backend = get_volume_backend()

        if self._backend is None:
            log.info("[VOLUME] No volume backend available, ducking disabled")
            self._initialized = True
            self._ready = False
            return False

        ok = self._backend.initialize()
        self._initialized = True

        if not ok:
            log.warning("[VOLUME] Backend %s failed to initialise", self._backend.name)
            self._ready = False
            return False
        self._ready = True

        # apply the backend's recommended poll interval as a
        recommended = getattr(self._backend, "recommended_poll_interval_ms", 500)
        if recommended < self._smart_duck_poll_ms:
            log.info(
                "[VOLUME] Backend %s recommends %dms poll interval (was %dms), adopting",
                self._backend.name,
                recommended,
                self._smart_duck_poll_ms,
            )
            self._smart_duck_poll_ms = recommended

        # subprocess backends (Linux pactl, macOS osascript) spawn
        clamped = self._clamp_poll_interval(self._smart_duck_poll_ms)
        if clamped > self._smart_duck_poll_ms:
            log.info(
                "[VOLUME] Backend %s requires %dms minimum poll interval "
                "(was %dms), adopting to avoid subprocess CPU waste",
                self._backend.name,
                clamped,
                self._smart_duck_poll_ms,
            )
            self._smart_duck_poll_ms = clamped

        log.info(
            "[VOLUME] Backend ready: %s (per_session=%s)",
            self._backend.name,
            self._backend.supports_per_session,
        )

        # osascript-like slow subprocess path (defensive for future
        backend_name = str(getattr(self._backend, "name", "") or "").lower()
        if "osascript" in backend_name and self._smart_duck_enabled:
            log.warning(
                "[VOLUME] Smart-duck disabled: macOS osascript backend "
                "is active (pyobjc-framework-CoreAudio not installed). "
                "osascript polling takes 200-500ms per call and would "
                "consume 40-100%% CPU on one core. Install pyobjc-"
                "framework-CoreAudio to re-enable smart-duck: "
                "pip install pyobjc-framework-CoreAudio"
            )
            # Use the public setter so the smart-duck monitor (if
            self.set_smart_duck_enabled(False)

        # Crash recovery: restore stale state from a previous crash.
        if self._crash_recovery is not None:
            stale = self._crash_recovery.load_stale()
            if stale is not None:
                log.warning(
                    "[VOLUME] Previous session crashed while ducked, restoring volume to %.0f%% (muted=%s)",
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

    def duck(
        self,
        level: float = DEFAULT_DUCK_LEVEL,
        fade_ms: int = 150,
        per_session: bool = False,
    ) -> bool:
        """Reduce system volume to *level* (0.0–1.0 perceptual-linear)."""
        if not self._initialized or self._backend is None:
            return False

        level = max(0.0, min(1.0, level))

        # ``backend.fade_to()`` can block for up to 150 ms
        with self._lock:
            if self._saved_state is None:
                # First duck -- save current state.
                state = self._backend.get_state()
                if state is None:
                    log.warning("[VOLUME] get_state failed, not ducking")
                    return False
                self._saved_state = state
                self._ducked_level = level

                # Smart duck: skip if no application is currently
                if self._smart_duck_enabled and self._backend is not None and not self._backend.is_speaker_active():
                    self._actually_ducked = False
                    # DEBUG: the actual volume change (if any) is logged
                    log.debug("[VOLUME] No audio output, duck skipped (smart duck, monitor started)")
                    self._start_smart_duck_monitor(level, fade_ms, per_session)
                    return True

                # snapshot the fade parameters so we can
                saved_state = state
                target_level = level
                target_fade_ms = fade_ms
                use_per_session = per_session and self._backend.supports_per_session
                backend_ref = self._backend
                is_first_duck = True

                # Persist the crash-recovery file BEFORE the
                if self._crash_recovery is not None:
                    self._crash_recovery.save(saved_state)
                # Count the fade as in flight BEFORE releasing the lock
                self._duck_fades_in_flight += 1
            else:
                # Already ducked -- update level without re-saving.
                self._ducked_level = level
                if not self._actually_ducked:
                    log.info(
                        "[VOLUME] Duck level updated -> %.0f%% (smart-duck still skipping, no fade)",
                        level * 100,
                    )
                    return True
                # snapshot for unlocked fade (see comment above).
                saved_state = self._saved_state
                target_level = level
                target_fade_ms = fade_ms
                use_per_session = False  # per-session only attempted on first duck
                backend_ref = self._backend
                is_first_duck = False
                # Count the fade as in flight BEFORE releasing the lock
                self._duck_fades_in_flight += 1

        # -- Heavy fade + post-fade state writes (fade counted in flight) --
        try:
            # -- Heavy fade OUTSIDE the lock () --
            if backend_ref is None:  # defensive -- checked at entry, but snapshotted
                ok = False
            elif use_per_session:
                ok = backend_ref.duck_other_sessions(target_level)
                if not ok:
                    ok = backend_ref.fade_to(target_level, target_fade_ms)
            else:
                ok = backend_ref.fade_to(target_level, target_fade_ms)

            # -- Post-fade state writes UNDER the lock () --
            with self._lock:
                if is_first_duck:
                    # Re-check invariants: ``restore()`` may have run
                    if self._saved_state is None:
                        log.info("[VOLUME] restore() ran during duck fade, skipping state update")
                        # Our fade may have completed AFTER the restore's
                        self._repair_volume_after_interrupted_fade(
                            backend_ref, saved_state, restore_sessions=use_per_session
                        )
                        return ok
                    self._actually_ducked = True
                    # Crash-recovery file was saved BEFORE the fade
                    log.info(
                        "[VOLUME] Duck -> %.0f%% (saved %.0f%%, muted=%s, per_session=%s)",
                        target_level * 100,
                        saved_state.linear * 100,
                        saved_state.muted,
                        use_per_session,
                    )
                    return ok
                # Already-ducked path: ``_ducked_level`` was updated
                if self._saved_state is None:
                    log.info("[VOLUME] restore() ran during level-update fade, skipping state update")
                    self._repair_volume_after_interrupted_fade(backend_ref, saved_state)
                    return ok
                log.info("[VOLUME] Duck level updated -> %.0f%%", target_level * 100)
                return ok
        except Exception:
            # The backend call raised mid-fade: nothing owns the ducked
            with self._lock:
                self._saved_state = None
                self._actually_ducked = False
                if self._crash_recovery is not None:
                    self._crash_recovery.clear()
                self._repair_volume_after_interrupted_fade(backend_ref, saved_state, restore_sessions=use_per_session)
            raise
        finally:
            # One decrement per increment, on every exit path.
            with self._lock:
                self._duck_fades_in_flight -= 1

    def _repair_volume_after_interrupted_fade(
        self,
        backend: VolumeBackend | None,
        saved_state: VolumeState | None,
        *,
        restore_sessions: bool = False,
    ) -> None:
        """Best-effort return of the system volume to *saved_state* after a"""
        if backend is None or saved_state is None:
            return
        try:
            if restore_sessions and backend.supports_per_session:
                backend.restore_other_sessions()
            backend.set_linear(saved_state.linear, muted=saved_state.muted)
            log.info(
                "[VOLUME] Interrupted duck fade repaired, volume reset to %.0f%% (muted=%s)",
                saved_state.linear * 100,
                saved_state.muted,
            )
        except Exception:
            log.warning("[VOLUME] Failed to repair volume after interrupted duck fade", exc_info=True)

    def restore(
        self,
        fade_ms: int = 150,
        force: bool = False,
        per_session: bool = False,
    ) -> bool:
        """Restore system volume to its pre-duck level + mute state."""
        if not self._initialized or self._backend is None:
            return False

        # stop the smart-duck background monitor INSIDE the lock.
        with self._lock:
            self._stop_smart_duck_monitor()

            if self._saved_state is None:
                return True  # not ducked, no-op success

            if not self._actually_ducked:
                if self._duck_fades_in_flight > 0:
                    # A duck()'s fade is currently lowering the volume:
                    if per_session and self._backend.supports_per_session:
                        self._backend.restore_other_sessions()
                    target = self._saved_state
                    ok = self._backend.fade_to(target.linear, fade_ms)
                    if ok:
                        # Restore mute state AFTER the volume fade
                        self._backend.set_linear(target.linear, muted=target.muted)
                        if self._crash_recovery is not None:
                            self._crash_recovery.clear()
                    log.info(
                        "[VOLUME] Restore during duck fade -> %.0f%% (muted=%s)",
                        target.linear * 100,
                        target.muted,
                    )
                    self._saved_state = None
                    self._actually_ducked = False
                    return ok

                # Smart duck skipped the actual volume change because
                self._saved_state = None
                self._actually_ducked = False
                return True

            if per_session and self._backend.supports_per_session:
                self._backend.restore_other_sessions()

            current = self._backend.get_state()
            if current is None:
                log.warning("[VOLUME] get_state failed on restore, using saved value")
                target = self._saved_state
            elif self._duck_fades_in_flight > 0:
                # A duck()'s level-update fade is still in flight: the
                log.info("[VOLUME] Duck fade in flight on restore, using saved value (current reading is mid-fade)")
                target = self._saved_state
            elif not force and abs(current.linear - self._ducked_level) > _MANUAL_OVERRIDE_THRESHOLD:
                log.info(
                    "[VOLUME] Manual volume change detected during duck "
                    "(current=%.0f%%, ducked=%.0f%%), restoring to current "
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
                self._backend.set_linear(target.linear, muted=target.muted)
                if self._crash_recovery is not None:
                    self._crash_recovery.clear()

            log.info("[VOLUME] Restore -> %.0f%% (muted=%s)", target.linear * 100, target.muted)
            self._saved_state = None
            self._actually_ducked = False
            return ok

    @property
    def is_ducked(self) -> bool:
        """``True`` if volume is currently ducked (logically, may be a smart-duck skip)."""
        with self._lock:
            return self._saved_state is not None

    @property
    def actually_ducked(self) -> bool:
        """``True`` if the volume was actually changed (not a smart-duck skip)."""
        with self._lock:
            return self._actually_ducked

    @property
    def smart_duck_enabled(self) -> bool:
        """``True`` if smart-duck (skip when no audio playing) is enabled."""
        return self._smart_duck_enabled

    def set_smart_duck_enabled(self, enabled: bool) -> None:
        """Enable or disable smart-duck at runtime.

        Wired from ``config.volume_duck_smart`` by :class:`LausuApp`
        on startup and whenever the config changes.  Takes effect on the
        next ``duck()`` call, does not affect an in-progress duck.

        v2.3: if smart-duck is disabled mid-dictation while the monitor
        is running, the monitor is stopped.  The current smart-duck
        skip state is left as-is (we don't retroactively duck, the
        user explicitly disabled the feature).  The next ``duck()``
        call will use the new setting.
        """
        self._smart_duck_enabled = bool(enabled)
        if not enabled:
            # Stop the monitor, the user disabled smart-duck, so we
            self._stop_smart_duck_monitor()

    def set_smart_duck_poll_interval(self, ms: int) -> None:
        """Set the smart-duck monitor polling interval in milliseconds.

        Wired from ``config.volume_duck_smart_poll_interval_ms`` by
        :class:`LausuApp`.  Takes effect on the next monitor poll.
        Clamped to [50, 5000], below 50ms risks starving the audio
        callback on slow backends (macOS osascript); above 5000ms is
        too slow to catch short audio bursts.

        the backend's ``min_poll_interval_ms`` floor is
        re-applied on EVERY call via :meth:`_clamp_poll_interval`.
        This is the core  fix: previously the floor was applied
        only inside :meth:`initialize` (which no-ops after the first
        dictation), so the 2nd-and-later dictations silently bypassed
        the floor, causing 10–20 % CPU waste on Linux ``pactl`` /
        macOS ``osascript`` for the duration of every subsequent
        dictation.
        """
        self._smart_duck_poll_ms = self._clamp_poll_interval(max(50, min(5000, int(ms))))

    # Smart-duck background monitor methods live in

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
