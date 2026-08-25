"""AppAdmin — mic/model/restart/settings management mixin extracted from
VoiceTyperApp.

Owns the management-side surface of ``VoiceTyperApp``:

    - Settings side-effect delegates (``_toggle_autostart``,
      ``_set_autostart``, ``_set_notifications``, ``_select_microphone``)
      → ``SettingsController`` (``self.settings``).
    - ``_open_config_file`` → ``ConfigEditorLauncher``
      (``self._config_editor_launcher``); holds ``_config_mutation_lock``
      for the FULL editor session (TOCTOU guard).
    - TrayController protocol methods for microphone / model management
      (``change_microphone``, ``active_microphone_id``,
      ``refresh_microphones``, ``change_model``).
    - Quit / restart lifecycle entry points (``quit_app``,
      ``restart_app``, ``_wait_for_relaunch_ack``) →
      ``LifecycleController`` (``self.lifecycle``). ``restart_app``
      keeps its re-entry guard inline (first executable statement) so
      the source-level invariant pinned by
      ``tests/test_app_cleanup.py::test_restart_app_guard_is_first_statement_in_method``
      keeps holding.

Previously all of this lived on ``VoiceTyperApp`` in ``app.py``. The
behaviour is preserved verbatim — only the class boundary moved.
``VoiceTyperApp(AppAdmin)`` inherits every method, so instance-level
monkeypatching and direct calls keep working unchanged, and
``inspect.getsource(VoiceTyperApp.restart_app)`` / ``quit_app`` keep
returning the same source text (getsource resolves through the MRO).

A note on logging (mirrors the convention in ``app_lifecycle.py``):
this module uses ``logging.getLogger("voice_typer.server.app")`` so
caplog captures route to the same logger as the original
VoiceTyperApp methods.
"""

from __future__ import annotations

import logging

# Tests capture restart_app / quit_app logs at this logger name — see
# module docstring.
log = logging.getLogger("voice_typer.server.app")


