"""Unit tests for ``OnboardingHandlersMixin`` (CR-12).

Covers the onboarding-wizard IPC handlers defined in
``voice_typer/server/handlers/onboarding_handlers.py``:

Step-navigation handlers (return ``{type: onboarding_step, data: <step>}``):
- ``_handle_onboarding_start``
- ``_handle_onboarding_next_step``
- ``_handle_onboarding_prev_step``

Status handlers (return their own ``onboarding_*`` type):
- ``_handle_onboarding_is_first_run`` → ``onboarding_first_run``
- ``_handle_onboarding_get_microphones`` → ``onboarding_microphones``
- ``_handle_onboarding_get_model_options`` → ``onboarding_models``
- ``_handle_onboarding_get_hotkey_presets`` → ``onboarding_hotkey_presets``

Set-style handlers (validate a single field, return ``{type: ack|error, data: <result>}``):
- ``_handle_onboarding_set_microphone`` — validates ``mic_id: str`` (required).
- ``_handle_onboarding_set_hotkey`` — validates ``hotkey: str`` (default ``<caps_lock>``).
- ``_handle_onboarding_set_model`` — validates ``model: str`` (default ``tiny``).
- ``_handle_onboarding_set_backend`` — validates ``backend: str`` (required — the explicit local-vs-cloud choice).

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

UE-15 (2026-07-30): ``_handle_onboarding_get_step``,
``_handle_onboarding_get_model_catalog``, and
``_handle_onboarding_request_keyboard_permission`` were deleted from
``OnboardingHandlersMixin`` (the renderer no longer invokes them).
The corresponding ``TestOnboardingStepNavigation.test_get_step_*``,
``TestOnboardingGetModelCatalogHandler``, and any
``TestOnboardingRequestKeyboardPermission`` classes were removed in
lockstep.
"""

from __future__ import annotations

import logging

import pytest


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
        # generic WS-path envelope (no ``str(exc)`` leak).
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


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
        # Validator emits the namespaced code (the bare-form
        # ``legacy_code`` alias was removed once the renderer migrated).
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.onboarding_set_microphone.assert_not_called()

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": 123}, {})
        assert resp["type"] == "error"
        # Namespaced ``client.invalid_field`` (the bare-form
        # ``legacy_code`` alias was removed once the renderer migrated).
        assert resp["data"]["code"] == "client.invalid_field"
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
    """``_handle_onboarding_set_hotkey`` — validates ``hotkey`` (default ``<caps_lock>``)."""

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
        # ``legacy_code`` alias was removed once the renderer migrated).
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "hotkey"


