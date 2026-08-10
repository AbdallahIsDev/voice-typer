"""Tests for ``onboarding_check_permissions`` and ``onboarding_set_microphone`` (CR-10 / CR-64).

CR-10: the backend ``OnboardingController`` was extended to a 6-step
flow (``Welcome → Microphone → Permissions → Hotkey → Model → Done``)
with a new ``onboarding_check_permissions`` IPC handler. These tests
verify the handler returns a well-formed permission-state dict
(``platform`` / ``state`` / ``needed`` / ``instructions``) so the
renderer's new Permissions step can render the right platform-specific
walkthrough (macOS Accessibility / Linux input group + udev rule).

CR-64: the renderer sends ``mic_id: null`` when no microphone is
detected. The validator previously rejected anything other than
``str``, surfacing an ``invalid_field`` error and blocking the wizard.
These tests verify the validator now accepts ``None`` (the
``OnboardingController.set_microphone`` stores it verbatim and
``apply_settings`` skips writing the microphone config key —
preserving the default).

These tests live at the ``tests/`` root (not ``tests/handlers/``)
because they cover TWO concerns (CR-10 + CR-64) that span the
handler mixin and the validator. The handler-mixin unit tests in
``tests/handlers/test_onboarding_handlers.py`` already cover the
other 12 onboarding_* handlers; this file adds the two new
behaviours introduced by CR-10 / CR-64.
"""

from __future__ import annotations

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

# ``onboarding_check_permissions`` returns a permission-state dict ──


