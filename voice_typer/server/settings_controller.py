"""RW-9 god-class decomposition: SettingsController — extracted from VoiceTyperApp.

Owns the platform-level configuration side effects triggered by the tray
menu and IPC:

    - autostart (enable/disable + toggle from tray menu)
    - notifications (show/hide tray notifications)
    - microphone selection (update config + recreate ``Recorder``)

The actual logic lived on ``VoiceTyperApp`` as four private methods
(``_toggle_autostart``, ``_set_autostart``, ``_set_notifications``,
``_select_microphone``). The behaviour is preserved verbatim — only the
class boundary moved. ``VoiceTyperApp`` keeps thin delegate methods so
the tray menu callbacks (and tests that call ``app._select_microphone``
directly) keep working unchanged.

Not extracted in this round:

    - ``_open_config_file``: stays on ``VoiceTyperApp`` because
      ``tests/test_b4_config_editor_lock.py`` and
      ``tests/test_bugfix_regressions.py:943`` use
      ``inspect.getsource(VoiceTyperApp._open_config_file)`` to pin
      source-level invariants (macOS ``open -W`` branch, three platform
      branches acquiring ``_config_mutation_lock``, etc.). Moving it
      would require rewriting those source-inspection tests, which
      expands scope and risk; left for a follow-up round.

A note on monkeypatching (mirrors the convention in
``startup_tasks.py``): tests like the ``app`` fixture in
``tests/test_app.py`` replace
``voice_typer.server.app.is_autostart_enabled`` /
``enable_autostart`` / ``disable_autostart`` at call time. To keep
those patches effective, the platform-helper names are looked up
DYNAMICALLY from the ``voice_typer.server.app`` module inside each
method rather than being captured at import time.
"""

from __future__ import annotations

import logging
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.recording import Recorder

log = logging.getLogger(__name__)


class SettingsController:
    """Owns tray-menu + IPC-driven settings side effects.

    RW-9 Phase 6: extracted from ``VoiceTyperApp``. The app passes itself
    (``app``) so ``SettingsController`` can:
    - Read/write ``app.config`` (autostart, show_notifications, microphone)
    - Call ``app.config.save()`` to persist changes
    - Call ``app.tray.set_autostart_enabled`` / ``set_notifications_enabled``
      / ``notify`` to update the tray UI
    - Recreate ``app.recorder`` (a ``Recorder`` instance) when the mic
      changes mid-session — see ``select_microphone`` for the
      during-recording deferral.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    # ── Autostart ──────────────────────────────────────────────────────

    def toggle_autostart(self) -> None:
        """Toggle autostart on/off from the tray menu.

        Delegates to :meth:`set_autostart` (P2 dedup).
        """
        # Look up the platform helper from the app module at call time so
        # tests that monkeypatch voice_typer.server.app.is_autostart_enabled
        # still take effect.
        from voice_typer.server import app as _app_module

        self.set_autostart(not _app_module.is_autostart_enabled())

    def set_autostart(self, enabled: bool) -> None:
        """Set autostart from the advanced settings window or tray toggle.

        Persists the change to disk and updates the tray UI. On failure,
        notifies the user via the tray (best-effort).

        G4-H-15: wrapped in ``_config_mutation_lock`` for atomicity
        w.r.t. IPC ``set_config`` mutations (RLock, re-entry safe).
        """
        # Look up the platform helpers from the app module at call time so
        # tests that monkeypatch voice_typer.server.app.{enable_autostart,
        # disable_autostart} still take effect.
        from voice_typer.server import app as _app_module

        app = self._app
        with app._config_mutation_lock:
            try:
                if enabled:
                    _app_module.enable_autostart()
                else:
                    _app_module.disable_autostart()
                app.config.autostart = enabled
                if not app.config.save():
                    log.warning("[CONFIG] Failed to save autostart setting to disk")
                app.tray.set_autostart_enabled(enabled)
                log.info("[CONFIG] Autostart set to %s", enabled)
            except Exception as e:
                log.exception("[CONFIG] Failed to set autostart")
                app.tray.notify(APP_NAME, f"Could not change autostart setting.\n{e}")

    # ── Notifications ──────────────────────────────────────────────────

    def set_notifications(self, enabled: bool) -> None:
        """Set notification behavior from the settings window.

        Persists ``show_notifications`` to disk and updates the tray UI
        (``tray.set_notifications_enabled`` gates all future ``notify()``
        calls so a toggle takes effect immediately).

        G4-H-15: wrapped in ``_config_mutation_lock`` for atomicity
        w.r.t. IPC ``set_config`` mutations (RLock, re-entry safe).
        """
        app = self._app
        with app._config_mutation_lock:
            app.config.show_notifications = enabled
            if not app.config.save():
                log.warning("[CONFIG] Failed to save notifications setting to disk")
            app.tray.set_notifications_enabled(enabled)
            log.info("[CONFIG] Notifications set to %s", enabled)

    # ── Microphone ────────────────────────────────────────────────────

    def select_microphone(self, mic_name: str | None) -> None:
        """Handle microphone selection from tray menu.

        Persists the change to disk. If a recording is in progress, the
        ``Recorder`` is NOT recreated immediately — the new mic takes
        effect on the next recording (recreating mid-stream would
        truncate the in-flight audio). Otherwise the ``Recorder`` is
        recreated with the new ``config.microphone`` so PortAudio opens
        the correct input device on the next ``start()``.

        G4-H-15: wrapped in ``_config_mutation_lock`` for atomicity
        w.r.t. IPC ``set_config`` mutations (RLock, re-entry safe).
        """
        app = self._app
        with app._config_mutation_lock:
            app.config.microphone = mic_name
            if not app.config.save():
                log.warning("[CONFIG] Failed to save microphone selection to disk")
                app.tray.notify(
                    APP_NAME,
                    "Failed to save microphone selection. Check disk space or permissions.",
                )
            label = mic_name if mic_name else "System Default"

            if app.recorder.recording:
                log.info("[CONFIG] Microphone changed to %s; applying after active recording", label)
                app.tray.notify(APP_NAME, f"Microphone next recording: {label}")
                return

            # Re-create with new mic. NOTE: this intentionally does NOT pass
            # ``thread_registry`` (mirrors the pre-refactor behaviour on
            # ``VoiceTyperApp._select_microphone``). The new ``Recorder``
            # inherits the global thread registry via its own default; the
            # original ``__init__``'s explicit ``thread_registry=`` is for the
            # primary instance only.
            app.recorder = Recorder(app.config, audio_processor=app._audio_processor)  # re-create with new mic
            log.info("[CONFIG] Microphone changed to: %s", label)
            app.tray.notify(APP_NAME, f"Microphone: {label}")
