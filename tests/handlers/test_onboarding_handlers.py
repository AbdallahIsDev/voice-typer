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
- ``_handle_onboarding_set_hotkey`` — validates ``hotkey: str`` (default ``<caps_lock>``).
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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
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
        # DE-36: validator now emits namespaced ``client.missing_field``.
        # ``legacy_code`` carries the bare form for one release cycle so
        # older renderer error-handling code that switches on the bare
        # string keeps working until the migration completes.
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["legacy_code"] == "missing_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.onboarding_set_microphone.assert_not_called()

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_set_microphone({"mic_id": 123}, {})
        assert resp["type"] == "error"
        # DE-36: namespaced ``client.invalid_field`` (legacy bare form
        # preserved in ``legacy_code``).
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["legacy_code"] == "invalid_field"
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
        # DE-36: namespaced ``client.invalid_field`` (legacy bare form
        # preserved in ``legacy_code``).
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["legacy_code"] == "invalid_field"
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
        # DE-36: namespaced ``client.invalid_field`` (legacy bare form
        # preserved in ``legacy_code``).
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["legacy_code"] == "invalid_field"
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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# ── FIX-11: new onboarding IPC handlers (UX-32 / UX-4 / UX-27) ────────


class TestOnboardingGetModelCatalogHandler:
    """``_handle_onboarding_get_model_catalog`` — UX-32.

    Returns the full ``model_registry`` catalog (rich metadata) so
    the wizard's Model step can render every variant with VRAM /
    language / speed / accuracy badges, instead of being limited to
    the curated ``MODEL_OPTIONS`` subset.

    Unlike most onboarding handlers, this does NOT delegate to
    ``self.service`` — the catalog is pure static metadata shared
    with the Models page's ``get_model_catalog`` IPC.
    """

    def test_happy_path_returns_onboarding_model_catalog(self, ipc_server, fake_service):
        resp = ipc_server._handle_onboarding_get_model_catalog({}, {})
        assert resp["type"] == "onboarding_model_catalog"
        models = resp["data"]["models"]
        assert isinstance(models, list)
        assert len(models) > 0
        # Catalog is a superset of MODEL_OPTIONS — must include the
        # English-only Whisper variants plus multilingual, distilled,
        # turbo, Parakeet.
        names = {m["name"] for m in models}
        assert "tiny.en" in names
        assert "small.en" in names
        assert "parakeet" in names

    def test_response_includes_rich_metadata_fields(self, ipc_server, fake_service):
        """UX-32 / UX-13: catalog entries must carry rich metadata
        fields (download_size_mb, required_vram_mb, backend,
        multilingual, supported_languages, repo_id, speed_rating,
        accuracy_rating) so the renderer can render badges."""
        resp = ipc_server._handle_onboarding_get_model_catalog({}, {})
        models = resp["data"]["models"]
        required_fields = {
            "name",
            "download_size_mb",
            "required_vram_mb",
            "backend",
            "multilingual",
            "supported_languages",
            "repo_id",
            "speed_rating",
            "accuracy_rating",
        }
        for entry in models:
            missing = required_fields - set(entry.keys())
            assert not missing, f"catalog entry {entry.get('name')!r} missing: {missing}"

    def test_does_not_call_service(self, ipc_server, fake_service):
        """The handler must NOT delegate to ``self.service`` — the
        catalog is pure static metadata from ``model_registry``."""
        fake_service.onboarding_get_model_options.reset_mock()
        fake_service.onboarding_get_model_catalog = None  # type: ignore[attr-defined]
        ipc_server._handle_onboarding_get_model_catalog({}, {})
        # Verify the service was never asked for model options.
        fake_service.onboarding_get_model_options.assert_not_called()

    def test_registry_failure_returns_error(self, ipc_server, fake_service, monkeypatch):
        """If the catalog lookup raises, the handler returns an
        ``error`` response (rather than propagating the exception)."""
        # Force OnboardingController.get_model_catalog to raise.
        from voice_typer.server import onboarding as onboarding_mod

        def _boom():
            raise RuntimeError("registry corrupted")

        monkeypatch.setattr(onboarding_mod.OnboardingController, "get_model_catalog", _boom)
        resp = ipc_server._handle_onboarding_get_model_catalog({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak) —
        # the handler no longer surfaces "registry corrupted" to the
        # renderer; the full traceback lands in voice-typer.log via
        # ``_respond_with_error``'s ``log.error(..., exc_info=True)``.
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


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
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
