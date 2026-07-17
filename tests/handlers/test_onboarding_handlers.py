"""Unit tests for ``OnboardingHandlersMixin`` (CR-12).

Covers the 13 onboarding-wizard IPC handlers defined in
``voice_typer/server/handlers/onboarding_handlers.py``:

Step-navigation handlers (return ``{type: onboarding_step, data: <step>}``):
- ``_handle_onboarding_start``
- ``_handle_onboarding_get_step``
- ``_handle_onboarding_next_step``
- ``_handle_onboarding_prev_step``

Status handlers (return their own ``onboarding_*`` type):
- ``_handle_onboarding_is_first_run`` → ``onboarding_first_run``
- ``_handle_onboarding_get_microphones`` → ``onboarding_microphones``
- ``_handle_onboarding_get_model_options`` → ``onboarding_models``
- ``_handle_onboarding_get_hotkey_presets`` → ``onboarding_hotkey_presets``

Set-style handlers (validate a single field, return ``{type: ack|error, data: <result>}``):
- ``_handle_onboarding_set_microphone`` — validates ``mic_id: str`` (required).
- ``_handle_onboarding_set_hotkey`` — validates ``hotkey: str`` (default ``<f2>``).
- ``_handle_onboarding_set_model`` — validates ``model: str`` (default ``small.en``).

Decision handlers (return ack or error based on whether the service
result contains an ``error`` key):
- ``_handle_onboarding_skip``
- ``_handle_onboarding_apply``

The interesting invariant for the set_* handlers: the response type
is ``ack`` if the service returned a success result, but ``error``
if the service returned a dict containing an ``error`` key (the
service uses this to signal e.g. "microphone not found").  This is
the only handler in the IPC layer that branches on the service
return value's shape rather than on exceptions.
"""

from __future__ import annotations


class TestOnboardingIsFirstRun:
    """``_handle_onboarding_is_first_run`` — returns first-run flag."""

    def test_happy_path_returns_onboarding_first_run(self, ipc_server, fake_service):
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": True}
        resp = ipc_server._handle_onboarding_is_first_run({}, {})
        assert resp["type"] == "onboarding_first_run"
        assert resp["data"] == {"is_first_run": True}

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.onboarding_is_first_run.side_effect = RuntimeError("disk error")
        resp = ipc_server._handle_onboarding_is_first_run({}, {})
        assert resp["type"] == "error"


class TestOnboardingStepNavigation:
    """The 4 step-navigation handlers all return ``{type: onboarding_step}``."""

    def test_start_returns_onboarding_step(self, ipc_server, fake_service):
        fake_service.onboarding_start.return_value = {"step": "welcome", "index": 0}
        resp = ipc_server._handle_onboarding_start({}, {})
        assert resp["type"] == "onboarding_step"
        assert resp["data"] == {"step": "welcome", "index": 0}

    def test_get_step_returns_onboarding_step(self, ipc_server, fake_service):
        fake_service.onboarding_get_step.return_value = {"step": "mic_select", "index": 1}
        resp = ipc_server._handle_onboarding_get_step({}, {})
        assert resp["type"] == "onboarding_step"

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
        assert "at last step" in resp["data"]["message"]


class TestOnboardingSetMicrophone:
    """``_handle_onboarding_set_microphone`` — validates ``mic_id`` (required)."""

    def test_happy_path_returns_ack_with_result(self, ipc_server, fake_service):
        fake_service.onboarding_set_microphone.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "usb_1"}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True}
        fake_service.onboarding_set_microphone.assert_called_once_with("usb_1")

    def test_missing_mic_id_returns_missing_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_microphone({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "missing_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.onboarding_set_microphone.assert_not_called()

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "mic_id"

    def test_service_returns_error_dict_flips_response_type_to_error(self, ipc_server, fake_service):
        """If the service returns ``{"error": ...}``, the handler flips
        the response type to ``error`` (not ``ack``).

        This is the only handler that branches on the service return
        value's shape — the onboarding flow uses it to signal "mic_id
        not found" without raising an exception.
        """
        fake_service.onboarding_set_microphone.return_value = {
            "error": "microphone not found",
        }
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "ghost"}, {})
        assert resp["type"] == "error"
        assert resp["data"] == {"error": "microphone not found"}


class TestOnboardingSetHotkey:
    """``_handle_onboarding_set_hotkey`` — validates ``hotkey`` (default ``<f2>``)."""

    def test_happy_path_with_explicit_hotkey(self, ipc_server, fake_service):
        fake_service.onboarding_set_hotkey.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_hotkey({"hotkey": "<f4>"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_hotkey.assert_called_once_with("<f4>")

    def test_missing_hotkey_uses_default_f2(self, ipc_server, fake_service):
        """Empty payload → default hotkey ``<f2>`` is used (required=False)."""
        fake_service.onboarding_set_hotkey.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_hotkey({}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_hotkey.assert_called_once_with("<f2>")

    def test_non_string_hotkey_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_hotkey({"hotkey": 99}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "hotkey"


class TestOnboardingSetModel:
    """``_handle_onboarding_set_model`` — validates ``model`` (default ``small.en``)."""

    def test_happy_path_with_explicit_model(self, ipc_server, fake_service):
        fake_service.onboarding_set_model.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_model({"model": "tiny.en"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_model.assert_called_once_with("tiny.en")

    def test_missing_model_uses_default_small_en(self, ipc_server, fake_service):
        fake_service.onboarding_set_model.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_model({}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_model.assert_called_once_with("small.en")

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_model({"model": ["small"]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "model"


class TestOnboardingSkipAndApply:
    """``_handle_onboarding_skip`` / ``_handle_onboarding_apply`` — decision handlers."""

    def test_skip_success_returns_ack(self, ipc_server, fake_service):
        fake_service.onboarding_skip.return_value = {"ok": True, "skipped": True}
        resp = ipc_server._handle_onboarding_skip({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True, "skipped": True}

    def test_skip_with_error_in_result_returns_error_type(self, ipc_server, fake_service):
        """A ``{"error": ...}`` result flips the type to ``error``."""
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
        assert "config save failed" in resp["data"]["message"]


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
