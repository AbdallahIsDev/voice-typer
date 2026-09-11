"""god-class decomposition: VolumeController, extracted from VoiceTyperApp.

Owns the system-volume side effects of the dictation lifecycle:

    - ``_on_volume_crash_restore``: tray notification fired by
      :class:`VolumeDucker` when it discovers a stale
      ``duck_crash_recovery.json`` on startup (i.e. the previous session
      crashed while ducked).
    - ``_duck_volume``: reduce system volume at the start of dictation
      (smart-duck + master-volume duck).
    - ``_restore_volume``: restore system volume at the end of dictation
      (or on quit/restart, where ``fade_ms=0`` is passed for an instant
      restore).

The actual logic lived on ``VoiceTyperApp`` as three private methods (see
``docs/history/rw9-god-class-decomposition.md`` §5.3). The behaviour is preserved
verbatim, only the class boundary moved. ``VoiceTyperApp`` keeps thin
delegate methods so callers
(``RecordingController._start_dictation`` → ``app._duck_volume()``,
``VoiceTyperApp._do_cleanup`` → ``self._restore_volume(fade_ms=0)``,
``VoiceTyperApp.restart_app`` → ``self._restore_volume(fade_ms=0)``,
and the ``VolumeDucker`` crash-restore callback wired in ``__init__``)
keep working unchanged.

Dependencies (read-only, no state is owned here):
    - ``app._volume_ducker``: the :class:`VolumeDucker` instance.
    - ``app.config``: volume_duck_enabled / volume_duck_level /
      volume_duck_fade_ms / volume_duck_smart_poll_interval_ms.
    - ``app.tray``: :class:`TrayIcon` (only used for crash-restore
      notification).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from voice_typer.server.branding import APP_NAME

# import the canonical smart-duck poll-interval default and the
# canonical duck-level default. ``volume_ducker`` owns both constants
# (``_DEFAULT_SMART_DUCK_POLL_MS`` / ``DEFAULT_DUCK_LEVEL``) as the
# single source of truth, the level default matches the
# ``volume_duck_level`` config-schema default so no effective value
# drifts between the config layer and this fallback.
from voice_typer.server.volume_ducker import _DEFAULT_SMART_DUCK_POLL_MS, DEFAULT_DUCK_LEVEL

if TYPE_CHECKING:
    # TYPE_CHECKING-only import so the back-reference type-checks without
    # creating a circular import at runtime (``app.py`` imports this
    # module). Mirrors the convention used by ``settings_controller.py``.
    from voice_typer.server.app import VoiceTyperApp

log = logging.getLogger(__name__)


class VolumeController:
    """Owns duck/restore volume side effects for the dictation lifecycle.

    §5.3: extracted from ``VoiceTyperApp``. The app passes itself
        (``app``) so ``VolumeController`` can:
        - Call ``app._volume_ducker.{set_smart_duck_enabled,
          set_smart_duck_poll_interval, initialize, duck, restore}``
        - Read ``app.config.{volume_duck_enabled, volume_duck_level,
          volume_duck_fade_ms, volume_duck_smart_poll_interval_ms}``
        - Call ``app.tray.notify(APP_NAME, ...)`` for crash-restore
          notifications.

        The VolumeDucker is constructed in ``VoiceTyperApp.__init__`` (NOT
        here) because it owns hardware-backend lifecycle and crash-recovery
        file state, neither of which belong in this controller.
    """

    def __init__(self, app: VoiceTyperApp | Any) -> None:
        self._app = app

    # ─── Crash-restore callback ────────────────────────────────────────

    def _on_volume_crash_restore(self, state: Any) -> None:
        """Callback invoked when a stale duck crash-recovery file is found.

        Notifies the user that the volume was restored after a crash.
        """
        app = self._app
        try:
            app.tray.notify(
                APP_NAME,
                f"System volume was restored after a crash (to {int(state.linear * 100)}%).",
            )
        except Exception:
            log.debug("[VOLUME] crash-restore notification failed", exc_info=True)

    # ─── Duck / restore ────────────────────────────────────────────────

    def _duck_volume(self) -> None:
        """Duck system volume at the start of dictation.

        Config fields actually read here (all four exist in the config
        schema; the ``getattr`` defaults only apply when a config object
        lacks the attribute):

        - ``volume_duck_enabled``: early-return when False.
        - ``volume_duck_smart_poll_interval_ms``: smart-duck poll
          cadence; default ``_DEFAULT_SMART_DUCK_POLL_MS`` (500 ms),
          clamped up to the backend's ``min_poll_interval_ms`` floor by
          the ducker.
        - ``volume_duck_level``: duck target; default
          ``DEFAULT_DUCK_LEVEL`` (0.20, same value as the config schema
          default).
        - ``volume_duck_fade_ms``: fade ramp duration; default 200 ms
          (same value as the config schema default).

        Fixed behaviour (not configurable here):

        - Smart duck is ALWAYS ON when ducking is enabled (merged into
          Auto Duck Volume; the separate smart-duck toggle was removed).
        - Per-session ducking is removed, always master-volume duck,
          cross-platform.
        """
        app = self._app
        if not getattr(app.config, "volume_duck_enabled", True):
            return
        try:
            # smart duck is always on when ducking is enabled.
            app._volume_ducker.set_smart_duck_enabled(True)
            # poll interval comes from config (backend floors it).
            app._volume_ducker.set_smart_duck_poll_interval(
                getattr(app.config, "volume_duck_smart_poll_interval_ms", _DEFAULT_SMART_DUCK_POLL_MS)
            )
            if app._volume_ducker.initialize():
                app._volume_ducker.duck(
                    level=getattr(app.config, "volume_duck_level", DEFAULT_DUCK_LEVEL),
                    fade_ms=getattr(app.config, "volume_duck_fade_ms", 200),
                    # per-session removed, always master-volume duck.
                    per_session=False,
                )
        except Exception:
            log.debug("[VOLUME] duck failed", exc_info=True)

    def _restore_volume(self, fade_ms: int | None = None) -> None:
        """Restore system volume at the end of dictation.

        If ``fade_ms`` is ``None``, uses the configured fade duration
        (``volume_duck_fade_ms``; 200 ms when the attribute is absent).
        Pass ``0`` for instant restore (used on quit/restart).
        """
        app = self._app
        if not getattr(app.config, "volume_duck_enabled", True):
            return
        try:
            if fade_ms is None:
                fade_ms = getattr(app.config, "volume_duck_fade_ms", 200)
            app._volume_ducker.restore(
                fade_ms=fade_ms,
                # per-session removed, always master-volume restore.
                per_session=False,
            )
        except Exception:
            log.debug("[VOLUME] restore failed", exc_info=True)
