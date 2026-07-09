"""Tests for voice_typer.onboarding — OnboardingController wizard."""

import json
import sys
import threading
import pytest
from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture
def onboarding_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def ctrl(onboarding_dir):
    """Create an OnboardingController with temp dir."""
    from voice_typer.server.onboarding import OnboardingController
    return OnboardingController(config_dir=onboarding_dir)


class TestOnboardingFirstRun:
    def test_is_first_run_no_config(self, ctrl):
        assert ctrl.is_first_run() is True

    def test_not_first_run_with_config(self, onboarding_dir):
        """#8: A config.json with onboarding_completed=True is NOT first run.

        Previously this test used an empty ``{}`` config which would have
        onboarding_completed=False (default), so is_first_run() now returns
        True. The wizard should appear whenever onboarding_completed is
        False, regardless of whether config.json exists.
        """
        (onboarding_dir / "config.json").write_text(
            json.dumps({"onboarding_completed": True}), encoding="utf-8"
        )
        from voice_typer.server.onboarding import OnboardingController
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is False

    def test_first_run_when_config_has_onboarding_false(self, onboarding_dir):
        """#8: config.json exists but onboarding_completed=False → first run.

        This is the case after app.py saves defaults on the very first
        launch. The wizard should still appear so the user can pick
        their microphone, hotkey, and model.
        """
        (onboarding_dir / "config.json").write_text(
            json.dumps({"onboarding_completed": False}), encoding="utf-8"
        )
        from voice_typer.server.onboarding import OnboardingController
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True

    def test_not_first_run_after_mark_complete(self, ctrl):
        ctrl.mark_complete()
        assert ctrl.is_first_run() is False


class TestOnboardingSteps:
    def test_initial_step(self, ctrl):
        assert ctrl.current_step == 0
        assert ctrl.total_steps == 5

    def test_step_names(self, ctrl):
        names = ["Welcome", "Microphone", "Hotkey", "Model", "Done"]
        for i, name in enumerate(names):
            ctrl._current_step = i
            assert ctrl.step_name == name

    def test_next_step(self, ctrl):
        assert ctrl.next_step() == 1
        assert ctrl.current_step == 1

    def test_next_step_capped(self, ctrl):
        ctrl._current_step = 4
        assert ctrl.next_step() == 4  # Already at last step

    def test_prev_step(self, ctrl):
        ctrl._current_step = 2
        assert ctrl.prev_step() == 1

    def test_prev_step_capped(self, ctrl):
        ctrl._current_step = 0
        assert ctrl.prev_step() == 0

    def test_step_change_advances_step(self, ctrl):
        """NEW-DEAD-033: callbacks were removed; verify step still advances."""
        assert ctrl.next_step() == 1
        assert ctrl._current_step == 1

    def test_complete_on_last_step(self, ctrl):
        """NEW-DEAD-033: callbacks were removed; verify completion still
        marks the onboarding as done."""
        ctrl._current_step = 3
        ctrl.next_step()  # Step 4 = last step
        assert ctrl.is_first_run() is False


class TestOnboardingSkip:
    def test_skip_marks_complete(self, ctrl):
        """NEW-DEAD-033: callbacks were removed; verify skip still
        marks the onboarding as complete."""
        ctrl.skip()
        assert ctrl.is_first_run() is False


class TestOnboardingSelections:
    def test_set_microphone(self, ctrl):
        ctrl.set_microphone("mic-1")
        assert ctrl.selected_microphone == "mic-1"

    def test_set_microphone_none(self, ctrl):
        ctrl.set_microphone(None)
        assert ctrl.selected_microphone is None

    def test_set_hotkey(self, ctrl):
        ctrl.set_hotkey("<f4>")
        assert ctrl.selected_hotkey == "<f4>"

    def test_hotkey_presets(self, ctrl):
        assert len(ctrl.HOTKEY_PRESETS) == 12  # Caps Lock + F2-F12
        assert ctrl.HOTKEY_PRESETS[0] == "<caps_lock>"  # first = recommended default

    def test_set_model(self, ctrl):
        ctrl.set_model("tiny.en")
        assert ctrl.selected_model == "tiny.en"

    def test_model_options(self, ctrl):
        assert len(ctrl.MODEL_OPTIONS) == 3


class TestOnboardingApplySettings:
    def test_apply_settings(self, ctrl, onboarding_dir):
        ctrl.set_microphone("mic-1")
        ctrl.set_hotkey("<f4>")
        ctrl.set_model("tiny.en")

        class MockConfig:
            microphone = None
            hotkey = "<f2>"
            model_size = "small.en"
            def save(self):
                pass

        config = MockConfig()
        ctrl.apply_settings(config)
        assert config.microphone == "mic-1"
        assert config.hotkey == "<f4>"
        assert config.model_size == "tiny.en"

    def test_apply_settings_no_mic(self, ctrl):
        ctrl.set_microphone(None)
        ctrl.set_hotkey("<f2>")
        ctrl.set_model("small.en")

        class MockConfig:
            microphone = "old-mic"
            hotkey = "<f2>"
            model_size = "small.en"
            def save(self):
                pass

        config = MockConfig()
        ctrl.apply_settings(config)
        # Should not overwrite when None
        assert config.microphone == "old-mic"