class AppAdmin:
    """Mic/model/restart/settings management mixin for ``VoiceTyperApp``.

    Declares NO ``__init__`` — construction order and the backing
    attributes stay entirely in ``app.py``; only the accessors live
    here.
    """

    # ─── Settings side-effects (autostart / notifications / mic) ───────

    def _toggle_autostart(self):
        """Toggle autostart on/off from the tray menu. Delegates to SettingsController."""
        self.settings.toggle_autostart()

    def _set_autostart(self, enabled: bool):
        """Set autostart from the advanced settings window or tray toggle.

        body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_autostart`.
        Behaviour preserved verbatim — only the class boundary moved.
        """
        self.settings.set_autostart(enabled)

    def _set_notifications(self, enabled: bool):
        """Set notification behavior from the settings window.

        body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.set_notifications`.
        """
        self.settings.set_notifications(enabled)

    def _select_microphone(self, mic_name: str | None):
        """Handle microphone selection from tray menu.

        body extracted to
        :meth:`voice_typer.server.settings_controller.SettingsController.select_microphone`.
        """
        self.settings.select_microphone(mic_name)

    def _open_config_file(self):
        """Open the config file in the user's default editor.

        body extracted to
        :class:`voice_typer.server.controllers.config_editor_launcher.ConfigEditorLauncher`.
        Behaviour preserved verbatim — only the class boundary moved.

        (security fix, restored): hold
        ``_config_mutation_lock`` for the FULL editor session — not just
        the save/reload phases — so a concurrent IPC ``set_config``
        cannot atomically clobber ``config.json`` mid-edit (TOCTOU
        race). The launcher's internal ``with self.app._config_mutation_lock:``
        blocks re-acquire the same RLock (reentrant — no-op while the
        outer hold is active), so the lock is held continuously from
        the pre-editor save through the editor wait through the
        post-editor reload. A concurrent ``set_config`` (which goes
        through ``ConfigApplier.apply_config`` → ``_config_mutation_lock``)
        blocks until the editor exits and the reload completes — exactly
        the invariant the regression test
        (``tests/regressions/test_concurrency.py::TestConfigEditHoldsMutationLock``)
        pins.

        The earlier "split-lock" relaxation released the lock
        during the editor wait to keep the tray / IPC responsive while
        the user edited. That trade-off downgraded the security
        invariant: a concurrent ``set_config`` could land its save on
        disk between the editor's open and the user's manual save,
        silently losing the user's edits on the subsequent reload.
        Restoring the full-session hold closes the TOCTOU window at
        the cost of blocking concurrent config mutations for the
        editor session (bounded by the launcher's 30-minute timeout —
        see ``config_editor._EDITOR_SESSION_TIMEOUT_SECONDS``). The
        IPC thread is NOT blocked because the editor launch runs on
        the tray / hotkey thread, not the IPC dispatch thread.
        """
        with self._config_mutation_lock:
            self._config_editor_launcher.open()

    # ─── TrayController Protocol Methods ───────────────────────────────

    def change_microphone(self, mic_id: str | None) -> None:
        """TrayController protocol: select microphone."""
        self._select_microphone(mic_id)

    @property
    def active_microphone_id(self) -> str | None:
        """TrayController protocol — return the currently selected
        microphone ID from ``config.microphone`` (None = system default)."""
        mic = getattr(self.config, "microphone", None)
        return str(mic) if mic else None

    def refresh_microphones(self) -> None:
        """TrayController protocol — re-enumerate microphones
        and refresh the tray menu by delegating to startup_tasks."""
        from voice_typer.server import startup_tasks
        from voice_typer.server.server_platform import invalidate_microphone_list_cache

        try:
            # An explicit user refresh must bypass the 5 s TTL cache —
            # serving cached records here would make the tray action a
            # no-op right after a device change.
            invalidate_microphone_list_cache()
            startup_tasks.load_microphones(self)
        except Exception:
            log.warning("[TRAY] refresh_microphones failed", exc_info=True)

    def change_model(self, model_size: str) -> None:
        """TrayController protocol: change transcription model.

        (pyrefly): parameter renamed from ``model`` to
        ``model_size`` to match :class:`voice_typer.server.providers.AppProtocol`'s
        ``change_model(self, model_size: str)`` signature. Pyrefly
        enforces parameter-name matching for Protocol members (a call
        like ``app.change_model(model_size="large")`` must be valid on
        any AppProtocol implementation), so the names must agree.

        the ``_change_model`` delegate has been removed;
        this method now calls ``self.models.change_model`` directly.
        """
        self.models.change_model(model_size)

    # the following 6 TrayController protocol methods were
    # removed because no IPC route, tray menu item, or UI invoked them:
    #   - toggle_autostart (use _toggle_autostart directly)
    #   - create_desktop_shortcut
    #   - set_notifications (use _set_notifications directly)
    #   - set_silence_warning_seconds (use set_config via IPC)
    #   - set_stop_on_silence_seconds (use set_config via IPC)
    #   - set_max_recording_time_seconds (use set_config via IPC)
    # The corresponding TrayController Protocol entries were also removed.

    # ─── Quit / Restart lifecycle entry points ─────────────────────────

    def quit_app(self) -> None:
        """TrayController protocol: quit the app.

        body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController.quit_app`.
        Behaviour preserved verbatim — only the class boundary moved.

        Preserved invariants (now in the controller):
        - cleanup runs via ``self.quit()`` (the audited
          ``SystemExit`` path) — never ``os._exit(0)``.
        - ``event_bus.publish({"type": "quit_app"})``
          runs BEFORE the ``if self._shutting_down:`` re-entry guard
          so a double-quit still pushes the event. (Historically the
          guard was the plain ``if self._shutting_down:`` form;
          migrated to the threading.Event version
          ``if self._shutting_down_event.is_set():`` for cross-thread
          memory ordering.)
        """
        return self.lifecycle.quit_app()

    def restart_app(self) -> None:
        """TrayController protocol: restart the app.

        body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController.restart_app`.

        Preserved invariants (now in the controller):
        - ``log.info("[RESTART] Restarting %s...", APP_NAME)``.
        - ``try:`` wraps ``self.config.save()`` so an unexpected
          raise (e.g. RecursionError from a cyclic dataclass) does not
          abort the restart — the ``except Exception:`` block logs
          ``log.warning("config.save() raised", exc_info=True)``.
        - the redundant ``_restore_volume(fade_ms=0)`` call
          was removed — ``_do_cleanup`` (now reached via
          ``self.lifecycle.restart_app`` -> ``self._do_cleanup``) handles
          the restore via the shared ShutdownController body.
        - ``self._thread_registry.shutdown_all()``
          -> ``self._do_cleanup()`` -> main-thread ``sys.exit(0)`` (or
          non-main-thread watchdog fallback).
        """
        #  re-entry guard (must be the first executable
        # statement — see
        # tests/test_app_cleanup.py::test_restart_app_guard_is_first_statement_in_method).
        # The rest of the body lives in LifecycleController.restart_app;
        # the controller mirrors this guard (idempotent) so it is safe
        # for direct calls from future code.
        if self._shutting_down_event.is_set():
            log.debug("[RESTART] ignoring duplicate restart_app call (already shutting down)")
            return
        return self.lifecycle.restart_app()

    def _wait_for_relaunch_ack(self, timeout: float) -> bool:
        """Delegate to LifecycleController.

        Body extracted to
        :meth:`voice_typer.server.app_lifecycle.LifecycleController._wait_for_relaunch_ack`.
        Behaviour preserved verbatim — only the class boundary moved.
        """
        return self.lifecycle._wait_for_relaunch_ack(timeout)
