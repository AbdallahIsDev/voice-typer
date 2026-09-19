"""regression tests for the ``StartupSequence`` extraction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

_AUTOSTART = "voice_typer.server.server_platform.autostart"
_MIC_LIST = "voice_typer.server.server_platform.microphone_list"


@pytest.fixture
def app_for_startup(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp suitable for exercising ``_do_startup``."""
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_MIC_LIST}.list_microphones", lambda: [], raising=False)

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    return instance


class TestStartupSequenceDelegate:
    """Verify ``VoiceTyperApp._do_startup`` delegates to ``StartupSequence.run``."""

    def test_do_startup_invokes_startup_sequence_run(self, app_for_startup, monkeypatch):
        """``_do_startup`` must construct a StartupSequence and call .run()."""
        from voice_typer.server import startup_sequence

        called_with: list = []

        def _fake_run(self_seq) -> None:
            called_with.append(self_seq._app)

        monkeypatch.setattr(startup_sequence.StartupSequence, "run", _fake_run)

        # Configure_corrections would normally load corrections.json; stub it.
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        app_for_startup._do_startup()

        assert len(called_with) == 1, "StartupSequence.run() must be called exactly once from _do_startup"
        assert called_with[0] is app_for_startup, (
            "StartupSequence must be constructed with the app as its back-reference"
        )