class TestOnboardingWizardE2E:
    """#8: End-to-end test of the wizard flow through the service layer.

    Verifies that:
    - is_first_run() returns True when onboarding_completed is False
    - The wizard can set microphone, hotkey, and model selections
    - apply_settings persists the choices and marks onboarding complete
    - After apply, is_first_run() returns False (wizard won't reappear)
    """

    def test_full_wizard_flow(self, onboarding_dir):
        """Simulate the React wizard's IPC call sequence."""
        from voice_typer.server.onboarding import OnboardingController

        # 1) Backend: first-run detection
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True, (
            "Wizard should appear when onboarding_completed is False"
        )

        # 2) Wizard starts
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.current_step == 0
        assert ctrl.total_steps == 5

        # 3) Step 1: select microphone
        ctrl.next_step()  # advance to step 1 (Microphone)
        ctrl.set_microphone("mic-usb")
        assert ctrl.selected_microphone == "mic-usb"

        # 4) Step 2: select hotkey
        ctrl.next_step()
        ctrl.set_hotkey("<f4>")
        assert ctrl.selected_hotkey == "<f4>"

        # 5) Step 3: select model
        ctrl.next_step()
        ctrl.set_model("tiny.en")
        assert ctrl.selected_model == "tiny.en"

        # 6) Step 4: apply settings (final step before done)
        ctrl.next_step()

        # 7) Apply settings to a mock config (mirrors service.onboarding_apply)
        from voice_typer.server.config import Config
        cfg = Config()
        cfg.microphone = None
        cfg.hotkey = "<f2>"
        cfg.model_size = "small.en"
        ctrl.apply_settings(cfg)
        ctrl.mark_complete()
        cfg.onboarding_completed = True
        cfg.save()

        # 8) Verify the wizard won't reappear
        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False, (
            "Wizard should NOT reappear after apply_settings + mark_complete"
        )

        # 9) Verify the user's choices were persisted
        cfg2 = Config.load()
        assert cfg2.microphone == "mic-usb"
        assert cfg2.hotkey == "<f4>"
        assert cfg2.model_size == "tiny.en"
        assert cfg2.onboarding_completed is True

    def test_skip_flow(self, onboarding_dir):
        """Skip path: user clicks 'Skip' on step 0 — defaults are kept."""
        from voice_typer.server.onboarding import OnboardingController
        from voice_typer.server.config import Config

        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is True

        # Skip immediately
        ctrl.skip()
        ctrl.mark_complete()

        # Wizard won't reappear
        ctrl2 = OnboardingController(config_dir=onboarding_dir)
        assert ctrl2.is_first_run() is False

        # Config retains defaults (wizard was skipped before any set_* call)
        cfg = Config.load()
        # NATIVE-001: default hotkey is platform-aware
        from voice_typer.server.config import _default_hotkey_for_platform
        assert cfg.hotkey == _default_hotkey_for_platform()
        assert cfg.model_size == "small.en"  # default


# ── 17-H-FIX-1: service-layer onboarding_apply side effects ────────────


@pytest.fixture
def app_with_service(tmp_path, monkeypatch):
    """Build a real VoiceTyperApp + VoiceTyperService with mocked deps.

    Mirrors the ``app`` fixture in tests/test_app.py: heavy imports
    are mocked, pynput is forced as the hotkey backend, and autostart
    helpers are stubbed so __init__ doesn't touch the OS.
    """
    # Point _config_dir at tmp_path so Config.save() is isolated.
    monkeypatch.setattr(
        "voice_typer.server.config._config_dir", lambda: tmp_path
    )

    # Mock heavy hardware/GUI deps (in addition to conftest's autouse
    # mock_heavy_imports, which doesn't run for this module-scope
    # override — be defensive and set them up explicitly here).
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
    monkeypatch.setitem(sys.modules, "faster_whisper", MagicMock())
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", MagicMock())
    monkeypatch.setitem(sys.modules, "pynput", MagicMock())
    monkeypatch.setitem(sys.modules, "pynput.keyboard", MagicMock())
    monkeypatch.setitem(sys.modules, "pystray", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
    monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())

    # Prevent the app's atexit handler from polluting test output.
    monkeypatch.setattr(
        "voice_typer.server.app.atexit.register", lambda *a, **kw: None
    )

    # Stub autostart helpers so __init__ doesn't touch the OS.
    monkeypatch.setattr("voice_typer.server.app.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("voice_typer.server.app.enable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.disable_autostart", lambda: True)
    monkeypatch.setattr("voice_typer.server.app.list_microphones", lambda: [])

    # Force PynputHotkey backend so tests can assert hotkey_str
    # without depending on native binaries. Patch BOTH app and
    # hotkey_dispatcher namespaces (see TEST-033 / Round 11 fix in
    # tests/test_app.py for why both are required).
    from voice_typer.server.hotkeys import PynputHotkey
    _force_pynput = lambda hotkey_str: PynputHotkey(hotkey_str)
    monkeypatch.setattr(
        "voice_typer.server.app.create_hotkey_backend", _force_pynput
    )
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        _force_pynput,
    )

    from voice_typer.server.app import VoiceTyperApp
    from voice_typer.server.service import VoiceTyperService

    app = VoiceTyperApp()
    # Deterministic test behavior: no ESC hotkey, opt into voice consent.
    app.config.esc_cancel_enabled = False
    app.config.voice_biometric_consent = True
    # Mock the transcriber so ModelManager doesn't try to load a real model.
    app.models.transcriber = MagicMock()
    app.models.transcriber.is_loaded = True
    app.models._sync_registry_from_fields()

    service = VoiceTyperService(app)
    return app, service


