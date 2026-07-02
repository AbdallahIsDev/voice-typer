"""Tests for voice_typer.onboarding — OnboardingController wizard."""

import json
import pytest
from pathlib import Path


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
