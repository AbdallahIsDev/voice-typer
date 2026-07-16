"""RW-9 regression tests for the ``StartupSequence`` extraction.

``VoiceTyperApp._do_startup`` is now a 1-line delegate that constructs a
``StartupSequence`` and calls ``.run()``. The full boot orchestration
lives in ``voice_typer/server/startup_sequence.py``.

These tests pin the contract of the extraction:

1. The expected sub-methods are invoked in the expected order
   (autostart → prewarm + mic enumeration in parallel → hotkey → model
   load).
2. ``_shutting_down`` short-circuits the sequence (RACE-020) at each
   major gate.
3. ``_do_startup`` delegates to ``StartupSequence(self).run()``.

The tests construct a ``VoiceTyperApp`` with mocked hardware/GUI deps
(mirrors the ``app`` fixture in ``tests/test_app.py``) and monkeypatch
``startup_tasks`` + ``app.hotkeys`` + ``app.models`` to record calls,
then call ``app._do_startup()`` and assert the recorded call order.

Pure-refactor safety: behaviour must be IDENTICAL to the pre-extraction
``VoiceTyperApp._do_startup`` (~340 lines). These tests would have
passed before the extraction too — they exist to catch regressions if
the StartupSequence body is edited in a way that reorders phases or
drops a RACE-020 gate.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def app_for_startup(tmp_config_dir, monkeypatch):
    """Create a VoiceTyperApp suitable for exercising ``_do_startup``.

    Heavier than the regular ``app`` fixture in test_app.py because we
    need the real Config/Tray/Recorder/Models/Hotkeys present so the
    StartupSequence can read/write them, but we still mock the
    hardware-touching platform helpers (is_autostart_enabled,
    enable/disable_autostart, list_microphones) and the
    hardware/IO-bound startup_tasks functions.
    """
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    from voice_typer.server.app import VoiceTyperApp

    instance = VoiceTyperApp()
    instance.config.esc_cancel_enabled = False
    instance.config.voice_biometric_consent = True
    instance.models.transcriber = MagicMock()
    instance.models.transcriber.is_loaded = True
    instance.models._sync_registry_from_fields()
    return instance


class TestStartupSequenceDelegate:
    """Verify ``VoiceTyperApp._do_startup`` delegates to ``StartupSequence.run``."""

    def test_do_startup_invokes_startup_sequence_run(self, app_for_startup, monkeypatch):
        """``_do_startup`` must construct a StartupSequence and call .run().

        RW-9 Phase 5 contract: the body of ``_do_startup`` is now a
        1-line delegate. The actual logic lives in
        ``StartupSequence(self).run()`` — verified by patching
        ``StartupSequence.run`` to a sentinel and asserting it was called.
        """
        from voice_typer.server import startup_sequence

        called_with: list = []

        def _fake_run(self_seq) -> None:
            called_with.append(self_seq._app)

        monkeypatch.setattr(startup_sequence.StartupSequence, "run", _fake_run)

        # Configure_corrections would normally load corrections.json; stub it.
        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )

        app_for_startup._do_startup()

        assert len(called_with) == 1, "StartupSequence.run() must be called exactly once from _do_startup"
        assert called_with[0] is app_for_startup, (
            "StartupSequence must be constructed with the app as its back-reference"
        )


class TestStartupSequenceRunOrder:
    """Pin the boot phase ordering — reordering risks documented in
    startup_sequence.py module docstring (hotkey-before-model,
    mic-before-hotkey, onboarding-before-save)."""

    def test_run_calls_autostart_sync_then_prewarm_mic_then_hotkey_then_model(self, app_for_startup, monkeypatch):
        """The expected order is:

            1. startup_tasks.sync_autostart(app)
            2. startup_tasks.ensure_desktop_shortcut(app)
            3. (parallel) startup_tasks.sync_prewarm_task + load_microphones
            4. app.hotkeys.register()
            5. app.models.start_background_load()

        Each numbered phase must complete before the next begins.
        """
        from voice_typer.server import startup_tasks

        call_order: list[str] = []

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
            lambda config_dir: None,
        )
        monkeypatch.setattr(startup_tasks, "sync_autostart", lambda app: call_order.append("sync_autostart"))
        monkeypatch.setattr(
            startup_tasks,
            "sync_prewarm_task",
            lambda app, evt=None: call_order.append("sync_prewarm_task"),
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

        # ── Assert the expected ordering ─────────────────────────────
        # Autostart must come BEFORE hotkey registration (so F2 works
        # even if hotkey backend init fails) and BEFORE model load
        # (so the user can quit cleanly while the model downloads).
        assert "sync_autostart" in call_order, "autostart sync must run"
        assert "hotkeys.register" in call_order, "hotkey registration must run"
        assert "models.start_background_load" in call_order, "model load must start"

        autostart_idx = call_order.index("sync_autostart")
        hotkey_idx = call_order.index("hotkeys.register")
        model_idx = call_order.index("models.start_background_load")

        assert autostart_idx < hotkey_idx, (
            "RACE-020 / phase ordering: autostart sync MUST precede hotkey "
            f"registration (got autostart@{autostart_idx}, hotkey@{hotkey_idx})"
        )
        assert hotkey_idx < model_idx, (
            "Phase ordering: hotkey registration MUST precede background model "
            f"load (got hotkey@{hotkey_idx}, model@{model_idx}). Rationale: "
            "F2 must work even if the model fails to load."
        )

        # Prewarm + mic enumeration must run between autostart and hotkey
        # (so the tray menu has mics available when the hotkey is bound).
        assert "sync_prewarm_task" in call_order, "prewarm task sync must run"
        assert "load_microphones" in call_order, "mic enumeration must run"
        prewarm_idx = call_order.index("sync_prewarm_task")
        mic_idx = call_order.index("load_microphones")
        assert autostart_idx < prewarm_idx, "Phase ordering: autostart sync MUST precede prewarm sync"
        assert autostart_idx < mic_idx, "Phase ordering: autostart sync MUST precede mic enumeration"
        assert prewarm_idx < hotkey_idx, "Phase ordering: prewarm sync MUST precede hotkey registration"
        assert mic_idx < hotkey_idx, "Phase ordering: mic enumeration MUST precede hotkey registration"


class TestStartupSequenceRACE020ShutdownGates:
    """RACE-020: ``app._shutting_down`` is checked between each major step
    so a ``quit()`` during startup short-circuits cleanly. The
    StartupSequence must NOT proceed with model downloads or background
    loads after the app has begun shutdown."""

    def test_run_returns_early_if_shutting_down_at_start(self, app_for_startup, monkeypatch):
        """If _shutting_down is True at the very start, NO phases run.

        This is the strongest RACE-020 invariant — a quit() that landed
        before startup even began must leave all subsystems untouched.
        """
        from voice_typer.server import startup_tasks

        call_count = {"sync_autostart": 0, "hotkeys.register": 0, "models.start_background_load": 0}

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
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

        # Set shutting_down BEFORE run() — quit() landed during the
        # tray.start(bg_work=_do_startup) handoff, before the bg thread
        # actually started running _do_startup.
        app_for_startup._shutting_down = True

        from voice_typer.server.startup_sequence import StartupSequence

        StartupSequence(app_for_startup).run()

        assert call_count == {"sync_autostart": 0, "hotkeys.register": 0, "models.start_background_load": 0}, (
            "RACE-020: if _shutting_down is set at startup start, NO phases "
            "must execute (model download, hotkey registration, autostart sync)."
        )

    def test_run_aborts_after_autostart_sync_if_shutting_down(self, app_for_startup, monkeypatch):
        """If _shutting_down becomes True after the autostart sync step,
        the sequence must short-circuit BEFORE hotkey registration and
        model load.

        This guards the RACE-020 gate documented in startup_sequence.py
        at the "Interrupted after autostart sync" log line.
        """
        from voice_typer.server import startup_tasks

        call_count = {"hotkeys.register": 0, "models.start_background_load": 0}

        def _sync_autostart_set_shutting_down(app):
            # Simulate quit() landing during the autostart sync step.
            app._shutting_down = True

        monkeypatch.setattr(
            "voice_typer.server.startup_sequence.configure_corrections",
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
    """StartupSequence.run() must not raise on common test-environment
    degradations (no sounddevice, no torch, etc.). Each subsystem's
    failure is logged and the sequence continues — startup resilience
    is a hard product requirement (see test_app.py:TestStartupResilience)."""

    def test_run_swallows_onboarding_exceptions(self, app_for_startup, monkeypatch):
        """An exception in the onboarding auto-heal block must NOT abort
        the rest of startup (RACE-020 / startup resilience).

        The onboarding block in startup_sequence.py wraps its body in
        try/except and increments ``app._onboarding_fail_count`` — after
        3 failures it marks onboarding complete with a failure flag.
        """
        from voice_typer.server import startup_sequence as ss_mod
        from voice_typer.server import startup_tasks
        from voice_typer.server.onboarding import OnboardingController

        # Force is_first_run() to raise — simulates a permissions error
        # reading the onboarding marker file.
        def _exploding_is_first_run(self):
            raise OSError("permission denied")

        monkeypatch.setattr(OnboardingController, "is_first_run", _exploding_is_first_run)
        # Bypass config-file existence check so the genuine-first-run
        # branch is taken (which would otherwise save the config and
        # NOT raise).
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

        # Must NOT raise — onboarding failure is logged and the
        # sequence proceeds to hotkey registration + model load.
        ss_mod.StartupSequence(app_for_startup).run()

        # Sanity: hotkey + model load DID run (startup was not aborted).
        app_for_startup.hotkeys.register.assert_called_once()
        app_for_startup.models.start_background_load.assert_called_once()
