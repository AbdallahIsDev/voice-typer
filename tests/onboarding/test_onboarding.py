"""Onboarding controller tests split out of the former ``tests/test_history_and_models.py``.

Domain: onboarding — OnboardingController no longer takes
on_step_change / on_complete callbacks (removed), and
``onboarding_apply`` routes model switches through
``self.change_model`` (SVC-10) instead of reaching into
``app.models.change_model`` directly.

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed.
"""

from __future__ import annotations

import inspect


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
    """SVC-10: ``onboarding_apply`` routes the model switch through
    ``self.change_model`` (the ADR-0008-§3.1 service-layer wrapper)
    instead of reaching into ``app.models.change_model`` directly."""

    def test_calls_self_change_model_not_app_models_directly(self, tmp_config_dir, monkeypatch):
        """When the user picks a non-default model in onboarding,
        ``onboarding_apply`` invokes ``self.change_model`` (which goes
        through ``app.change_model`` -> ``app.models.change_model``)."""
        import voice_typer.server.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "publish", lambda msg: True)

        import contextlib
        from unittest.mock import MagicMock

        from voice_typer.server.service import VoiceTyperService

        app = MagicMock()
        app.config.onboarding_completed = False
        app.config.model_size = "small.en"
        app.config.save = MagicMock(return_value=True)

        @contextlib.contextmanager
        def _fake_lock():
            yield

        app._config_mutation_lock = _fake_lock()

        service = VoiceTyperService(app)

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
