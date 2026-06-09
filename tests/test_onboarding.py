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
        (onboarding_dir / "config.json").write_text("{}", encoding="utf-8")
        from voice_typer.server.onboarding import OnboardingController
        ctrl = OnboardingController(config_dir=onboarding_dir)
        assert ctrl.is_first_run() is False

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

    def test_on_step_change_callback(self, ctrl):
        steps_reached = []
        ctrl.on_step_change = lambda s: steps_reached.append(s)
        ctrl.next_step()
        assert len(steps_reached) == 1
        assert steps_reached[0] == 1

    def test_on_complete_callback_on_last_step(self, ctrl):
        completed = []
        ctrl.on_complete = lambda: completed.append(True)
        ctrl._current_step = 3
        ctrl.next_step()  # Step 4 = last step
        assert len(completed) == 1


class TestOnboardingSkip:
    def test_skip_marks_complete(self, ctrl):
        completed = []
        ctrl.on_complete = lambda: completed.append(True)
        ctrl.skip()
        assert ctrl.is_first_run() is False
        assert len(completed) == 1


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
        assert len(ctrl.HOTKEY_PRESETS) == 11  # F2-F12

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
