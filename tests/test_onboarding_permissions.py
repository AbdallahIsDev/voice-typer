"""Tests for ``onboarding_check_permissions`` and ``onboarding_set_microphone`` (CR-10 / CR-64)."""

from __future__ import annotations

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

# ``onboarding_check_permissions`` returns a permission-state dict ──


class TestOnboardingCheckPermissions:
    """``_handle_onboarding_check_permissions`` (CR-10)."""

    def test_check_permissions_returns_state(self):
        """``onboarding_check_permissions`` must return a permission"""
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})

        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert set(data.keys()) == {"platform", "state", "needed", "instructions"}
        assert data["platform"] in {"windows", "macos", "linux", "unknown"}
        assert data["state"] in {"granted", "denied", "unknown"}
        assert isinstance(data["needed"], bool)
        # ``instructions`` is either ``None`` (no permission needed /
        if data["instructions"] is not None:
            # (session NH): server returns i18n keys (title_key /
            assert "title_key" in data["instructions"] or "title" in data["instructions"]
            assert "steps_keys" in data["instructions"] or "steps" in data["instructions"]
            assert "commands" in data["instructions"]

    def test_check_permissions_does_not_invoke_service(self):
        """:mod:`voice_typer.server.permissions` (via"""
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        server._handle_onboarding_check_permissions({}, {})
        # The fake_service is a MagicMock, any attribute access
        service_calls = [c for c in fake_service.mock_calls if "onboarding_check_permissions" in str(c)]
        assert service_calls == []

    def test_check_permissions_linux_denied_returns_instructions(self, monkeypatch):
        """On Linux with permission denied, the handler must return"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: False)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: True)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.DENIED,
        )

        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})

        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert data["platform"] == "linux"
        assert data["state"] == "denied"
        assert data["needed"] is True
        assert data["instructions"] is not None
        import json
        from pathlib import Path

        _en_path = (
            Path(__file__).parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "i18n"
            / "translations"
            / "en.json"
        )
        _en = json.loads(_en_path.read_text(encoding="utf-8"))

        def _flat(d, p=""):
            out = {}
            for k, v in d.items():
                key = f"{p}.{k}" if p else k
                if isinstance(v, dict):
                    out.update(_flat(v, key))
                else:
                    out[key] = v
            return out

        _en_flat = _flat(_en)
        _steps_keys = data["instructions"].get("steps_keys") or data["instructions"].get("steps") or []
        _resolved = [_en_flat.get(k, k) for k in _steps_keys]
        _joined = " ".join(_resolved).lower()
        _cmds = " ".join(data["instructions"].get("commands") or []).lower()
        assert "input" in _joined or "udev" in (_joined + " " + _cmds)

    def test_check_permissions_macos_denied_embeds_runtime_bundle_id(self, monkeypatch):
        """On macOS with permission denied, the walkthrough's ``commands``"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: True)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.DENIED,
        )
        monkeypatch.setattr(
            "voice_typer.server.onboarding.resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
        )

        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})

        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert data["platform"] == "macos"
        assert data["needed"] is True
        assert data["instructions"] is not None
        assert data["instructions"]["commands"] == ["tccutil reset Accessibility com.voicetyper.desktop"]

    def test_check_permissions_macos_denied_embeds_any_runtime_bundle_id(self, monkeypatch):
        """The command must follow the resolved value, not a fixed one —"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: True)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.DENIED,
        )
        monkeypatch.setattr(
            "voice_typer.server.onboarding.resolve_host_bundle_id",
            lambda: "com.voicetyper.some-other-build",
        )

        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})
        data = resp["data"]

        assert data["instructions"]["commands"] == ["tccutil reset Accessibility com.voicetyper.some-other-build"]

    def test_check_permissions_macos_denied_omits_command_when_unresolved(self, monkeypatch):
        """macOS denied + unresolvable bundle ID → ``commands`` is None"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: True)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.DENIED,
        )
        monkeypatch.setattr(
            "voice_typer.server.onboarding.resolve_host_bundle_id",
            lambda: None,
        )

        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})
        data = resp["data"]

        assert data["platform"] == "macos"
        assert data["needed"] is True
        assert data["instructions"] is not None
        assert data["instructions"]["commands"] is None

    def test_check_permissions_macos_granted_has_no_instructions(self, monkeypatch):
        """macOS with permission already granted → ``needed=False`` and"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: False)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: True)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.GRANTED,
        )

        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})
        data = resp["data"]

        assert data["platform"] == "macos"
        assert data["needed"] is False
        assert data["instructions"] is None

    def test_check_permissions_windows_returns_not_needed(self, monkeypatch):
        """On Windows, no permission is needed: ``needed=False``,"""
        from voice_typer.server import permissions as perm_mod
        from voice_typer.server.permissions import PermissionState

        monkeypatch.setattr(perm_mod, "is_windows", lambda: True)
        monkeypatch.setattr(perm_mod, "is_macos", lambda: False)
        monkeypatch.setattr(perm_mod, "is_linux", lambda: False)
        monkeypatch.setattr(
            perm_mod,
            "check_keyboard_permission",
            lambda: PermissionState.GRANTED,
        )

        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})

        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert data["platform"] == "windows"
        assert data["needed"] is False
        assert data["instructions"] is None


# ``onboarding_set_microphone`` accepts ``mic_id=None`` ────────────


class TestOnboardingSetMicrophoneAcceptsNull:
    """``_handle_onboarding_set_microphone`` (CR-64)."""

    def test_set_microphone_accepts_none(self):
        """``onboarding_set_microphone`` must accept ``mic_id=None``"""
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        fake_service.onboarding_set_microphone.return_value = {"ok": True}

        resp = server._handle_onboarding_set_microphone({"mic_id": None}, {})

        # The validator must NOT have returned an ``invalid_field``
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True}
        fake_service.onboarding_set_microphone.assert_called_once_with(None)

    def test_set_microphone_accepts_string(self):
        """Regression guard: the validator must still accept a regular"""
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        fake_service.onboarding_set_microphone.return_value = {"ok": True}

        resp = server._handle_onboarding_set_microphone({"mic_id": "usb-mic-1"}, {})

        assert resp["type"] == "ack"
        fake_service.onboarding_set_microphone.assert_called_once_with("usb-mic-1")

    def test_set_microphone_rejects_int(self):
        """Regression guard: ``mic_id=123`` (int) must still be"""
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_set_microphone({"mic_id": 123}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field" or resp["data"].get("legacy_code") == "invalid_field"
        assert resp["data"]["field"] == "mic_id"

    def test_set_microphone_rejects_list(self):
        """Regression guard: ``mic_id=[\"a\", \"b\"]`` (list) must still"""
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_set_microphone({"mic_id": ["a", "b"]}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field" or resp["data"].get("legacy_code") == "invalid_field"
        assert resp["data"]["field"] == "mic_id"

    def test_set_microphone_missing_field_returns_missing_field_error(self):
        """Regression guard: empty payload must still surface"""
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_set_microphone({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field" or resp["data"].get("legacy_code") == "missing_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.onboarding_set_microphone.assert_not_called()

    def test_set_microphone_none_propagates_to_controller(self, tmp_config_dir):
        """End-to-end check: ``mic_id=None`` flows through the IPC"""
        from voice_typer.server.onboarding import OnboardingController
        from voice_typer.server.service import VoiceTyperService

        service = VoiceTyperService.__new__(VoiceTyperService)
        service._onboarding = OnboardingController(config_dir=tmp_config_dir)  # type: ignore[attr-defined]

        server, _fake_app, _ = make_ipc_server_with_fakes()
        # Inject the real (minimal) service so the call flows through
        server.service = service

        resp = server._handle_onboarding_set_microphone({"mic_id": None}, {})

        assert resp["type"] == "ack"
        # Verify the controller stored ``None`` (not coerced to "").
        ctrl = service._onboarding  # type: ignore[attr-defined]
        assert ctrl is not None
        assert ctrl.selected_microphone is None