@pytest.fixture
def captured_events(monkeypatch):
    """Capture all events pushed via _push_event_now."""
    events: list[dict] = []
    import voice_typer.server.ipc_server as ipc_mod
    monkeypatch.setattr(
        ipc_mod, "_push_event_now", lambda msg: events.append(msg) or True
    )
    return events


class TestOnboardingApplySideEffects:
    """17-H-FIX-1: onboarding_apply must re-register the hotkey and
    push a config_changed event so the user's wizard choices take
    effect immediately (without app restart).

    Previously onboarding_apply only called config.save(), so the
    hotkey/model/mic chosen in the first-run wizard were ignored
    until the next app launch.
    """

    def test_hotkey_re_registered_without_restart(
        self, app_with_service, captured_events
    ):
        """The dictation hotkey backend reflects the wizard's choice
        immediately after onboarding_apply — no restart needed."""
        app, service = app_with_service

        # Wizard flow: start, pick a non-default hotkey, apply.
        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        result = service.onboarding_apply()

        assert result == {"ok": True}, f"onboarding_apply failed: {result}"

        # The hotkey dispatcher should have a live backend whose
        # hotkey_str matches the user's selection. Before 17-H-FIX-1,
        # apply_config_side_effects was never called so the backend
        # would still be None (or hold the default hotkey).
        backend = app.hotkeys._hotkey_backend
        assert backend is not None, (
            "Hotkey backend was not registered by onboarding_apply — "
            "apply_config_side_effects was not invoked"
        )
        assert backend.hotkey_str == "<f6>", (
            f"Expected hotkey_str='<f6>' after onboarding_apply, "
            f"got {backend.hotkey_str!r}"
        )

    def test_config_changed_event_pushed(
        self, app_with_service, captured_events
    ):
        """onboarding_apply pushes a config_changed event so the
        renderer can refresh UI-local state without a bespoke
        get_config round-trip (parity with set_config)."""
        app, service = app_with_service

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_apply()

        config_events = [
            e for e in captured_events if e.get("type") == "config_changed"
        ]
        assert len(config_events) >= 1, (
            f"Expected at least one config_changed event, got: {captured_events}"
        )
        # The event data must carry the wizard's hotkey choice so the
        # renderer can update its hotkey label without re-fetching.
        data = config_events[-1].get("data", {})
        assert data.get("hotkey") == "<f6>", (
            f"config_changed event data should include hotkey='<f6>', got: {data}"
        )
        assert "model_size" in data, (
            "config_changed event data should include model_size"
        )

    def test_onboarding_completed_persisted(
        self, app_with_service, captured_events
    ):
        """The existing onboarding_completed=True + config.save()
        behavior must be preserved by the refactor."""
        app, service = app_with_service

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_apply()

        assert app.config.onboarding_completed is True
        assert app.config.hotkey == "<f6>"

    def test_model_change_invoked_when_model_differs(
        self, app_with_service, captured_events, monkeypatch
    ):
        """When the user picks a non-default model, onboarding_apply
        must invoke ModelManager.change_model so the new model loads
        immediately (or queues via _pending_model_change if the
        background loader hasn't finished)."""
        app, service = app_with_service

        # Spy on change_model — don't actually run the unload/load
        # cycle (which would try to load a real model in the test env).
        change_model_calls: list[str] = []
        monkeypatch.setattr(
            app.models,
            "change_model",
            lambda model_size: change_model_calls.append(model_size),
        )

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        service.onboarding_set_model("tiny.en")  # non-default
        service.onboarding_apply()

        assert change_model_calls == ["tiny.en"], (
            f"Expected change_model('tiny.en'), got: {change_model_calls}"
        )

    def test_model_change_skipped_when_model_unchanged(
        self, app_with_service, captured_events, monkeypatch
    ):
        """When the user keeps the default model, onboarding_apply
        must NOT invoke change_model (avoids an expensive no-op
        unload/load cycle)."""
        app, service = app_with_service

        change_model_calls: list[str] = []
        monkeypatch.setattr(
            app.models,
            "change_model",
            lambda model_size: change_model_calls.append(model_size),
        )

        service.onboarding_start()
        service.onboarding_set_hotkey("<f6>")
        # Don't call onboarding_set_model — OnboardingController's
        # default is "small.en", which matches Config's default.
        service.onboarding_apply()

        assert change_model_calls == [], (
            f"change_model should NOT be called when model is unchanged, "
            f"got: {change_model_calls}"
        )
