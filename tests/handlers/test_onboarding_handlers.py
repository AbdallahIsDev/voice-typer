"""Unit tests for ``OnboardingHandlersMixin`` (CR-12)."""

from __future__ import annotations

import logging

import pytest
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE


class TestOnboardingIsFirstRun:
    """``_handle_onboarding_is_first_run``, returns first-run flag."""

    def test_happy_path_returns_onboarding_first_run(self, ipc_server, fake_service):
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        resp = ipc_server._handle_onboarding_is_first_run({}, {})
        assert resp["type"] == "onboarding_first_run"
        assert resp["data"] == {"is_first_run": True}

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.onboarding_is_first_run.side_effect = RuntimeError("disk error")
        resp = ipc_server._handle_onboarding_is_first_run({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestOnboardingStepNavigation:
    """The 3 step-navigation handlers all return ``{type: onboarding_step}``."""

    def test_start_returns_onboarding_step(self, ipc_server, fake_service):
        fake_service.onboarding_start.return_value = {"step": "welcome", "index": 0}
        resp = ipc_server._handle_onboarding_start({}, {})
        assert resp["type"] == "onboarding_step"
        assert resp["data"] == {"step": "welcome", "index": 0}

    def test_next_step_returns_onboarding_step(self, ipc_server, fake_service):
        fake_service.onboarding_next_step.return_value = {"step": "hotkey", "index": 2}
        resp = ipc_server._handle_onboarding_next_step({}, {})
        assert resp["type"] == "onboarding_step"

    def test_prev_step_returns_onboarding_step(self, ipc_server, fake_service):
        fake_service.onboarding_prev_step.return_value = {"step": "welcome", "index": 0}
        resp = ipc_server._handle_onboarding_prev_step({}, {})
        assert resp["type"] == "onboarding_step"

    def test_next_step_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.onboarding_next_step.side_effect = RuntimeError("at last step")
        resp = ipc_server._handle_onboarding_next_step({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestOnboardingSetMicrophone:
    """``_handle_onboarding_set_microphone``, validates ``mic_id`` (required)."""

    def test_happy_path_returns_ack_with_result(self, ipc_server, fake_service):
        fake_service.onboarding_set_microphone.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "usb_1"}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True}
        fake_service.onboarding_set_microphone.assert_called_once_with("usb_1")

    def test_missing_mic_id_returns_missing_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_microphone({}, {})
        assert resp["type"] == "error"
        # Validator emits the namespaced code (the bare-form
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.onboarding_set_microphone.assert_not_called()

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": 123}, {})
        assert resp["type"] == "error"
        # Namespaced ``client.invalid_field`` (the bare-form
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "mic_id"

    def test_service_returns_error_dict_flips_response_type_to_error(self, ipc_server, fake_service):
        """If the service returns ``{\"error\": ...}``, the handler flips"""
        fake_service.onboarding_set_microphone.return_value = {
            "error": "microphone not found",
        }
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "ghost"}, {})
        assert resp["type"] == "error"
        assert resp["data"] == {"error": "microphone not found"}