class TestOnboardingCheckPermissions:
    """``_handle_onboarding_check_permissions`` (CR-10).

    The handler delegates to
    :meth:`OnboardingController.check_permissions`, which returns a
    renderer-friendly dict describing the current platform, whether
    permission is still needed, and (on macOS / Linux) the setup
    walkthrough. The frontend's new Permissions step calls this on
    entry so it can show the right instructions.
    """

    def test_check_permissions_returns_state(self):
        """``onboarding_check_permissions`` must return a permission
        state dict with the four documented keys
        (``platform`` / ``state`` / ``needed`` / ``instructions``).

        The probe runs against the real host platform (Linux in the
        sandbox); on Linux it returns either ``state="granted"`` (if
        the test runner is already in the ``input`` group) or
        ``state="denied"`` (if not). We don't pin the value — we just
        assert the dict shape and that ``needed`` is a bool.
        """
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_check_permissions({}, {})

        assert resp["type"] == "onboarding_permissions"
        data = resp["data"]
        assert set(data.keys()) == {"platform", "state", "needed", "instructions"}
        assert data["platform"] in {"windows", "macos", "linux", "unknown"}
        assert data["state"] in {"granted", "denied", "unknown"}
        assert isinstance(data["needed"], bool)
        # ``instructions`` is either ``None`` (no permission needed /
        # Windows / unknown) or a dict with title / steps / commands
        # (macOS / Linux when permission is still needed).
        if data["instructions"] is not None:
            # (session NH): server returns i18n keys (title_key /
            # steps_keys) for the renderer to localize. Accept either the
            # i18n-key form or the legacy literal form.
            assert "title_key" in data["instructions"] or "title" in data["instructions"]
            assert "steps_keys" in data["instructions"] or "steps" in data["instructions"]
            assert "commands" in data["instructions"]

    def test_check_permissions_does_not_invoke_service(self):
        """The handler must NOT delegate to ``self.service`` — the
        permission probe lives in
        :mod:`voice_typer.server.permissions` (via
        :meth:`OnboardingController.check_permissions`) and is shared
        with the hotkey-adapter runtime path.

        If the handler tried to call
        ``self.service.onboarding_check_permissions``, the service
        mock would auto-create the attribute and the call would be
        recorded. We assert no such call was made.
        """
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        server._handle_onboarding_check_permissions({}, {})
        # The fake_service is a MagicMock — any attribute access
        # auto-creates a child mock, but only CALLS are recorded in
        # ``mock_calls``. We assert ``onboarding_check_permissions``
        # was never called on the service.
        service_calls = [c for c in fake_service.mock_calls if "onboarding_check_permissions" in str(c)]
        assert service_calls == []

    def test_check_permissions_linux_denied_returns_instructions(self, monkeypatch):
        """On Linux with permission denied, the handler must return
        the input-group + udev-rule walkthrough (UX-27)."""
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
        # (session NH): server returns i18n keys (steps_keys) for the
        # renderer to localize. Resolve them via en.json before checking.
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
        """On macOS with permission denied, the walkthrough's ``commands``
        carry the RUNTIME-resolved bundle ID (never a hardcoded one) —
        mirroring ``startup_tasks.py``'s a11y re-grant notification."""
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
        """The command must follow the resolved value, not a fixed one —
        the whole point of runtime resolution (e.g. a future Tauri
        build with a different identifier)."""
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
        """macOS denied + unresolvable bundle ID → ``commands`` is None
        (a wrong bundle ID in a tccutil command is worse than no
        command)."""
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
        """macOS with permission already granted → ``needed=False`` and
        ``instructions=None`` (no walkthrough, no bundle-ID probe)."""
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
        """On Windows, no permission is needed — ``needed=False``,
        ``instructions=None`` (UX-4 auto-pass branch)."""
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
    """``_handle_onboarding_set_microphone`` (CR-64).

    The renderer sends ``mic_id: null`` when no microphones are
    detected. The validator previously rejected anything other than
    ``str`` (CR-64 fix: accept ``str | NoneType``). The handler then
    passes ``None`` through to ``OnboardingController.set_microphone``,
    which stores it verbatim; ``apply_settings`` later skips writing
    the microphone config key when ``selected_microphone is None``,
    preserving the default.
    """

    def test_set_microphone_accepts_none(self):
        """``onboarding_set_microphone`` must accept ``mic_id=None``
        (the "no microphone detected" case) without returning a
        validation error.

        CR-64 fix: the validator's ``type`` was widened from
        ``str`` to ``(str, type(None))``.
        """
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        fake_service.onboarding_set_microphone.return_value = {"ok": True}

        resp = server._handle_onboarding_set_microphone({"mic_id": None}, {})

        # The validator must NOT have returned an ``invalid_field``
        # error for ``mic_id=None``. The handler should have called
        # the service with ``None`` and returned an ``ack`` response.
        assert resp["type"] == "ack"
        assert resp["data"] == {"ok": True}
        fake_service.onboarding_set_microphone.assert_called_once_with(None)

    def test_set_microphone_accepts_string(self):
        """Regression guard: the validator must still accept a regular
        string ``mic_id`` (the normal "microphone selected" case)."""
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        fake_service.onboarding_set_microphone.return_value = {"ok": True}

        resp = server._handle_onboarding_set_microphone({"mic_id": "usb-mic-1"}, {})

        assert resp["type"] == "ack"
        fake_service.onboarding_set_microphone.assert_called_once_with("usb-mic-1")

    def test_set_microphone_rejects_int(self):
        """Regression guard: ``mic_id=123`` (int) must still be
        rejected — the validator accepts ``str | None`` only."""
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_set_microphone({"mic_id": 123}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field" or resp["data"].get("legacy_code") == "invalid_field"
        assert resp["data"]["field"] == "mic_id"

    def test_set_microphone_rejects_list(self):
        """Regression guard: ``mic_id=["a", "b"]`` (list) must still
        be rejected."""
        server, _fake_app, _fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_set_microphone({"mic_id": ["a", "b"]}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field" or resp["data"].get("legacy_code") == "invalid_field"
        assert resp["data"]["field"] == "mic_id"

    def test_set_microphone_missing_field_returns_missing_field_error(self):
        """Regression guard: empty payload must still surface
        ``missing_field`` (the field is ``required: True``)."""
        server, _fake_app, fake_service = make_ipc_server_with_fakes()
        resp = server._handle_onboarding_set_microphone({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field" or resp["data"].get("legacy_code") == "missing_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.onboarding_set_microphone.assert_not_called()

    def test_set_microphone_none_propagates_to_controller(self, tmp_config_dir):
        """End-to-end check: ``mic_id=None`` flows through the IPC
        handler → service → ``OnboardingController.set_microphone``,
        and the controller stores ``None`` verbatim
        (``apply_settings`` later skips writing the microphone config
        key when ``selected_microphone is None``).

        This guards against a future refactor that e.g. coerces
        ``None`` to ``""`` (which would then be written to the config
        as an empty string, breaking the default-mic fallback).
        """
        from voice_typer.server.onboarding import OnboardingController
        from voice_typer.server.service import VoiceTyperService

        service = VoiceTyperService.__new__(VoiceTyperService)
        # ``onboarding_set_microphone`` requires the controller to
        # already exist (only ``onboarding_start`` and
        # ``onboarding_check_permissions`` lazy-create it). Mirror what
        # ``onboarding_start`` does: instantiate a controller up-front.
        service._onboarding = OnboardingController(config_dir=tmp_config_dir)  # type: ignore[attr-defined]

        server, _fake_app, _ = make_ipc_server_with_fakes()
        # Inject the real (minimal) service so the call flows through
        # to OnboardingController.set_microphone.
        server.service = service

        resp = server._handle_onboarding_set_microphone({"mic_id": None}, {})

        assert resp["type"] == "ack"
        # Verify the controller stored ``None`` (not coerced to "").
        ctrl = service._onboarding  # type: ignore[attr-defined]
        assert ctrl is not None
        assert ctrl.selected_microphone is None