class TestStartupSequenceRunOrder:
    """hotkey-before-mic (late mics recover via ``microphones_changed``),"""

    def test_run_dispatches_autostart_then_hotkey_then_model(self, app_for_startup, monkeypatch):
        """The expected startup contract is:"""
        import threading

        from voice_typer.server import startup_tasks

        call_order: list[str] = []

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )
        monkeypatch.setattr(
            startup_tasks,
            "sync_autostart",
            lambda app: call_order.append("sync_autostart") or {"registered": False, "error": None},
        )
        prewarm_calls: list = []
        monkeypatch.setattr(
            startup_tasks,
            "sync_prewarm_task",
            lambda app, evt=None: prewarm_calls.append(1),
        )
        monkeypatch.setattr(
            startup_tasks,
            "load_microphones",
            lambda app, evt=None: call_order.append("load_microphones"),
        )
        monkeypatch.setattr(
            startup_tasks,
            "ensure_desktop_shortcut",
            lambda app: call_order.append("ensure_desktop_shortcut"),
        )
        monkeypatch.setattr(
            startup_tasks,
            "start_accessibility_pulse",
            lambda app, initial_state: call_order.append("start_accessibility_pulse"),
        )

        autostart_threads: list[str] = []
        _orig_thread_start = threading.Thread.start

        def spy_start(self):
            autostart_threads.append(self.name)
            return _orig_thread_start(self)

        monkeypatch.setattr(threading.Thread, "start", spy_start)

        # Replace app.hotkeys + app.models with recorders.
        app_for_startup.hotkeys = MagicMock()
        app_for_startup.hotkeys.register = lambda: call_order.append("hotkeys.register")
        app_for_startup.models = MagicMock()
        app_for_startup.models.start_background_load = lambda: call_order.append("models.start_background_load")

        # Avoid actually opening the bubble on startup.
        app_for_startup.config.bubble_behavior = "hidden"
        app_for_startup.config.bubble_show_on_startup = False

        # Avoid restart-env branch.
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        assert prewarm_calls == [], "startup must never call sync_prewarm_task (no-op stub)"
        assert "startup-prewarm-sync" not in autostart_threads, (
            "the prewarm sync thread is deleted, no such thread may spawn"
        )
        # Autostart sync IS dispatched (fire-and-forget daemon thread).
        assert "startup-autostart-sync" in autostart_threads, (
            "sync_autostart must be dispatched on a 'startup-autostart-sync' thread"
        )
        assert "hotkeys.register" in call_order, "hotkey registration must run"
        assert "models.start_background_load" in call_order, "model load must start"

        hotkey_idx = call_order.index("hotkeys.register")
        model_idx = call_order.index("models.start_background_load")

        assert hotkey_idx < model_idx, (
            "Phase ordering: hotkey registration MUST precede background model "
            f"load (got hotkey@{hotkey_idx}, model@{model_idx}). Rationale: "
            "F2 must work even if the model fails to load."
        )

        # Mic enumeration runs AFTER the hotkey is bound: the mic task
        assert "load_microphones" in call_order, "mic enumeration must run"
        mic_idx = call_order.index("load_microphones")
        assert hotkey_idx < mic_idx, (
            "Phase ordering: hotkey registration MUST precede mic enumeration, "
            "the mic task's 5s budget must not delay the dictation hotkey (late "
            "mics recover via the microphones_changed push)."
        )

    def test_run_marks_session_active_after_crash_check(self, app_for_startup, monkeypatch, tmp_config_dir):
        """SESSION-STATE: once startup passes the crash-check phase, run()"""
        from voice_typer.server import session_state, startup_tasks

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        # Abort the sequence right after the first phase, which runs
        def _sync_autostart_abort(app):
            app._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", _sync_autostart_abort)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        marker = tmp_config_dir / "run" / session_state.SESSION_MARKER_FILENAME
        assert marker.exists(), (
            "SESSION-STATE: run() must mark the session active after the crash "
            "check so a later crash is detectable on the next launch"
        )

    def test_run_abort_before_marker_does_not_mark_session(self, app_for_startup, monkeypatch, tmp_config_dir):
        """crash check completes, run() aborts and must NOT write a fresh"""
        from voice_typer.server import session_state, startup_tasks

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)

        app_for_startup._shutting_down = True

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        marker = tmp_config_dir / "run" / session_state.SESSION_MARKER_FILENAME
        assert not marker.exists(), "aborted startup must not mark a session active"

    def _crash_check_app(self, app_for_startup, monkeypatch):
        """Configure the app so run() executes the crash-check phase then"""
        from voice_typer.server import startup_tasks

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )

        def _sync_autostart_abort(app):
            app._shutting_down = True

        monkeypatch.setattr(startup_tasks, "sync_autostart", _sync_autostart_abort)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)

        notified: list[tuple] = []
        events: list[dict] = []
        app_for_startup.tray.notify_safety = lambda *args: notified.append(args)
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: events.append(dict(msg)),
        )
        return notified, events

    def test_crash_file_plus_abnormal_session_notifies(self, app_for_startup, monkeypatch, tmp_config_dir):
        """SESSION-STATE gate (end-to-end): a real crash file + a survived"""
        from voice_typer.server import session_state
        from voice_typer.server.startup_sequence import StartupSequence

        notified, events = self._crash_check_app(app_for_startup, monkeypatch)
        # Real crash diagnostics file from the "previous" process.
        (tmp_config_dir / "crash_diagnostics.1234.txt").write_text(
            "code=0xC0000005 STATUS_ACCESS_VIOLATION: invalid memory access.\r\n",
            encoding="utf-8",
        )
        # Previous session did NOT shut down cleanly (marker survived).
        session_state.mark_session_active(tmp_config_dir)

        StartupSequence(app_for_startup).run()

        assert len(notified) == 1, f"expected 1 tray notification, got {notified}"
        title, body = notified[0]
        assert "Crashed" not in title, "title must not say 'Crashed'"
        assert "didn't close properly" in body
        assert "Settings" in body
        assert "python" not in body, "notification must not mention python/terminal commands"
        # The event-bus payload mirrors the tray copy + click-through.
        assert len(events) == 1
        assert events[0]["data"]["click_path"] == "/settings"
        assert "Access violation" not in body, "raw crash summary must stay out of the notification"

    def test_crash_file_plus_clean_session_suppressed(self, app_for_startup, monkeypatch, tmp_config_dir):
        """SESSION-STATE gate (end-to-end): the SAME crash file with NO"""
        from voice_typer.server import session_state
        from voice_typer.server.startup_sequence import StartupSequence

        notified, events = self._crash_check_app(app_for_startup, monkeypatch)
        # Real crash file, but NO session marker (clean shutdown recorded).
        (tmp_config_dir / "crash_diagnostics.1234.txt").write_text(
            "code=0xC0000005 STATUS_ACCESS_VIOLATION: invalid memory access.\r\n",
            encoding="utf-8",
        )
        assert not session_state.was_previous_session_abnormal(tmp_config_dir)

        StartupSequence(app_for_startup).run()

        assert notified == [], f"clean previous session must NOT notify: {notified}"
        assert events == [], f"clean previous session must NOT publish: {events}"
        # The crash file was still archived for support (no re-notification).
        assert not (tmp_config_dir / "crash_diagnostics.1234.txt").exists(), (
            "crash file should be archived (moved) even when suppressed"
        )