class TestOnboardingSetHotkey:
    """``_handle_onboarding_set_hotkey``, validates ``hotkey`` (default ``<caps_lock>``)."""

    def test_happy_path_with_explicit_hotkey(self, ipc_server, fake_service):
        fake_service.onboarding_set_hotkey.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_hotkey({"hotkey": "<f4>"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_hotkey.assert_called_once_with("<f4>")

    def test_missing_hotkey_uses_default_caps_lock(self, ipc_server, fake_service):
        """Empty payload → default hotkey ``<caps_lock>`` is used (required=False)."""
        fake_service.onboarding_set_hotkey.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_hotkey({}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_hotkey.assert_called_once_with("<caps_lock>")

    def test_non_string_hotkey_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_hotkey({"hotkey": 99}, {})
        assert resp["type"] == "error"
        # Namespaced ``client.invalid_field`` (the bare-form
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "hotkey"


class TestOnboardingSetModel:
    """canonical ``DEFAULT_MODEL_SIZE`` sentinel, no concrete default"""

    def test_happy_path_with_explicit_model(self, ipc_server, fake_service):
        fake_service.onboarding_set_model.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_model({"model": "tiny"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_model.assert_called_once_with("tiny")

    def test_missing_model_uses_default_tiny(self, ipc_server, fake_service):
        """``onboarding_set_model`` command)."""
        fake_service.onboarding_set_model.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_model({}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_model.assert_called_once_with(DEFAULT_MODEL_SIZE)

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_model({"model": ["small"]}, {})
        assert resp["type"] == "error"
        # Namespaced ``client.invalid_field`` (the bare-form
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"


class TestOnboardingSetBackend:
    """``_handle_onboarding_set_backend``, validates ``backend`` (required)."""

    def test_happy_path_with_local(self, ipc_server, fake_service):
        fake_service.onboarding_set_backend.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_backend({"backend": "local"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_backend.assert_called_once_with("local")

    def test_happy_path_with_cloud(self, ipc_server, fake_service):
        fake_service.onboarding_set_backend.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_backend({"backend": "cloud"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_backend.assert_called_once_with("cloud")

    def test_missing_backend_returns_missing_field_error(self, ipc_server, fake_service):
        """``backend`` is required, an empty payload must NOT silently"""
        resp = ipc_server._handle_onboarding_set_backend({}, {})
        assert resp["type"] == "error"
        # Required field absent → ``client.missing_field`` (not
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "backend"

    def test_non_string_backend_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_backend({"backend": 42}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "backend"

    def test_service_error_flips_response_to_error(self, ipc_server, fake_service):
        """An invalid choice surfaces the service's ValueError as an"""
        fake_service.onboarding_set_backend.return_value = {
            "error": "unknown onboarding backend choice: 'nope'",
        }
        resp = ipc_server._handle_onboarding_set_backend({"backend": "nope"}, {})
        assert resp["type"] == "error"
        assert resp["data"] == {"error": "unknown onboarding backend choice: 'nope'"}


class TestOnboardingSkipAndApply:
    """``_handle_onboarding_skip`` / ``_handle_onboarding_apply``, decision handlers."""

    def test_skip_success_returns_ack(self, ipc_server, fake_service):
        fake_service.onboarding_skip.return_value = {"ok": True, "skipped": True}
        resp = ipc_server._handle_onboarding_skip({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True, "skipped": True}

    def test_skip_with_error_in_result_returns_error_type(self, ipc_server, fake_service):
        """A ``{\"error\": ...}`` result flips the type to ``error``."""
        fake_service.onboarding_skip.return_value = {"error": "cannot skip welcome step"}
        resp = ipc_server._handle_onboarding_skip({}, {})
        assert resp["type"] == "error"

    def test_apply_success_returns_ack(self, ipc_server, fake_service):
        fake_service.onboarding_apply.return_value = {"ok": True, "applied": True}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True, "applied": True}

    def test_apply_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.onboarding_apply.side_effect = RuntimeError("config save failed")
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestOnboardingListHandlers:
    """The 3 list-returning handlers (microphones / models / hotkey presets)."""

    def test_get_microphones_returns_onboarding_microphones(self, ipc_server, fake_service):
        fake_service.onboarding_get_microphones.return_value = [
            {"id": "0", "name": "Built-in"},
        ]
        resp = ipc_server._handle_onboarding_get_microphones({}, {})
        assert resp["type"] == "onboarding_microphones"
        assert resp["data"] == [{"id": "0", "name": "Built-in"}]

    def test_get_model_options_returns_onboarding_models(self, ipc_server, fake_service):
        fake_service.onboarding_get_model_options.return_value = [
            {"name": "tiny.en", "size_mb": 75},
        ]
        resp = ipc_server._handle_onboarding_get_model_options({}, {})
        assert resp["type"] == "onboarding_models"
        assert resp["data"] == [{"name": "tiny.en", "size_mb": 75}]

    def test_get_hotkey_presets_returns_onboarding_hotkey_presets(self, ipc_server, fake_service):
        fake_service.onboarding_get_hotkey_presets.return_value = [
            {"label": "F2", "hotkey": "<f2>"},
        ]
        resp = ipc_server._handle_onboarding_get_hotkey_presets({}, {})
        assert resp["type"] == "onboarding_hotkey_presets"
        assert resp["data"] == [{"label": "F2", "hotkey": "<f2>"}]

    def test_get_microphones_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.onboarding_get_microphones.side_effect = RuntimeError("portaudio")
        resp = ipc_server._handle_onboarding_get_microphones({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestOnboardingCheckPermissionsHandler:
    """``_handle_onboarding_check_permissions``, UX-4 / UX-27."""

    def test_happy_path_returns_onboarding_permissions(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_check_permissions({}, {})
        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert set(data.keys()) == {"platform", "state", "needed", "instructions"}
        assert data["platform"] in {"windows", "macos", "linux", "unknown"}
        assert data["state"] in {"granted", "denied", "unknown"}
        assert isinstance(data["needed"], bool)

    def test_does_not_call_service(self, ipc_server, fake_service):
        """permission probe lives in ``voice_typer.server.permissions``"""
        # If the handler tried to call self.service.onboarding_check_permissions,
        fake_service.onboarding_check_permissions = None  # type: ignore[attr-defined]
        resp = ipc_server._handle_onboarding_check_permissions({}, {})
        assert resp["type"] == "onboarding_permissions"

    def test_check_permissions_failure_returns_error(self, ipc_server, fake_service, monkeypatch):
        """If the permission probe raises, the handler returns an"""
        from voice_typer.server import onboarding as onboarding_mod

        def _boom(self):
            raise RuntimeError("probe failed")

        monkeypatch.setattr(onboarding_mod.OnboardingController, "check_permissions", _boom)
        resp = ipc_server._handle_onboarding_check_permissions({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestXzEh002ServiceErrorRedaction:
    """XZ-EH-002: the five ``set_*`` / ``skip`` / ``apply`` handlers"""

    _SECRET_BEARER = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    _SECRET_SK = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"

    def test_apply_redacts_bearer_token(self, ipc_server, fake_service):
        """``onboarding_apply`` service error containing a Bearer token"""
        fake_service.onboarding_apply.return_value = {
            "error": f"config write failed: auth header={self._SECRET_BEARER}"
        }
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        # The Bearer token prefix is preserved but the secret suffix
        assert "Bearer ***" in resp["data"]["error"]
        assert self._SECRET_BEARER not in resp["data"]["error"]

    def test_apply_redacts_openai_key(self, ipc_server, fake_service):
        """``onboarding_apply`` service error containing an OpenAI"""
        fake_service.onboarding_apply.return_value = {"error": f"cloud config error: invalid key {self._SECRET_SK}"}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        # ``sk-...`` is fully replaced with ``***`` by ``redact_secret``
        assert self._SECRET_SK not in resp["data"]["error"]
        assert "***" in resp["data"]["error"]

    def test_set_microphone_redacts_error(self, ipc_server, fake_service):
        """``onboarding_set_microphone`` service error is redacted."""
        fake_service.onboarding_set_microphone.return_value = {
            "error": f"mic probe failed: token={self._SECRET_BEARER}"
        }
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "usb_1"}, {})
        assert resp["type"] == "error"
        assert self._SECRET_BEARER not in resp["data"]["error"]
        assert "***" in resp["data"]["error"]

    def test_set_hotkey_redacts_error(self, ipc_server, fake_service):
        """``onboarding_set_hotkey`` service error is redacted."""
        fake_service.onboarding_set_hotkey.return_value = {"error": f"hotkey reserved: {self._SECRET_SK}"}
        resp = ipc_server._handle_onboarding_set_hotkey({"hotkey": "<f2>"}, {})
        assert resp["type"] == "error"
        assert self._SECRET_SK not in resp["data"]["error"]

    def test_set_model_redacts_error(self, ipc_server, fake_service):
        """``onboarding_set_model`` service error is redacted."""
        fake_service.onboarding_set_model.return_value = {"error": f"model unavailable: {self._SECRET_BEARER}"}
        resp = ipc_server._handle_onboarding_set_model({"model": "tiny"}, {})
        assert resp["type"] == "error"
        assert self._SECRET_BEARER not in resp["data"]["error"]

    def test_skip_redacts_error(self, ipc_server, fake_service):
        """``onboarding_skip`` service error is redacted."""
        fake_service.onboarding_skip.return_value = {"error": f"skip failed: {self._SECRET_SK}"}
        resp = ipc_server._handle_onboarding_skip({}, {})
        assert resp["type"] == "error"
        assert self._SECRET_SK not in resp["data"]["error"]

    def test_success_result_not_mutated_by_redaction(self, ipc_server, fake_service):
        """When the service returns a success dict (no ``\"error\"`` key),"""
        fake_service.onboarding_apply.return_value = {"ok": True, "step": "done"}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True, "step": "done"}
        # No ``"error"`` key was added by the redaction helper.
        assert "error" not in resp["data"]

    def test_none_error_value_treated_as_success(self, ipc_server, fake_service):
        """XZ-EH-015: if the service returns ``{\"error\": None}`` (key"""
        fake_service.onboarding_apply.return_value = {"error": None}
        resp = ipc_server._handle_onboarding_apply({}, {})
        # XZ-EH-015: {"error": None} -> type is "ack" (was "error").
        assert resp["type"] == "ack"
        # The None value is preserved (not redacted to a string).
        assert resp["data"]["error"] is None

    def test_explicit_string_error_still_flips_to_error_type(self, ipc_server, fake_service):
        """XZ-EH-015 sanity: a real string-valued ``error`` is still"""
        fake_service.onboarding_apply.return_value = {"error": "config write failed"}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["error"] == "config write failed"

    def test_none_error_in_set_microphone_treated_as_success(self, ipc_server, fake_service):
        """XZ-EH-015: the same {\"error\": None} -> ack fix applies to all"""
        fake_service.onboarding_set_microphone.return_value = {"error": None, "ok": True}
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "usb_1"}, {})
        assert resp["type"] == "ack"
        assert resp["data"]["error"] is None


class TestOnboardingStartRerunGuard:
    """DE-39: ``_handle_onboarding_start`` refuses to re-run the wizard"""

    def test_first_run_true_proceeds_normally(self, ipc_server, fake_service):
        """When ``onboarding_is_first_run`` returns True, the handler"""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "onboarding_step"
        assert resp["data"]["step_name"] == "Welcome"
        fake_service.onboarding_start.assert_called_once()

    def test_first_run_false_without_force_returns_already_complete_error(self, ipc_server, fake_service):
        """When onboarding is already complete and no ``force`` flag is"""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete", (
            f"DE-39: expected code 'onboarding_already_complete'; got: {resp['data'].get('code')!r}"
        )
        assert "force" in resp["data"]["message"].lower(), "DE-39: error message must mention the force flag"
        fake_service.onboarding_start.assert_not_called()

    def test_first_run_false_with_force_proceeds(self, ipc_server, fake_service):
        """Settings → Troubleshooting → Re-run Setup Wizard)."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        resp = ipc_server._handle_onboarding_start({"force": True}, {})

        assert resp["type"] == "onboarding_step"
        assert resp["data"]["step_name"] == "Welcome"
        fake_service.onboarding_start.assert_called_once()

    def test_first_run_false_with_force_falsy_string_does_not_proceed(self, ipc_server, fake_service):
        """``force`` must be a real boolean True, the string"""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        # Empty string → falsy → guard fires.
        resp = ipc_server._handle_onboarding_start({"force": ""}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete"

    def test_non_dict_data_does_not_crash_guard(self, ipc_server, fake_service):
        """non-dict (renderer may send no payload). The handler coerces"""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        # None payload, must not raise TypeError.
        resp = ipc_server._handle_onboarding_start(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete"

    def test_guard_logs_warning_when_blocking(self, ipc_server, fake_service, caplog):
        """DE-39: when the guard blocks, the handler logs a WARNING so"""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            ipc_server._handle_onboarding_start({}, {})

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "onboarding_start" in r.getMessage() and "already" in r.getMessage()
        ]
        assert warnings, "DE-39: rejection must be logged at WARNING for operator visibility"


class TestServiceErrorsLogged:
    """DE-40: when a service returns ``{\"error\": ...}``, the handler"""

    @pytest.mark.parametrize(
        "handler_name, service_method, payload",
        [
            (
                "_handle_onboarding_set_microphone",
                "onboarding_set_microphone",
                {"mic_id": "ghost"},
            ),
            (
                "_handle_onboarding_set_hotkey",
                "onboarding_set_hotkey",
                {"hotkey": "<f4>"},
            ),
            (
                "_handle_onboarding_set_model",
                "onboarding_set_model",
                {"model": "tiny.en"},
            ),
            (
                "_handle_onboarding_set_backend",
                "onboarding_set_backend",
                {"backend": "local"},
            ),
            ("_handle_onboarding_skip", "onboarding_skip", {}),
            ("_handle_onboarding_apply", "onboarding_apply", {}),
        ],
    )
    def test_service_error_is_logged_at_warning(
        self,
        ipc_server,
        fake_service,
        caplog,
        handler_name,
        service_method,
        payload,
    ):
        """Each of the 6 onboarding handlers that delegate ack-vs-error"""
        service_mock = getattr(fake_service, service_method)
        service_mock.return_value = {"error": "service-layer failure"}
        handler = getattr(ipc_server, handler_name)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = handler(payload, {})

        # Response shape is unchanged, only adds a log line.
        assert resp["type"] == "error"
        assert resp["data"] == {"error": "service-layer failure"}

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and service_method in r.getMessage()
            and "service returned error" in r.getMessage()
            and "service-layer failure" in r.getMessage()
        ]
        assert warnings, (
            f"DE-40: {handler_name} must log a WARNING with the command name and the service-returned error string"
        )

    def test_service_success_does_not_log_warning(self, ipc_server, fake_service, caplog):
        """DE-40: when the service returns success (no ``error`` key),"""
        fake_service.onboarding_apply.return_value = {"ok": True}

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_onboarding_apply({}, {})

        assert resp["type"] == "ack"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_apply" in r.getMessage()
            and "service returned error" in r.getMessage()
        ]
        assert not warnings, "DE-40: success path must not emit the service-error warning"


class TestMarkStartedFailureLogged:
    """``exc_info=True`` instead of being silently swallowed."""

    def test_mark_started_failure_logs_warning_with_exc_info(self, ipc_server, fake_service, monkeypatch, caplog):
        """When ``mark_started`` raises, the handler must emit a WARNING"""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        # Force mark_started to raise.
        from voice_typer.server import onboarding as onboarding_mod

        def _boom(self):
            raise OSError("disk full")

        monkeypatch.setattr(onboarding_mod.OnboardingController, "mark_started", _boom)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "onboarding_step"

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_start" in r.getMessage()
            and "mark_started failed" in r.getMessage()
        ]
        assert warnings, "DE-41: mark_started failure must be logged at WARNING"
        assert any(r.exc_info is not None for r in warnings), (
            "DE-41: warning must carry exc_info=True so the traceback is logged"
        )

    def test_mark_started_success_does_not_log_warning(self, ipc_server, fake_service, monkeypatch, caplog):
        """When ``mark_started`` succeeds, NO warning is logged."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        fake_service.onboarding_start.return_value = {
            "step": 0,
            "total_steps": 6,
            "step_name": "Welcome",
        }

        from voice_typer.server import onboarding as onboarding_mod

        def _ok(self):
            return None

        monkeypatch.setattr(onboarding_mod.OnboardingController, "mark_started", _ok)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "onboarding_step"
        warnings = [
            r for r in caplog.records if r.levelno == logging.WARNING and "mark_started failed" in r.getMessage()
        ]
        assert not warnings, "DE-41: success path must not emit the mark_started warning"


class TestOnboardingApplyServiceErrorLogging:
    """``_handle_onboarding_apply`` logs the service-returned error twice"""

    _SECRET_SK = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"

    def test_apply_logs_raw_warning_then_redacted_error(self, ipc_server, fake_service, caplog):
        """A service error is logged at WARNING with the raw string and at"""
        raw_error = f"config write failed: invalid key {self._SECRET_SK}"
        fake_service.onboarding_apply.return_value = {"error": raw_error}

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_onboarding_apply({}, {})

        assert resp["type"] == "error"
        # Only the redacted form reaches the renderer.
        assert self._SECRET_SK not in resp["data"]["error"]
        assert "***" in resp["data"]["error"]

        # WARNING carries the raw, unredacted string (operator-only log).
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_apply" in r.getMessage()
            and "service returned error" in r.getMessage()
        ]
        assert warnings, "apply must log the service error at WARNING"
        assert raw_error in warnings[0].getMessage()

        # ERROR carries the redacted form.
        errors = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "onboarding_apply failed (redacted)" in r.getMessage()
        ]
        assert errors, "apply must mirror the redacted service error at ERROR"
        assert self._SECRET_SK not in errors[0].getMessage()
        assert "***" in errors[0].getMessage()

    def test_skip_error_has_no_error_level_mirror(self, ipc_server, fake_service, caplog):
        """The ERROR-level redacted mirror is apply-specific, the other"""
        fake_service.onboarding_skip.return_value = {"error": "cannot skip welcome step"}

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_onboarding_skip({}, {})

        assert resp["type"] == "error"
        mirrors = [r for r in caplog.records if r.levelno == logging.ERROR and "failed (redacted)" in r.getMessage()]
        assert not mirrors, "the ERROR-level redacted mirror is apply-specific"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_skip" in r.getMessage()
            and "service returned error" in r.getMessage()
        ]
        assert warnings, "skip must still log the service error at WARNING"
