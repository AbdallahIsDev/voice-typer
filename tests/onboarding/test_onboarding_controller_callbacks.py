"""Tests for ``OnboardingController`` callback removal and service change-model routing."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock


class TestOnboardingControllerRemovesStepCallbacks:
    """on_step_change and on_complete callbacks removed."""

    def test_no_callbacks_in_init(self):
        from voice_typer.server.onboarding import OnboardingController

        source = inspect.getsource(OnboardingController.__init__)
        assert "self.on_step_change =" not in source
        assert "self.on_complete =" not in source

    def test_next_step_no_callback_invocation(self):
        from voice_typer.server.onboarding import OnboardingController

        source = inspect.getsource(OnboardingController.next_step)
        assert "on_step_change" not in source
        assert "on_complete" not in source


class TestOnboardingUsesServiceChangeModel:
    """SVC-10: ``onboarding_apply`` routes the model switch through"""

    def test_calls_self_change_model_not_app_models_directly(self, tmp_config_dir, monkeypatch):
        """When the user picks a non-default model in onboarding,"""
        import contextlib

        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", lambda msg: True)

        from voice_typer.server.service import LausuService

        app = MagicMock()
        app.config.onboarding_completed = False
        app.config.model_size = "small.en"
        app.config.save = MagicMock(return_value=True)

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app._config_mutation_lock = _fake_lock()

        service = LausuService(app)

        from voice_typer.server.onboarding import OnboardingController

        ctrl = OnboardingController()
        ctrl.set_hotkey("<f6>")
        ctrl.set_model("tiny.en")
        service._onboarding = ctrl

        service.onboarding_apply()

        (
            app.change_model.assert_called_once_with("tiny.en"),
            (
                "onboarding_apply should route model switch through "
                "self.change_model (SVC-10) which delegates to app.change_model"
            ),
        )