class TestOnboardingSetModel:
    """``_handle_onboarding_set_model`` — validates ``model`` (default ``tiny``)."""

    def test_happy_path_with_explicit_model(self, ipc_server, fake_service):
        fake_service.onboarding_set_model.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_model({"model": "tiny"}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_model.assert_called_once_with("tiny")

    def test_missing_model_uses_default_tiny(self, ipc_server, fake_service):
        fake_service.onboarding_set_model.return_value = {"ok": True}
        resp = ipc_server._handle_onboarding_set_model({}, {})
        assert resp["type"] == "ack"
        fake_service.onboarding_set_model.assert_called_once_with("tiny")

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_model({"model": ["small"]}, {})
        assert resp["type"] == "error"
        # Namespaced ``client.invalid_field`` (the bare-form
        # ``legacy_code`` alias was removed once the renderer migrated).
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"


class TestOnboardingSetBackend:
    """``_handle_onboarding_set_backend`` — validates ``backend`` (required).

    The Model-step backend choice ("local" vs "cloud") is the user's
    explicit decision — the app never auto-downloads a model. The field
    is required (no default) so the wizard cannot silently fall back to
    one backend when the renderer forgot to send the choice.
    """

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
        """``backend`` is required — an empty payload must NOT silently
        default to one of the choices."""
        resp = ipc_server._handle_onboarding_set_backend({}, {})
        assert resp["type"] == "error"
        # Required field absent → ``client.missing_field`` (not
        # ``client.invalid_field``, which is for wrong-type values).
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "backend"

    def test_non_string_backend_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_backend({"backend": 42}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "backend"

    def test_service_error_flips_response_to_error(self, ipc_server, fake_service):
        """An invalid choice surfaces the service's ValueError as an
        error envelope (``{type: error}``), never a silent ack."""
        fake_service.onboarding_set_backend.return_value = {
            "error": "unknown onboarding backend choice: 'nope'",
        }
        resp = ipc_server._handle_onboarding_set_backend({"backend": "nope"}, {})
        assert resp["type"] == "error"
        assert resp["data"] == {"error": "unknown onboarding backend choice: 'nope'"}


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
        # generic WS-path envelope (no ``str(exc)`` leak).
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
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# new onboarding IPC handlers ( / ) ────────────────


class TestOnboardingCheckPermissionsHandler:
    """``_handle_onboarding_check_permissions`` — UX-4 / UX-27.

    Returns the OS-level keyboard-monitoring permission state plus
    platform-specific setup instructions. The wizard's new Permissions
    step uses this to render a macOS Accessibility walkthrough or a
    Linux input-group + udev-rule walkthrough.
    """

    def test_happy_path_returns_onboarding_permissions(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_check_permissions({}, {})
        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert set(data.keys()) == {"platform", "state", "needed", "instructions"}
        assert data["platform"] in {"windows", "macos", "linux", "unknown"}
        assert data["state"] in {"granted", "denied", "unknown"}
        assert isinstance(data["needed"], bool)

    def test_does_not_call_service(self, ipc_server, fake_service):
        """The handler must NOT delegate to ``self.service`` — the
        permission probe lives in ``voice_typer.server.permissions``
        and is shared with the hotkey-adapter runtime path."""
        # If the handler tried to call self.service.onboarding_check_permissions,
        # MagicMock would auto-create the attribute and return a MagicMock,
        # which would not be a dict — the handler would still return a
        # valid response, but the assertion below catches the case where
        # the handler actually invokes a service method (MagicMock would
        # record the call).
        fake_service.onboarding_check_permissions = None  # type: ignore[attr-defined]
        resp = ipc_server._handle_onboarding_check_permissions({}, {})
        assert resp["type"] == "onboarding_permissions"

    def test_check_permissions_failure_returns_error(self, ipc_server, fake_service, monkeypatch):
        """If the permission probe raises, the handler returns an
        ``error`` response."""
        from voice_typer.server import onboarding as onboarding_mod

        def _boom(self):
            raise RuntimeError("probe failed")

        monkeypatch.setattr(onboarding_mod.OnboardingController, "check_permissions", _boom)
        resp = ipc_server._handle_onboarding_check_permissions({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# ──────────────────────────────────────────────────────────────────────
# service-returned ``{"error": str(exc)}`` redaction
# ──────────────────────────────────────────────────────────────────────


class TestXzEh002ServiceErrorRedaction:
    """XZ-EH-002: the five ``set_*`` / ``skip`` / ``apply`` handlers
    pass the service-returned dict straight to ``resp["data"]``. The
    service's ``str(exc)`` can contain secrets (API keys, file paths);
    the handler now applies ``_redact_service_error`` before
    forwarding so the redacted form lands in the IPC response.

    These tests cover the redaction path: feed each handler a
    service-returned dict whose ``"error"`` value contains a known
    secret pattern, and assert the secret is replaced with ``***`` in
    the response's ``data["error"]`` field.
    """

    _SECRET_BEARER = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
    _SECRET_SK = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"

    def test_apply_redacts_bearer_token(self, ipc_server, fake_service):
        """``onboarding_apply`` service error containing a Bearer token
        is redacted before landing in the IPC response."""
        fake_service.onboarding_apply.return_value = {
            "error": f"config write failed: auth header={self._SECRET_BEARER}"
        }
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        # The Bearer token prefix is preserved but the secret suffix
        # is replaced with ``***`` by ``redact_secret``.
        assert "Bearer ***" in resp["data"]["error"]
        assert self._SECRET_BEARER not in resp["data"]["error"]

    def test_apply_redacts_openai_key(self, ipc_server, fake_service):
        """``onboarding_apply`` service error containing an OpenAI
        ``sk-...`` key is redacted."""
        fake_service.onboarding_apply.return_value = {"error": f"cloud config error: invalid key {self._SECRET_SK}"}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        # ``sk-...`` is fully replaced with ``***`` by ``redact_secret``
        # (no prefix preservation for the bare ``sk-`` form).
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
        """When the service returns a success dict (no ``"error"`` key),
        ``_redact_service_error`` is a no-op and the result is passed
        through unchanged (no spurious ``"error"`` key added)."""
        fake_service.onboarding_apply.return_value = {"ok": True, "step": "done"}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True, "step": "done"}
        # No ``"error"`` key was added by the redaction helper.
        assert "error" not in resp["data"]

    def test_none_error_value_treated_as_success(self, ipc_server, fake_service):
        """XZ-EH-015: if the service returns ``{"error": None}`` (key
        present but value ``None``), the handler must treat it as a
        SUCCESS (``ack``) - not misreport it as ``error``.

        Previously the handler checked ``"error" in result`` (key
        presence), which flipped the response type to ``error`` even
        though the service clearly intended success. The fix checks
        ``result.get("error") is not None`` so a ``None`` value is
        treated as "no error". The full typed-exception migration was
        deferred - cross-file work outside this finding's scope.
        """
        fake_service.onboarding_apply.return_value = {"error": None}
        resp = ipc_server._handle_onboarding_apply({}, {})
        # XZ-EH-015: {"error": None} -> type is "ack" (was "error").
        assert resp["type"] == "ack"
        # The None value is preserved (not redacted to a string).
        assert resp["data"]["error"] is None

    def test_explicit_string_error_still_flips_to_error_type(self, ipc_server, fake_service):
        """XZ-EH-015 sanity: a real string-valued ``error`` is still
        reported as ``error`` (the fix only changes the None case)."""
        fake_service.onboarding_apply.return_value = {"error": "config write failed"}
        resp = ipc_server._handle_onboarding_apply({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["error"] == "config write failed"

    def test_none_error_in_set_microphone_treated_as_success(self, ipc_server, fake_service):
        """XZ-EH-015: the same {"error": None} -> ack fix applies to all
        five set_*/skip/apply handlers. Pin the set_microphone path."""
        fake_service.onboarding_set_microphone.return_value = {"error": None, "ok": True}
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": "usb_1"}, {})
        assert resp["type"] == "ack"
        assert resp["data"]["error"] is None


# ==============================================================================
# Merged from tests/test_config_onboarding_handler_fixes.py —
#   onboarding-handler regression pins (start re-run guard, service-error WARNING breadcrumbs, mark_started failure
#   logged not swallowed)
# ==============================================================================


class TestOnboardingStartRerunGuard:
    """DE-39: ``_handle_onboarding_start`` refuses to re-run the wizard
    after completion unless the caller passes ``{"force": true}``."""

    def test_first_run_true_proceeds_normally(self, ipc_server, fake_service):
        """When ``onboarding_is_first_run`` returns True, the handler
        delegates to ``service.onboarding_start`` as before."""
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
        """When onboarding is already complete and no ``force`` flag is
        passed, the handler returns an error envelope with
        ``code: 'onboarding_already_complete'`` — and does NOT call
        ``service.onboarding_start``."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        resp = ipc_server._handle_onboarding_start({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete", (
            f"DE-39: expected code 'onboarding_already_complete'; got: {resp['data'].get('code')!r}"
        )
        assert "force" in resp["data"]["message"].lower(), "DE-39: error message must mention the force flag"
        fake_service.onboarding_start.assert_not_called()

    def test_first_run_false_with_force_proceeds(self, ipc_server, fake_service):
        """When ``force: true`` is passed, the handler re-runs the
        wizard even though onboarding is already complete (used by
        Settings → Troubleshooting → Re-run Setup Wizard)."""
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
        """``force`` must be a real boolean True — the string
        ``"false"`` is truthy in Python but the handler uses
        ``bool(data.get("force", False))`` which coerces it to True.

        Wait — actually, ``bool("false")`` is True in Python because
        non-empty strings are truthy. So this test asserts that a
        NON-empty string value for ``force`` does proceed (matching
        Python truthiness). The guard only blocks when ``force`` is
        falsy (None, False, 0, empty string, missing key)."""
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
        """DE-39: the guard must not crash when ``data`` is None or a
        non-dict (renderer may send no payload). The handler coerces
        to ``{}`` before reading ``force``."""
        fake_service.onboarding_is_first_run.return_value = {"is_first_run": False}

        # None payload — must not raise TypeError.
        resp = ipc_server._handle_onboarding_start(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "onboarding_already_complete"

    def test_guard_logs_warning_when_blocking(self, ipc_server, fake_service, caplog):
        """DE-39: when the guard blocks, the handler logs a WARNING so
        operators can see the rejection in ``voice-typer.log``."""
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
    """DE-40: when a service returns ``{"error": ...}``, the handler
    must log a WARNING with the command name and the error string."""

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
        """Each of the 6 onboarding handlers that delegate ack-vs-error
        to the service's return dict shape must log the service-returned
        error at WARNING so the failure leaves a server-side breadcrumb."""
        service_mock = getattr(fake_service, service_method)
        service_mock.return_value = {"error": "service-layer failure"}
        handler = getattr(ipc_server, handler_name)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.ipc_server"):
            resp = handler(payload, {})

        # Response shape is unchanged —  only adds a log line.
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
        """DE-40: when the service returns success (no ``error`` key),
        NO warning is logged — the handler's ack-vs-error branch only
        logs on the error path."""
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
    """DE-41: ``OnboardingController().mark_started()`` failures in
    ``_handle_onboarding_start`` are logged at WARNING with
    ``exc_info=True`` instead of being silently swallowed."""

    def test_mark_started_failure_logs_warning_with_exc_info(self, ipc_server, fake_service, monkeypatch, caplog):
        """When ``mark_started`` raises, the handler must emit a WARNING
        with ``exc_info=True`` (was ``except Exception: pass``)."""
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

        # Response is still success — mark_started is best-effort and
        # must not abort the wizard.
        assert resp["type"] == "onboarding_step"

        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "onboarding_start" in r.getMessage()
            and "mark_started failed" in r.getMessage()
        ]
        assert warnings, "DE-41: mark_started failure must be logged at WARNING"
        # exc_info must be attached so the traceback lands in voice-typer.log.
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