class TestStartupSequenceRACE020ShutdownGates:
    """
    RACE-020: ``app._shutting_down`` is checked between each major step
    StartupSequence must NOT proceed with model downloads or background
    """

    def test_run_returns_early_if_shutting_down_at_start(self, app_for_startup, monkeypatch):
        """
        If _shutting_down is True at the very start, NO phases run.
        This is the strongest RACE-020 invariant, a quit() that landed
        """
        from voice_typer.server import startup_tasks

        call_count = {"sync_autostart": 0, "hotkeys.register": 0, "models.start_background_load": 0}

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )
        monkeypatch.setattr(
            startup_tasks,
            "sync_autostart",
            lambda app: call_count.__setitem__("sync_autostart", call_count["sync_autostart"] + 1),
        )

        def _inc_call(key):
            call_count[key] += 1

        app_for_startup.hotkeys = MagicMock()
        app_for_startup.hotkeys.register = lambda: _inc_call("hotkeys.register")
        app_for_startup.models = MagicMock()
        app_for_startup.models.start_background_load = lambda: _inc_call("models.start_background_load")

        app_for_startup._shutting_down = True

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        assert call_count == {"sync_autostart": 0, "hotkeys.register": 0, "models.start_background_load": 0}, (
            "RACE-020: if _shutting_down is set at startup start, NO phases "
            "must execute (model download, hotkey registration, autostart sync)."
        )

    def test_run_aborts_after_autostart_sync_if_shutting_down(self, app_for_startup, monkeypatch):
        """
        If _shutting_down becomes True after the autostart sync step,
        This guards the RACE-020 gate documented in startup_sequence.py
        """
        from voice_typer.server import startup_tasks

        call_count = {"hotkeys.register": 0, "models.start_background_load": 0}

        def _sync_autostart_set_shutting_down(app):
            # Simulate quit() landing during the autostart sync step.
            app._shutting_down = True

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence._phases_early.configure_corrections",
            lambda config_dir: None,
        )
        monkeypatch.setattr(startup_tasks, "sync_autostart", _sync_autostart_set_shutting_down)
        # Stub the heavy IO tasks so they don't actually fire.
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)

        def _inc_call(key):
            call_count[key] += 1

        app_for_startup.hotkeys.register = lambda: _inc_call("hotkeys.register")
        app_for_startup.models = MagicMock()
        app_for_startup.models.start_background_load = lambda: _inc_call("models.start_background_load")

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        assert call_count == {"hotkeys.register": 0, "models.start_background_load": 0}, (
            "RACE-020: when _shutting_down is set after the autostart sync "
            "step, hotkey registration and model load MUST NOT proceed. "
            f"Got: {call_count}"
        )


class TestStartupSequenceDoesNotCrashOnMissingDeps:
    """StartupSequence.run() must not raise on common test-environment"""

    def test_run_swallows_onboarding_exceptions(self, app_for_startup, monkeypatch):
        """An exception in the onboarding auto-heal block must NOT abort"""
        from voice_typer.server import startup_sequence as ss_mod, startup_tasks
        from voice_typer.server.onboarding import OnboardingController

        # Force is_first_run() to raise, simulates a permissions error
        def _exploding_is_first_run(self):
            raise OSError("permission denied")

        monkeypatch.setattr(OnboardingController, "is_first_run", _exploding_is_first_run)
        # Bypass config-file existence check so the genuine-first-run
        monkeypatch.setattr(
            "pathlib.Path.exists",
            lambda self: False,
        )

        # Stub the rest of startup so we can isolate the onboarding block.
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)

        app_for_startup.hotkeys = MagicMock()
        app_for_startup.models = MagicMock()
        app_for_startup.config.bubble_behavior = "hidden"
        app_for_startup.config.bubble_show_on_startup = False
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

        ss_mod.StartupSequence(app_for_startup).run()

        # Sanity: hotkey + model load DID run (startup was not aborted).
        app_for_startup.hotkeys.register.assert_called_once()
        app_for_startup.models.start_background_load.assert_called_once()


