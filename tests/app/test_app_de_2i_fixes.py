"""DE-2I (Group 4): targeted regression tests for four fixes in
``voice_typer/server/app.py``.

Each test class exercises one finding:

* ``DE-47`` — ``restart_app`` must not abort the restart sequence if
  ``self.config.save()`` raises an unexpected exception (e.g.
  ``RecursionError`` from ``asdict`` on a cyclic dataclass, or
  ``MemoryError`` during a huge credential_store migration).
* ``DE-48`` — ``VoiceTyperApp.__init__`` must not crash the entire
  backend if ``Config.load()`` propagates an unexpected exception
  (``KeyError`` / ``AttributeError`` / ``MemoryError`` — the
  deliberate "do not silently swallow" propagation in
  ``Config.load``).  We catch, log at ERROR with ``exc_info=True``,
  fall back to ``Config()`` defaults, and surface a tray
  notification once ``self.tray`` is built.
* ``DE-49`` — the re-entry guards in ``quit_app`` and
  ``restart_app`` must check ``self._shutting_down_event.is_set()``
  (the ``threading.Event`` version, which provides cross-thread
  memory-ordering) instead of the plain ``self._shutting_down``
  boolean.
* ``DE-50`` — ``app.main()`` must wrap the ``ipc_main()`` call in a
  top-level ``try/except Exception`` so a crash logs at ERROR with
  the full traceback and exits with code 1 (rather than propagating
  to the console-script wrapper with no structured log entry).

All heavy dependencies are mocked via the project-wide
``mock_heavy_imports`` autouse fixture (in ``tests/conftest.py``).
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import sys
from unittest.mock import MagicMock

import pytest

# ── Shared helpers ──────────────────────────────────────────────────────


def _stub_restart_environment(app, monkeypatch):
    """Stub out restart_app side effects so it runs in tests."""
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    monkeypatch.setattr("voice_typer.server.app.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "voice_typer.server.app.sys.exit",
        lambda code=0: (_ for _ in ()).throw(SystemExit(code)),
    )
    monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
    app.hotkeys._hotkey_backend = MagicMock()
    app.hotkeys._esc_backend = MagicMock()
    app.hotkeys._repaste_backend = MagicMock()
    app._cancel_pending_timers = MagicMock()
    app.tray = MagicMock()
    app.recorder = MagicMock()
    app.recorder.recording = False
    app.recording._transcription_thread = None
    app.recording.get_streaming_session = MagicMock(return_value=None)
    app.recording.set_streaming_session = MagicMock()


# ── DE-47: config.save() raising in restart_app ────────────────────────


class TestDE47ConfigSaveRaisesInRestartApp:
    """DE-47: ``self.config.save()`` in ``restart_app`` is wrapped in a
    ``try/except Exception`` so an unexpected raise cannot strand the
    user in a half-dead process (no relaunch event published, no
    exit).  The restart must continue regardless."""

    def test_restart_app_continues_when_config_save_raises(self, app, monkeypatch):
        """If ``config.save()`` raises (e.g. ``RecursionError`` from a
        cyclic dataclass, ``MemoryError`` during credential_store
        migration), ``restart_app`` must still push the
        ``relaunch_app`` event and proceed with the restart."""
        _stub_restart_environment(app, monkeypatch)

        publish_calls = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: publish_calls.append(msg),
        )
        # Force config.save() to raise an unexpected exception.
        monkeypatch.setattr(
            app.config,
            "save",
            lambda: (_ for _ in ()).throw(RecursionError("cyclic dataclass")),
        )

        with contextlib.suppress(SystemExit):
            app.restart_app()

        # The relaunch_app event MUST still be pushed despite the save
        # failure — otherwise the user's "Restart" tray click is a
        # silent no-op.
        assert any(
            msg.get("type") == "relaunch_app" for msg in publish_calls
        ), (
            "DE-47: restart_app must still publish the relaunch_app event "
            "even when config.save() raises; got pushes: " + repr(publish_calls)
        )

    def test_restart_app_logs_warning_when_config_save_raises(
        self, app, monkeypatch, caplog
    ):
        """The exception must be logged at WARNING with ``exc_info=True``
        so the stack trace lands in the user's log file for triage."""
        _stub_restart_environment(app, monkeypatch)

        monkeypatch.setattr(
            app.config,
            "save",
            lambda: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"), contextlib.suppress(SystemExit):
            app.restart_app()

        save_warning_records = [
            r for r in caplog.records
            if "config.save() raised" in r.message
        ]
        assert save_warning_records, (
            "DE-47: restart_app must log a WARNING containing "
            "'config.save() raised' when config.save() raises"
        )
        # exc_info=True must be set so the traceback is captured.
        assert save_warning_records[0].exc_info is not None, (
            "DE-47: the config.save() warning must include exc_info=True "
            "so the traceback lands in the log"
        )
        assert isinstance(save_warning_records[0].exc_info[1], RuntimeError), (
            "DE-47: the logged exception must be the RuntimeError from config.save()"
        )

    def test_restart_app_still_logs_failure_when_save_returns_false(
        self, app, monkeypatch, caplog
    ):
        """DE-47 must NOT regress the existing ``save() returns False``
        path (the documented ``OSError``/``PermissionError``/``TimeoutError``
        contract).  The original ``if not self.config.save():`` warning
        must still fire when save returns False."""
        _stub_restart_environment(app, monkeypatch)

        monkeypatch.setattr(app.config, "save", lambda: False)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.app"), contextlib.suppress(SystemExit):
            app.restart_app()

        false_warning_records = [
            r for r in caplog.records
            if "config.save() before push failed" in r.message
        ]
        assert false_warning_records, (
            "DE-47: the existing 'config.save() before push failed' WARNING "
            "must still fire when save() returns False (preserved contract)"
        )

    def test_source_has_try_except_around_config_save(self):
        """Source-level invariant: the ``self.config.save()`` call in
        ``restart_app`` must be wrapped in ``try:/except Exception:``."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        # Find the save call.
        save_idx = src.find("self.config.save()")
        assert save_idx != -1, "restart_app must call self.config.save()"
        # The try: keyword must appear before the save call.
        try_idx = src.rfind("try:", 0, save_idx)
        assert try_idx != -1, (
            "DE-47: self.config.save() in restart_app must be wrapped in a "
            "try: block (no 'try:' found before the save call)"
        )
        # The except Exception: clause must appear after the save call.
        except_idx = src.find("except Exception:", save_idx)
        assert except_idx != -1, (
            "DE-47: self.config.save() in restart_app must be followed by "
            "an 'except Exception:' clause that logs the failure"
        )
        # The except body must log at WARNING with 'config.save() raised'.
        except_block = src[except_idx:]
        assert "config.save() raised" in except_block, (
            "DE-47: the except block must log 'config.save() raised'"
        )
        assert "exc_info=True" in except_block, (
            "DE-47: the except block must pass exc_info=True to log.warning"
        )


# ── DE-48: Config.load() raising in __init__ ───────────────────────────


class TestDE48ConfigLoadRaisesInInit:
    """DE-48: ``VoiceTyperApp.__init__`` catches any ``Exception`` from
    ``Config.load()``, logs at ERROR with ``exc_info=True``, falls back
    to ``Config()`` defaults, and surfaces a tray notification."""

    def test_init_falls_back_to_defaults_when_config_load_raises(
        self, tmp_config_dir, monkeypatch
    ):
        """When ``Config.load()`` raises (e.g. ``KeyError`` from a
        ``data[...]`` access without a default — the deliberate
        propagation in Config.load), ``__init__`` must catch it and
        construct with ``Config()`` defaults so the rest of init can
        proceed."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config

        monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(app_module, "enable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "disable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "list_microphones", lambda: [])

        # Force Config.load to raise an unexpected exception.
        def _boom():
            raise KeyError("simulated bug in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        instance = app_module.VoiceTyperApp()

        # Config must be a default Config instance, NOT None.
        assert isinstance(instance.config, Config), (
            "DE-48: __init__ must fall back to Config() defaults when "
            "Config.load() raises; got: " + repr(instance.config)
        )
        # The flag must be set so the deferred tray notification fires.
        assert instance._config_load_failed is True, (
            "DE-48: __init__ must set _config_load_failed=True when "
            "Config.load() raises so the tray notification is deferred"
        )
        # Cleanup the instance to avoid resource leaks.
        with contextlib.suppress(Exception):
            instance._do_cleanup()

    def test_init_logs_error_with_exc_info_when_config_load_raises(
        self, tmp_config_dir, monkeypatch, caplog
    ):
        """The exception must be logged at ERROR with ``exc_info=True``."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config

        monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(app_module, "enable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "disable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "list_microphones", lambda: [])

        def _boom():
            raise AttributeError("simulated None deref in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.app"):
            instance = app_module.VoiceTyperApp()

        try:
            error_records = [
                r for r in caplog.records
                if "Config.load() raised" in r.message
            ]
            assert error_records, (
                "DE-48: __init__ must log an ERROR containing "
                "'Config.load() raised' when Config.load() raises"
            )
            assert error_records[0].levelno == logging.ERROR, (
                "DE-48: the Config.load failure must be logged at ERROR level "
                "(not WARNING/DEBUG) so it's visible in the default-INFO "
                "production log"
            )
            assert error_records[0].exc_info is not None, (
                "DE-48: the Config.load failure log must include exc_info=True "
                "so the traceback lands in the log for triage"
            )
            assert isinstance(
                error_records[0].exc_info[1], AttributeError
            ), "DE-48: the logged exception must be the AttributeError from Config.load"
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_init_surfaces_tray_notification_when_config_load_raises(
        self, tmp_config_dir, monkeypatch
    ):
        """After ``self.tray`` is built, ``__init__`` must call
        ``tray.notify`` with a user-facing message about the config
        load failure."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config

        monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(app_module, "enable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "disable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "list_microphones", lambda: [])

        def _boom():
            raise MemoryError("simulated OOM in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        # Spy on TrayIcon to capture the notify call.
        notify_calls = []
        original_init = app_module.TrayIcon.__init__

        def _spy_init(self, *a, **kw):
            original_init(self, *a, **kw)
            original_notify = self.notify

            def _spy_notify(title, message, *a2, **kw2):
                notify_calls.append((title, message))
                return original_notify(title, message, *a2, **kw2)

            self.notify = _spy_notify

        monkeypatch.setattr(app_module.TrayIcon, "__init__", _spy_init)

        instance = app_module.VoiceTyperApp()

        try:
            # The tray notification must have been called.
            assert notify_calls, (
                "DE-48: __init__ must call self.tray.notify when "
                "Config.load() raises (after the tray is built)"
            )
            # The message must mention the config load failure.
            titles_msgs = " ".join(f"{t} {m}" for t, m in notify_calls)
            assert "Config load failed" in titles_msgs, (
                "DE-48: the tray notification must mention 'Config load failed' "
                "so the user knows their settings were reset; got: " + repr(notify_calls)
            )
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_init_does_not_notify_when_config_load_succeeds(
        self, tmp_config_dir, monkeypatch
    ):
        """Sanity: when ``Config.load()`` succeeds, ``__init__`` must
        NOT call ``tray.notify`` for a config-load failure (the flag
        must be False and the notification branch skipped)."""
        from voice_typer.server import app as app_module

        monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(app_module, "enable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "disable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "list_microphones", lambda: [])

        notify_calls = []
        original_init = app_module.TrayIcon.__init__

        def _spy_init(self, *a, **kw):
            original_init(self, *a, **kw)
            original_notify = self.notify

            def _spy_notify(title, message, *a2, **kw2):
                notify_calls.append((title, message))
                return original_notify(title, message, *a2, **kw2)

            self.notify = _spy_notify

        monkeypatch.setattr(app_module.TrayIcon, "__init__", _spy_init)

        instance = app_module.VoiceTyperApp()

        try:
            assert instance._config_load_failed is False, (
                "DE-48: _config_load_failed must be False when Config.load() succeeds"
            )
            # No config-load-failure notification.
            config_fail_notifies = [
                (t, m) for t, m in notify_calls
                if "Config load failed" in (t or "") or "Config load failed" in (m or "")
            ]
            assert config_fail_notifies == [], (
                "DE-48: __init__ must NOT call tray.notify with a config-load "
                "failure message when Config.load() succeeds; got: "
                + repr(config_fail_notifies)
            )
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_init_tray_notify_failure_is_swallowed(
        self, tmp_config_dir, monkeypatch
    ):
        """If ``tray.notify`` itself raises (e.g. tray backend not
        fully initialized), ``__init__`` must NOT re-raise — the
        user already has the ERROR log line + traceback for triage."""
        from voice_typer.server import app as app_module
        from voice_typer.server.config import Config

        monkeypatch.setattr(app_module, "is_autostart_enabled", lambda: False)
        monkeypatch.setattr(app_module, "enable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "disable_autostart", lambda: True)
        monkeypatch.setattr(app_module, "list_microphones", lambda: [])

        def _boom():
            raise RuntimeError("simulated bug in Config.load")

        monkeypatch.setattr(Config, "load", classmethod(lambda cls: _boom()))

        # Make TrayIcon.notify raise.
        original_init = app_module.TrayIcon.__init__

        def _spy_init(self, *a, **kw):
            original_init(self, *a, **kw)
            self.notify = lambda *a, **kw: (_ for _ in ()).throw(
                OSError("tray backend not initialized")
            )

        monkeypatch.setattr(app_module.TrayIcon, "__init__", _spy_init)

        # Must not raise.
        instance = app_module.VoiceTyperApp()

        try:
            assert instance._config_load_failed is True
        finally:
            with contextlib.suppress(Exception):
                instance._do_cleanup()

    def test_source_has_try_except_around_config_load(self):
        """Source-level invariant: ``Config.load()`` in ``__init__``
        must be wrapped in ``try:/except Exception:`` with an
        ``ERROR``-level log and a ``Config()`` fallback."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.__init__)
        # Search for the actual call (``self.config = Config.load()``),
        # not the comment-text occurrences of ``Config.load()``.
        load_idx = src.find("self.config = Config.load()")
        assert load_idx != -1, "__init__ must assign self.config = Config.load()"
        try_idx = src.rfind("try:", 0, load_idx)
        assert try_idx != -1, (
            "DE-48: 'self.config = Config.load()' in __init__ must be wrapped "
            "in a try: block"
        )
        except_idx = src.find("except Exception:", load_idx)
        assert except_idx != -1, (
            "DE-48: 'self.config = Config.load()' in __init__ must be followed "
            "by an 'except Exception:' clause"
        )
        except_block = src[except_idx:]
        # Must log at ERROR.
        assert "log.error" in except_block, (
            "DE-48: the except block must use log.error (not log.warning or log.debug)"
        )
        # Must include exc_info=True.
        assert "exc_info=True" in except_block, (
            "DE-48: the except block must pass exc_info=True so the traceback is logged"
        )
        # Must fall back to Config() defaults.
        assert "Config()" in except_block, (
            "DE-48: the except block must fall back to Config() defaults"
        )


# ── DE-49: re-entry guard uses _shutting_down_event.is_set() ────────────


class TestDE49ReentryGuardUsesEventIsSet:
    """DE-49: the re-entry guards in ``quit_app`` and ``restart_app``
    check ``self._shutting_down_event.is_set()`` instead of the plain
    ``self._shutting_down`` boolean, for cross-thread memory-ordering."""

    def test_quit_app_guard_uses_event_is_set(self):
        """Source-level invariant: ``quit_app`` re-entry guard must
        use ``self._shutting_down_event.is_set():``, not the plain
        boolean form."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.quit_app)
        assert "if self._shutting_down_event.is_set():" in src, (
            "DE-49: quit_app must use 'if self._shutting_down_event.is_set():' "
            "as its re-entry guard"
        )

    def test_restart_app_guard_uses_event_is_set(self):
        """Source-level invariant: ``restart_app`` re-entry guard must
        use ``self._shutting_down_event.is_set():``, not the plain
        boolean form."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        assert "if self._shutting_down_event.is_set():" in src, (
            "DE-49: restart_app must use 'if self._shutting_down_event.is_set():' "
            "as its re-entry guard"
        )

    def test_quit_app_does_not_use_plain_boolean_guard(self):
        """The plain ``if self._shutting_down:`` form must NOT appear
        as a guard in ``quit_app`` (it's the buggy form that lacks
        cross-thread memory ordering)."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.quit_app)
        # The plain form (without _event.is_set) must not appear as
        # an executable guard.  We strip out comments/docstrings
        # loosely by checking that the only occurrence is inside the
        # docstring (which mentions the historical form).
        # Easiest invariant: search for the literal guard pattern
        # followed by a colon AND a newline.
        assert "if self._shutting_down:\n" not in src, (
            "DE-49: quit_app must NOT use the plain 'if self._shutting_down:' "
            "guard (use 'if self._shutting_down_event.is_set():' instead for "
            "cross-thread memory ordering)"
        )

    def test_restart_app_does_not_use_plain_boolean_guard(self):
        """The plain ``if self._shutting_down:`` form must NOT appear
        as a guard in ``restart_app``."""
        from voice_typer.server.app import VoiceTyperApp

        src = inspect.getsource(VoiceTyperApp.restart_app)
        assert "if self._shutting_down:\n" not in src, (
            "DE-49: restart_app must NOT use the plain "
            "'if self._shutting_down:' guard (use "
            "'if self._shutting_down_event.is_set():' instead)"
        )

    def test_quit_app_skips_quit_when_event_set(self, app, monkeypatch):
        """Behavioral: when ``_shutting_down_event`` is set (and the
        boolean is also True — quit() sets both), ``quit_app`` must
        skip the duplicate ``self.quit()`` call.  The push must still
        happen (APP-10 invariant)."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))

        # DE-49: set the Event, not (only) the boolean.
        app._shutting_down_event.set()
        # Also set the boolean to mirror what quit() does in production
        # — both are set together so a test that sets only the Event
        # is sufficient, but mirroring production is cleaner.
        app._shutting_down = True

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [], (
            "DE-49: quit_app must skip self.quit() when "
            "_shutting_down_event.is_set() is True"
        )

    def test_quit_app_calls_quit_when_event_not_set(self, app, monkeypatch):
        """Behavioral: when ``_shutting_down_event`` is NOT set,
        ``quit_app`` must call ``self.quit()`` (the normal path)."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)

        # Sanity: event must be clear by default.
        assert not app._shutting_down_event.is_set()
        # Edge case: the boolean may be True from a prior test in this
        # session (some tests set it directly).  Clear it to mirror a
        # fresh app.
        app._shutting_down = False

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [True], (
            "DE-49: quit_app must call self.quit() when "
            "_shutting_down_event is not set"
        )

    def test_restart_app_skips_when_event_set(self, app, monkeypatch):
        """Behavioral: when ``_shutting_down_event`` is set,
        ``restart_app`` must short-circuit (no push, no save, no
        cleanup)."""
        _stub_restart_environment(app, monkeypatch)

        publish_calls = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: publish_calls.append(msg),
        )
        save_calls = []
        monkeypatch.setattr(
            app.config, "save", lambda: save_calls.append(True) or True
        )
        cleanup_calls = []
        original_do_cleanup = app._do_cleanup
        monkeypatch.setattr(
            app, "_do_cleanup", lambda: cleanup_calls.append(True) or original_do_cleanup()
        )

        # DE-49: set the Event (not just the boolean).
        app._shutting_down_event.set()
        app._shutting_down = True

        app.restart_app()

        assert publish_calls == [], (
            "DE-49: restart_app must NOT push events when "
            "_shutting_down_event.is_set() is True"
        )
        assert save_calls == [], (
            "DE-49: restart_app must NOT call config.save() when "
            "_shutting_down_event.is_set() is True"
        )
        assert cleanup_calls == [], (
            "DE-49: restart_app must NOT call _do_cleanup() when "
            "_shutting_down_event.is_set() is True"
        )

    def test_quit_app_guard_does_not_fire_on_boolean_only(self, app, monkeypatch):
        """DE-49 regression guard: setting ONLY the plain boolean
        ``_shutting_down = True`` (without setting the Event) must
        NOT short-circuit ``quit_app``'s guard — because the guard
        now reads the Event, not the boolean.

        This test pins the new behavior: a refactor that sets only
        the boolean (e.g. a future contributor copying the pre-DE-49
        pattern) will not accidentally trigger the re-entry guard.
        The production code in ``quit()`` / ``restart_app()`` sets
        BOTH the boolean and the Event, so production is unaffected;
        this test guards against the boolean-only anti-pattern in
        test scaffolding and any future code that touches the flag
        directly.
        """
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        quit_calls = []
        monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
        monkeypatch.setattr("voice_typer.server.app.os._exit", lambda code: None)

        # Set ONLY the boolean — NOT the Event.  Pre-DE-49 this would
        # have short-circuited the guard; post-DE-49 it must NOT.
        app._shutting_down = True
        app._shutting_down_event.clear()

        app.quit_app()

        assert pushed == [{"type": "quit_app"}]
        assert quit_calls == [True], (
            "DE-49: setting only the boolean _shutting_down=True (without "
            "setting the Event) must NOT short-circuit quit_app's guard. "
            "The guard reads _shutting_down_event.is_set(), which is False "
            "here — so self.quit() must still be called."
        )


# ── DE-50: main() wraps ipc_main() in try/except ───────────────────────


class TestDE50MainWrapsIpcMain:
    """DE-50: ``app.main()`` wraps ``ipc_main()`` in a top-level
    ``try/except Exception`` so a backend crash logs at ERROR with
    the full traceback and exits with code 1."""

    def test_main_logs_and_exits_when_ipc_main_raises(self, monkeypatch, caplog):
        """When ``ipc_main()`` raises an unexpected exception,
        ``app.main()`` must log it at ERROR with the full traceback
        and call ``sys.exit(1)``."""
        from voice_typer.server import app as app_module

        # Make faulthandler.enable() a no-op so the test is hermetic.
        monkeypatch.setitem(sys.modules, "faulthandler", MagicMock(enable=lambda: None))

        # Make ipc_main raise an unexpected exception.
        def _boom():
            raise RuntimeError("simulated backend crash")

        # Patch the ipc_server.main symbol BEFORE app.main imports it.
        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", _boom)

        exit_calls = []
        monkeypatch.setattr(
            app_module.sys, "exit", lambda code=0: exit_calls.append(code)
        )

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.app"):
            app_module.main()

        # Must have called sys.exit(1).
        assert exit_calls == [1], (
            "DE-50: app.main must call sys.exit(1) when ipc_main raises; "
            "got exit calls: " + repr(exit_calls)
        )
        # Must have logged at ERROR with the FATAL prefix.
        fatal_records = [
            r for r in caplog.records if "[FATAL] backend crashed" in r.message
        ]
        assert fatal_records, (
            "DE-50: app.main must log an ERROR containing "
            "'[FATAL] backend crashed' when ipc_main raises"
        )
        # log.exception captures exc_info automatically.
        assert fatal_records[0].exc_info is not None, (
            "DE-50: the FATAL log must include exc_info (log.exception "
            "captures the traceback automatically)"
        )
        assert isinstance(
            fatal_records[0].exc_info[1], RuntimeError
        ), "DE-50: the logged exception must be the RuntimeError from ipc_main"

    def test_main_does_not_swallow_system_exit(self, monkeypatch):
        """``SystemExit`` (raised by ``sys.exit(0)`` inside ``quit()``
        / ``restart_app()``) must propagate unchanged — it's the
        normal shutdown signal and must NOT be caught by the
        ``except Exception:`` (since ``SystemExit`` inherits from
        ``BaseException``, not ``Exception``)."""
        from voice_typer.server import app as app_module

        # Make faulthandler.enable() a no-op so the test is hermetic.
        monkeypatch.setitem(sys.modules, "faulthandler", MagicMock(enable=lambda: None))

        # Make ipc_main raise SystemExit(0) (the intentional exit).
        def _raise_system_exit():
            raise SystemExit(0)

        import voice_typer.server.ipc_server as ipc_server_module

        monkeypatch.setattr(ipc_server_module, "main", _raise_system_exit)

        # sys.exit must NOT be called by the except branch (SystemExit
        # is not an Exception subclass, so it propagates).
        exit_calls = []
        monkeypatch.setattr(
            app_module.sys, "exit", lambda code=0: exit_calls.append(code)
        )

        # SystemExit must propagate out of main().
        with pytest.raises(SystemExit) as exc_info:
            app_module.main()

        assert exc_info.value.code == 0, (
            "DE-50: SystemExit(0) from ipc_main must propagate with code 0 "
            "(not be caught and re-exited as 1)"
        )
        assert exit_calls == [], (
            "DE-50: app.main must NOT call sys.exit when ipc_main raises "
            "SystemExit (the normal shutdown path); got exit calls: "
            + repr(exit_calls)
        )

    def test_source_has_try_except_around_ipc_main(self):
        """Source-level invariant: the ``ipc_main()`` call in
        ``app.main()`` must be wrapped in ``try:/except Exception:``
        with ``log.exception('[FATAL] backend crashed')`` and
        ``sys.exit(1)``."""
        from voice_typer.server.app import main

        src = inspect.getsource(main)
        ipc_idx = src.find("ipc_main()")
        assert ipc_idx != -1, "main() must call ipc_main()"
        try_idx = src.rfind("try:", 0, ipc_idx)
        assert try_idx != -1, (
            "DE-50: ipc_main() in main() must be wrapped in a try: block"
        )
        except_idx = src.find("except Exception:", ipc_idx)
        assert except_idx != -1, (
            "DE-50: ipc_main() in main() must be followed by an "
            "'except Exception:' clause"
        )
        except_block = src[except_idx:]
        assert "log.exception" in except_block, (
            "DE-50: the except block must use log.exception (which captures "
            "exc_info automatically)"
        )
        assert "[FATAL] backend crashed" in except_block, (
            "DE-50: the except block must log '[FATAL] backend crashed'"
        )
        assert "sys.exit(1)" in except_block, (
            "DE-50: the except block must call sys.exit(1) so the host "
            "sees a deterministic non-zero status"
        )
