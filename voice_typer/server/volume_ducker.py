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
from collections.abc import Callable

from voice_typer.server._audio_constants import _DEFAULT_SMART_DUCK_POLL_MS
from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
from voice_typer.server.volume_backend_base import VolumeBackend, VolumeState
from voice_typer.server.volume_ducker_monitor import SmartDuckMonitorMixin

log = logging.getLogger(__name__)

# If the current volume differs from the ducked level by more than this
# fraction, we assume the user manually changed it during dictation.
_MANUAL_OVERRIDE_THRESHOLD = 0.05


# Default polling interval for the smart-duck background monitor.
# 500ms is a balance: fast enough to catch audio within half a second,
# slow enough to not spam the backend (macOS osascript is 200-500ms
# per call; Windows IAudioMeterInformation is ~0ms; Linux pactl is
# ~50ms).  Override via set_smart_duck_poll_interval().
#
# ``VolumeDucker.initialize()`` further clamps this down to
# ``backend.recommended_poll_interval_ms`` when the backend advertises
# a faster safe cadence (e.g. macOS CoreAudio path → 100ms, since the
# in-process ``kAudioDevicePropertyDeviceIsRunning`` query is <1ms).
# The user's explicit ``set_smart_duck_poll_interval()`` value is
# respected if it is *faster* than the backend's recommendation.
class VolumeDucker(SmartDuckMonitorMixin):
    """Manages system audio volume ducking during dictation.

    Platform-agnostic: delegates to a :class:`VolumeBackend` selected by
    ``platform.get_volume_backend()``.

    The smart-duck background monitor (``_start_smart_duck_monitor``,
    ``_stop_smart_duck_monitor``, ``_smart_duck_monitor_loop``,
    ``is_monitor_running``) is contributed by :class:`SmartDuckMonitorMixin`
    in ``voice_typer/server/volume_ducker_monitor.py``. Extracted to keep
    this module under the 800-line ceiling. The mixin owns NO state of
    its own — all instance attributes are initialised by
    :meth:`VolumeDucker.__init__` below.
    """

    def __init__(
        self,
        backend: VolumeBackend | None = None,
        crash_recovery: DuckCrashRecovery | None = None,
        on_crash_restore: Callable[[VolumeState], None] | None = None,
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
        self._backend: VolumeBackend | None = backend
        self._crash_recovery = crash_recovery
        self._on_crash_restore = on_crash_restore
        self._saved_state: VolumeState | None = None
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
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop: threading.Event = threading.Event()
        self._lock = threading.Lock()
        self._initialized: bool = False
        self._ready: bool = False  # True only if initialize() succeeded

    # ── Lifecycle ───────────────────────────────────────────────────

    def _clamp_poll_interval(self, value: int) -> int:
        """apply backend ``min_poll_interval_ms`` floor on every
        ``set_smart_duck_poll_interval`` call, not just inside
        :meth:`initialize`.

         introduced the floor to stop subprocess backends (Linux
        ``pactl``, macOS ``osascript``) from burning 10–20 % CPU per
        core when smart-duck polls faster than the backend can service.
        Originally the floor lived only inside ``initialize()``, which
        no-ops after the first successful call — so the 2nd and later
        dictations (whose ``VolumeController._duck_volume`` path calls
        ``set_smart_duck_poll_interval`` BEFORE the now-no-op
        ``initialize``) silently bypassed the floor.  Centralising the
        clamp here ensures the floor is re-applied on every set.

        When no backend is bound yet (the production ducker is created
        with ``backend=None`` and auto-detects inside ``initialize``),
        the helper is a pass-through — the floor is applied later by
        ``initialize``.  This preserves the first-dictation behaviour
        the production code path relies on.

        Defensive ``int(...) or 0`` coercion: duck-typed test fakes and
        ``MagicMock`` backends may return non-numeric values from
        ``min_poll_interval_ms``; coerce so a missing attribute doesn't
        leak a non-numeric value into the comparison.
        """
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
        otherwise (unsupported platform / missing library → graceful
        no-op).  Safe to call multiple times.
        """
        if self._initialized:
            return self._backend is not None

        if self._backend is None:
            from voice_typer.server.server_platform import get_volume_backend

            self._backend = get_volume_backend()

        if self._backend is None:
            log.info("[VOLUME] No volume backend available — ducking disabled")
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
        # *floor* on polling speed — the monitor never polls *slower*
        # than the backend recommends, but the user can always go
        # faster via ``config.volume_duck_smart_poll_interval_ms``.
        # This lets the macOS CoreAudio path (in-process, <1ms) cut
        # the default 500ms poll down to 100ms, while leaving the
        # slower osascript / pactl paths at their conservative default.
        #
        # ``getattr`` is used (rather than direct attribute access) so
        # duck-typed test fakes that don't extend ``VolumeBackend`` —
        # and therefore don't inherit the property — fall back to the
        # conservative 500ms default rather than raising
        # ``AttributeError``.
        recommended = getattr(self._backend, "recommended_poll_interval_ms", 500)
        if recommended < self._smart_duck_poll_ms:
            log.info(
                "[VOLUME] Backend %s recommends %dms poll interval (was %dms) — adopting",
                self._backend.name,
                recommended,
                self._smart_duck_poll_ms,
            )
            self._smart_duck_poll_ms = recommended

        # subprocess backends (Linux pactl, macOS osascript) spawn
        # an expensive subprocess per ``is_speaker_active()`` call.  At
        # the default 500ms cadence, Linux pactl burns 10–20% CPU on one
        # core just for smart-duck.  The backend's ``min_poll_interval_ms``
        # advertises the *slowest* cadence the monitor should adopt; we
        # use ``max(user_value, min_poll)`` so the monitor never polls
        # *faster* than the backend can handle.  Users who explicitly
        # configure a slower value are still honoured.
        #
        # the clamp is applied via the shared
        # ``_clamp_poll_interval`` helper so that
        # ``set_smart_duck_poll_interval`` (called on every dictation
        # start by ``VolumeController._duck_volume``) re-applies the
        # same floor — preventing the 2nd-and-later-dictation bypass
        # that previously reset the cadence to the unclamped user
        # value (500 ms) on Linux and burned 10–20 % CPU per core.
        clamped = self._clamp_poll_interval(self._smart_duck_poll_ms)
        if clamped > self._smart_duck_poll_ms:
            log.info(
                "[VOLUME] Backend %s requires %dms minimum poll interval "
                "(was %dms) — adopting to avoid subprocess CPU waste",
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

        # CPU-02 (c-review): macOS osascript polling wastes CPU.
        # ``MacVolumeBackend`` falls back to spawning an ``osascript``
        # subprocess (200–500 ms latency per call) when pyobjc-
        # framework-CoreAudio isn't installed. With smart-duck enabled,
        # the background monitor polls ``is_speaker_active()`` every
        # 500 ms — each poll spawns osascript → 40–100% CPU on one
        # core just for smart-duck, plus repeated AppleScript
        # permission prompts on macOS 13+.
        #
        # The fix: if the active backend is osascript (not CoreAudio),
        # disable smart-duck entirely. The duck still applies
        # immediately on dictation start; only the "skip duck if no
        # audio is playing" optimization is lost. The user can
        # re-enable smart-duck by installing pyobjc-framework-CoreAudio
        # (``pip install pyobjc-framework-CoreAudio``).
        #
        # We check ``backend.name`` rather than ``isinstance`` so this
        # is duck-typed and works with any backend that advertises an
        # osascript-like slow subprocess path (defensive for future
        # backends). The string check is case-insensitive and matches
        # the value returned by ``MacVolumeBackend.name`` (see
        # volume_backends.py:351: ``"CoreAudio (pyobjc)" if
        # self._use_coreaudio else "osascript"``).
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
            # running from a previous dictation) is also stopped.
            self.set_smart_duck_enabled(False)

        # Crash recovery: restore stale state from a previous crash.
        if self._crash_recovery is not None:
            stale = self._crash_recovery.load_stale()
            if stale is not None:
                log.warning(
                    "[VOLUME] Previous session crashed while ducked — restoring volume to %.0f%% (muted=%s)",
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

        # ``backend.fade_to()`` can block for up to 150 ms
        # (10 steps x 15 ms sleep on in-process backends; a single
        # subprocess call on Linux/macOS).  Holding ``self._lock``
        # during the fade serialises ``restore()`` (ESC cancel) behind
        # the fade -- visible as a 150 ms "ESC doesn't respond" delay.
        # We mirror the pattern in
        # ``level_monitor/worker._process_level_chunk``: snapshot the
        # shared state under the lock (quick), release the lock for
        # the heavy fade, re-acquire for the post-fade state writes
        # (re-checking invariants because ``restore()`` may have run
        # while we were fading).  ``restore()`` itself still holds the
        # lock for its own fade (out of scope for ) -- but the
        # common path (duck in progress, user hits ESC) now lets
        # ``restore()`` start fading back immediately instead of
        # waiting for ``duck()``'s fade to finish.
        with self._lock:
            if self._saved_state is None:
                # First duck -- save current state.
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
                # applies the duck -- closing the gap where speaker bleed
                # could leak into the mic.  See _smart_duck_monitor_loop.
                #  (fix): null-check _backend before calling
                if self._smart_duck_enabled and self._backend is not None and not self._backend.is_speaker_active():
                    self._actually_ducked = False
                    log.info("[VOLUME] No audio output — duck skipped (smart duck, monitor started)")
                    self._start_smart_duck_monitor(level, fade_ms, per_session)
                    return True

                # snapshot the fade parameters so we can
                # drop the lock for the actual ``backend.fade_to()``
                # call below.  ``saved_state`` is captured for the
                # pre-fade crash-recovery write;
                # ``use_per_session`` gates the ``duck_other_sessions``
                # fallback path.
                saved_state = state
                target_level = level
                target_fade_ms = fade_ms
                use_per_session = per_session and self._backend.supports_per_session
                backend_ref = self._backend
                is_first_duck = True

                # Persist the crash-recovery file BEFORE the
                # fade begins (still under ``self._lock`` so
                # ``_saved_state`` and the file stay consistent).  The
                # fade can block for up to 150 ms; if the process
                # crashes mid-fade the volume is partially ducked but,
                # without this write, no recovery file would exist for
                # the next launch — leaving the speakers stuck at the
                # ducked level.  Saving before the fade guarantees the
                # file exists for the entire duration of the fade.
                # If the fade subsequently fails, ``restore()`` will
                # clear the file on the next clean shutdown; if the
                # process exits cleanly, ``restore()`` clears it too.
                # The smart-duck skip path above returns before this
                # point, so no spurious file is written when the
                # volume is never actually changed.
                if self._crash_recovery is not None:
                    self._crash_recovery.save(saved_state)
            else:
                # Already ducked -- update level without re-saving.
                #
                # BUGFIX (v1.1): if smart-duck skipped the first duck
                # (_actually_ducked=False), we must NOT call fade_to
                # here -- doing so would fade the user's volume down to
                # the new duck level with no saved state to restore
                # from.  Instead, just update the logical ducked_level
                # so a later restore() (if audio has since started)
                # knows the target.  The user's volume is unchanged.
                #
                # v2.3: if the smart-duck monitor is running, it will
                # pick up the new _ducked_level on its next poll -- no
                # need to restart it.
                self._ducked_level = level
                if not self._actually_ducked:
                    log.info(
                        "[VOLUME] Duck level updated -> %.0f%% (smart-duck still skipping — no fade)",
                        level * 100,
                    )
                    return True
                # snapshot for unlocked fade (see comment above).
                saved_state = None  # not used on the already-ducked path
                target_level = level
                target_fade_ms = fade_ms
                use_per_session = False  # per-session only attempted on first duck
                backend_ref = self._backend
                is_first_duck = False

        # -- Heavy fade OUTSIDE the lock () --
        # ``backend.fade_to()`` may block for up to 150 ms.  Holding
        # ``self._lock`` here would block ``restore()`` (ESC cancel)
        # for the fade duration.  The backend's ``fade_to`` is
        # thread-safe at the backend level (Windows COM is
        # apartment-threaded; Linux/macOS spawn independent
        # subprocesses), so concurrent fades from ``restore()`` race
        # on the backend but do not corrupt ``VolumeDucker`` state --
        # the post-fade re-check below handles the
        # ``restore()``-ran-during-fade case.
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
                # during the fade, clearing ``_saved_state`` and
                # fading the volume back to the saved level.  If so,
                # we must NOT mark ``_actually_ducked = True`` (that
                # would leave the ducker in an inconsistent state
                # where ``is_ducked`` is False but ``_actually_ducked``
                # is True).  ``restore()`` also cleared the
                # crash-recovery file (it was saved pre-fade),
                # so there's nothing to recover from — the volume was
                # already restored.
                if self._saved_state is None:
                    log.info("[VOLUME] restore() ran during duck fade — skipping state update")
                    return ok
                self._actually_ducked = True
                # Crash-recovery file was saved BEFORE the fade
                # (under the first lock acquisition).  No save here —
                # re-saving would race with a concurrent ``restore()``
                # that may have just cleared the file.
                log.info(
                    "[VOLUME] Duck -> %.0f%% (saved %.0f%%, muted=%s, per_session=%s)",
                    target_level * 100,
                    saved_state.linear * 100,
                    saved_state.muted,
                    use_per_session,
                )
                return ok
            # Already-ducked path: ``_ducked_level`` was updated
            # before the fade (under the first lock acquisition), so
            # the smart-duck monitor picks up the new level on its
            # next poll.  Re-check whether ``restore()`` ran during
            # the fade -- if so, skip the "level updated" log (the
            # user-facing state is "restored", not "ducked at new
            # level").
            if self._saved_state is None:
                log.info("[VOLUME] restore() ran during level-update fade — skipping state update")
                return ok
            log.info("[VOLUME] Duck level updated -> %.0f%%", target_level * 100)
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

        # stop the smart-duck background monitor INSIDE the lock.
        # v2.3 originally signalled the monitor BEFORE acquiring
        # ``self._lock`` so the monitor thread could acquire the lock
        # to check ``_saved_state`` and exit cleanly.  But that
        # ordering is racy: ``duck()`` (which holds the lock and calls
        # ``_start_smart_duck_monitor()`` inside the lock) could run
        # concurrently with ``restore()``'s unlocked
        # ``_stop_smart_duck_monitor()``.  Sequence:
        #
        #   1. Thread A (restore) reads ``_monitor_thread`` = T1
        #      (without the lock).
        #   2. Thread A sets ``_monitor_stop``.
        #   3. Thread B (concurrent duck) acquires the lock, calls
        #      ``_start_smart_duck_monitor()``.  It reads
        #      ``_monitor_thread`` — if Thread A hasn't nulled it yet,
        #      it sees T1 (alive) and returns WITHOUT starting a fresh
        #      monitor (``_start_smart_duck_monitor``'s early-exit when
        #      a monitor is already running).  Thread A then nulls
        #      ``_monitor_thread``; T1 exits on its next poll.  The new
        #      dictation has NO monitor running — no retroactive-duck
        #      protection.
        #
        # Calling ``_stop_smart_duck_monitor()`` from inside the lock
        # makes the stop + ``_saved_state``-clear atomic with respect
        # to ``duck()``'s start.  ``_stop_smart_duck_monitor()`` is
        # non-blocking (: signals the Event, clears
        # ``_monitor_thread``, returns without joining), so holding the
        # lock for the call does not introduce a deadlock — the monitor
        # thread either sees the Event via ``wait()`` and exits without
        # the lock, or acquires the lock after ``restore()`` releases
        # it and exits via the ``_saved_state is None`` re-check.
        with self._lock:
            self._stop_smart_duck_monitor()

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

        the backend's ``min_poll_interval_ms`` floor is
        re-applied on EVERY call via :meth:`_clamp_poll_interval`.
        This is the core  fix: previously the floor was applied
        only inside :meth:`initialize` (which no-ops after the first
        dictation), so the 2nd-and-later dictations silently bypassed
        the floor — causing 10–20 % CPU waste on Linux ``pactl`` /
        macOS ``osascript`` for the duration of every subsequent
        dictation.
        """
        self._smart_duck_poll_ms = self._clamp_poll_interval(max(50, min(5000, int(ms))))

    # Smart-duck background monitor methods live in
    # SmartDuckMonitorMixin (voice_typer/server/volume_ducker_monitor.py).
    # _start_smart_duck_monitor / _stop_smart_duck_monitor /
    # _smart_duck_monitor_loop / is_monitor_running resolve via MRO.

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