class TestOnboardingStartedMarkerGate:
    """XZ-R12-01: ``startup_sequence.py``'s auto-heal logic must NOT"""

    def _stub_non_onboarding_startup(self, app_for_startup, monkeypatch):
        """Stub everything except the onboarding block so we can isolate it."""
        from voice_typer.server import startup_tasks

        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: None)
        monkeypatch.setattr(startup_tasks, "sync_prewarm_task", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "load_microphones", lambda app, evt=None: None)
        monkeypatch.setattr(startup_tasks, "ensure_desktop_shortcut", lambda app: None)
        monkeypatch.setattr(startup_tasks, "start_accessibility_pulse", lambda app, initial_state: None)

        app_for_startup.hotkeys = MagicMock()
        app_for_startup.models = MagicMock()
        app_for_startup.config.bubble_behavior = "hidden"
        app_for_startup.config.bubble_show_on_startup = False
        monkeypatch.delenv("VOICE_TYPER_RESTART", raising=False)

    def test_auto_heal_skipped_when_started_marker_present(self, app_for_startup, tmp_config_dir, monkeypatch):
        """If ``.onboarding_started`` exists, auto-heal MUST NOT fire."""
        from voice_typer.server.onboarding import OnboardingController
        from voice_typer.server.startup_sequence import (
            StartupSequence,
            _phases_early as ss_mod,
        )

        # Force the onboarding block to enter the auto-heal decision
        app_for_startup.config.onboarding_completed = False
        monkeypatch.setattr(OnboardingController, "is_first_run", lambda self: True)

        # Patch the module-level _config_dir binding in the OWNING
        monkeypatch.setattr(ss_mod, "_config_dir", lambda: tmp_config_dir)

        config_file = tmp_config_dir / "config.json"
        config_file.write_text('{"onboarding_completed": false}', encoding="utf-8")

        from voice_typer.server import onboarding_status

        onboarding_status.write_status(tmp_config_dir, started=True)

        # Spy on OnboardingController.mark_complete, auto-heal calls
        mark_complete_calls = []
        monkeypatch.setattr(
            OnboardingController,
            "mark_complete",
            lambda self: mark_complete_calls.append(1),
        )

        self._stub_non_onboarding_startup(app_for_startup, monkeypatch)

        StartupSequence(app_for_startup).run()

        # Auto-heal was SKIPPED, mark_complete was NOT called.
        assert mark_complete_calls == [], (
            "XZ-R12-01: auto-heal must NOT call mark_complete when "
            ".onboarding_started marker is present (wizard is in progress)"
        )
        assert app_for_startup.config.onboarding_completed is False, (
            "XZ-R12-01: auto-heal must NOT flip onboarding_completed when .onboarding_started marker is present"
        )

    def test_auto_heal_fires_when_started_marker_absent(self, app_for_startup, tmp_config_dir, monkeypatch):
        """If ``.onboarding_started`` does NOT exist (and config.json"""
        from voice_typer.server.onboarding import OnboardingController
        from voice_typer.server.startup_sequence import (
            StartupSequence,
            _phases_early as ss_mod,
        )

        app_for_startup.config.onboarding_completed = False
        monkeypatch.setattr(OnboardingController, "is_first_run", lambda self: True)
        monkeypatch.setattr(ss_mod, "_config_dir", lambda: tmp_config_dir)

        config_file = tmp_config_dir / "config.json"
        config_file.write_text('{"onboarding_completed": false}', encoding="utf-8")

        # .onboarding_started is NOT present.
        started_marker = tmp_config_dir / ".onboarding_started"
        assert not started_marker.exists()

        mark_complete_calls = []
        monkeypatch.setattr(
            OnboardingController,
            "mark_complete",
            lambda self: mark_complete_calls.append(1),
        )

        self._stub_non_onboarding_startup(app_for_startup, monkeypatch)

        StartupSequence(app_for_startup).run()

        # Auto-heal DID fire, mark_complete was called exactly once.
        assert len(mark_complete_calls) == 1, (
            "XZ-R12-01: auto-heal MUST call mark_complete when "
            ".onboarding_started marker is absent and config.json exists "
            "(stale-state recovery path)"
        )
        assert app_for_startup.config.onboarding_completed is True, (
            "XZ-R12-01: auto-heal must flip onboarding_completed to True "
            "in the stale-state recovery path (no .onboarding_started marker)"
        )

    def test_auto_heal_defers_when_config_json_absent(self, app_for_startup, tmp_config_dir, monkeypatch):
        """the wizard regardless of the ``.onboarding_started`` marker."""
        from voice_typer.server.onboarding import OnboardingController
        from voice_typer.server.startup_sequence import (
            StartupSequence,
            _phases_early as ss_mod,
        )

        app_for_startup.config.onboarding_completed = False
        monkeypatch.setattr(OnboardingController, "is_first_run", lambda self: True)
        monkeypatch.setattr(ss_mod, "_config_dir", lambda: tmp_config_dir)

        config_file = tmp_config_dir / "config.json"
        assert not config_file.exists()

        # .onboarding_started is also absent.
        started_marker = tmp_config_dir / ".onboarding_started"
        assert not started_marker.exists()

        mark_complete_calls = []
        monkeypatch.setattr(
            OnboardingController,
            "mark_complete",
            lambda self: mark_complete_calls.append(1),
        )

        self._stub_non_onboarding_startup(app_for_startup, monkeypatch)

        StartupSequence(app_for_startup).run()

        # Genuine first run, auto-heal does NOT fire.
        assert mark_complete_calls == [], (
            "XZ-R12-01: auto-heal must NOT fire on genuine first run (config.json absent), defer to the wizard"
        )
        assert app_for_startup.config.onboarding_completed is False, (
            "XZ-R12-01: onboarding_completed must stay False on genuine first run so the wizard renders"
        )
