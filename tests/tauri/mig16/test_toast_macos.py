"""toast notification wiring validation (macOS)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
MAIN_RS = _SRC_TAURI / "src" / "main.rs"
WS_RS = _SRC_TAURI / "src" / "sidecar" / "ws.rs"
TAURI_CONF_JSON = _SRC_TAURI / "tauri.conf.json"
CAPABILITIES_JSON = (
    _SRC_TAURI / "capabilities" / "main-runtime.json"
)  # migrate-runtime split; notification perms live in main-runtime
ENTITLEMENTS_PLIST = _SRC_TAURI / "entitlements.plist"
SYSTEM_HANDLERS_PY = _REPO_ROOT / "voice_typer" / "server" / "handlers" / "system_handlers.py"
MACOS_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "macos-validation-runbook.md"


def _read(path: Path) -> str:
    """Read a file as UTF-8 text. Fail loud if missing, every path this"""
    assert path.is_file(), f"required macOS toast-wiring artifact missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_ws_bridge_rs() -> str:
    """Read the Rust WS bridge source: ws.rs plus its ws/reader.rs /"""
    parts = [_read(WS_RS)]
    for name in ("reader.rs", "writer.rs"):
        parts.append(_read(WS_RS.parent / "ws" / name))
    return "\n\n".join(parts)


class TestMainRsRegistersNotificationPlugin:
    """Gate 1: the Rust host must register the notification plugin."""

    def test_main_rs_registers_tauri_plugin_notification_init(self):
        """``main.rs`` must contain a ``.plugin(tauri_plugin_notification::init())``"""
        src = _read(MAIN_RS)
        assert "tauri_plugin_notification::init()" in src, (
            "main.rs must register tauri_plugin_notification::init() in the "
            "Builder chain, without it, the webview's notification invoke() "
            "calls fail with 'plugin not registered' on macOS."
        )

    def test_main_rs_notification_plugin_is_in_builder_chain(self):
        """Belt-and-braces: the ``tauri_plugin_notification::init()`` call"""
        src = _read(MAIN_RS)
        # Look for the actual call form: `.plugin(tauri_plugin_notification::init())`
        assert ".plugin(tauri_plugin_notification::init())" in src, (
            "tauri_plugin_notification::init() must be called via .plugin(...) "
            "inside the tauri::Builder::default() chain in main.rs."
        )


class TestTauriConfDeclaresNotificationPlugin:
    """Gate 2: tauri.conf.json must list ``notification`` in the plugins section."""

    def test_tauri_conf_json_has_notification_in_plugins(self):
        """``tauri.conf.json`` MUST declare a ``notification`` entry under"""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        assert "plugins" in conf, (
            "tauri.conf.json must have a top-level 'plugins' object, Tauri v2 generates JS bindings from this section."
        )
        assert "notification" in conf["plugins"], (
            "tauri.conf.json plugins section must declare 'notification', "
            "without it, the @tauri-apps/plugin-notification JS bindings "
            "are not generated and the webview's notify() call fails on macOS."
        )

    def test_notification_plugin_config_is_unit_compatible(self):
        """The ``notification`` plugin config MUST be serde-unit compatible"""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        notif_cfg = conf["plugins"]["notification"]
        assert notif_cfg is None, (
            f"tauri.conf.json plugins.notification must be null (serde unit), got "
            f"{type(notif_cfg).__name__}: {notif_cfg!r}, a non-null value fails "
            "app startup with 'invalid type: map, expected unit'"
        )


class TestCapabilitiesGrantNotificationPermission:
    """Gate 3: the main-runtime capability must grant a notification permission."""

    def test_capabilities_grants_notification_allow_notify_or_default(self):
        """The ``main-runtime`` capability MUST grant at least one"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        assert "permissions" in cap, "main-runtime.json must declare a 'permissions' array."
        perms = cap["permissions"]
        assert isinstance(perms, list), f"capabilities 'permissions' must be a list, got {type(perms).__name__}"
        notif_perms = [p for p in perms if isinstance(p, str) and p.startswith("notification:")]
        assert notif_perms, (
            f"main-runtime.json must grant at least one 'notification:*' "
            f"permission, found none in {perms!r}. Without this, the "
            f"webview's notify() call returns PermissionDenied on macOS."
        )

    def test_capabilities_grants_notification_allow_notify_specifically(self):
        """The macOS runbook §6.4 specifically calls out"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-notify" in perms, (
            f"main-runtime.json must grant 'notification:allow-notify' "
            f"(per macOS runbook §6.4 pass criteria). Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )

    def test_capabilities_grants_notification_request_permission(self):
        """Belt-and-braces: the capability also grants"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-request-permission" in perms, (
            "main-runtime.json must grant 'notification:allow-request-permission' "
            "so the renderer can trigger the macOS UNUserNotificationCenter "
            "authorization prompt on first launch."
        )


class TestWsRsNotificationEventName:
    """Gate 4: the WS reader emits the canonical ``notification`` event."""

    def test_ws_rs_emits_canonical_notification_event(self):
        """``ws.rs`` emits the canonical ``notification`` event type."""
        src = _read_ws_bridge_rs()
        assert 'emit("notification"' in src or "notification" in src, (
            "ws.rs must emit the canonical 'notification' event type."
        )

    def test_ws_rs_alias_branch_emits_notification_with_payload(self):
        """The notification event must be emitted WITH the payload"""
        src = _read_ws_bridge_rs()
        assert "emit(emit_name, payload.clone())" in src, (
            "ws.rs must emit the event WITH the payload (payload.clone()), "
            "not just an empty event, otherwise the macOS banner renders blank."
        )


class TestNotificationPayloadShape:
    """
    Gate 5: the published notification payload shape.
    Do Not Disturb IF the user has granted critical-alert permission;
    """

    def test_payload_shape_matches_actual_implementation(self):
        """``event_bus.publish`` being called with the canonical payload"""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {
                    "title": "Transcription complete",
                    "message": "Inserted 42 words.",
                    "duration_ms": 4000,
                    "critical": False,
                },
                {},
            )
        # Top-level shape.
        assert set(captured.keys()) == {"type", "data"}, (
            f"payload top-level keys must be {{'type', 'data'}}, got {set(captured.keys())!r}"
        )
        assert captured["type"] == "notification", (
            f"event name must be 'notification' (per CR-8), got {captured.get('type')!r}"
        )
        # data shape.
        data = captured["data"]
        assert set(data.keys()) == {"title", "message", "duration_ms", "critical"}, (
            f"payload data keys must be {{title, message, duration_ms, critical}}, got {set(data.keys())!r}"
        )
        assert data["title"] == "Transcription complete"
        assert data["message"] == "Inserted 42 words."
        assert data["duration_ms"] == 4000
        assert data["critical"] is False

    def test_payload_uses_message_field_not_body(self):
        """
        The body field MUST be named ``message`` (NOT ``body``).
        This pins the actual implementation's field name. The renderer's
        """
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {"title": "T", "message": "M"},
                {},
            )
        assert "message" in captured["data"], (
            "payload data must have a 'message' field, this is the field "
            "name the renderer reads when calling tauri-plugin-notification's "
            "notify({title, body}) on macOS."
        )
        assert "body" not in captured["data"], (
            "payload data must NOT have a 'body' field, the actual "
            "implementation uses 'message' (the renderer maps data.message "
            "→ notify body)."
        )

    def test_payload_defaults_when_data_empty(self):
        """Empty ``data: {}`` MUST still produce a well-formed payload"""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {},
                {},
            )
        assert captured["type"] == "notification"
        # APP_NAME is "Voice Typer" per voice_typer/server/branding.py.
        assert captured["data"]["title"] == "Voice Typer"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False

    def test_payload_critical_field_is_bool_not_string(self):
        """JSON-serializes to ``true``/``false``), NOT a string"""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {"title": "T", "message": "M", "critical": True},
                {},
            )
        assert isinstance(captured["data"]["critical"], bool), (
            f"payload 'critical' must be a bool (so JSON-serializes to "
            f"true/false), got {type(captured['data']['critical']).__name__}. "
            f"A string 'true' would always be truthy in JS, escalating every "
            f"notification to critical on macOS."
        )

    def test_payload_duration_ms_field_is_int_not_string(self):
        """JSON-serializes to a number), NOT a string."""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {"title": "T", "message": "M", "duration_ms": 3000},
                {},
            )
        assert isinstance(captured["data"]["duration_ms"], int), (
            f"payload 'duration_ms' must be an int (so JSON-serializes to a "
            f"number), got {type(captured['data']['duration_ms']).__name__}."
        )


class TestMacOSNotificationsRequireSigning:
    """Gate 6: macOS notifications require the app to be SIGNED."""

    def test_entitlements_plist_exists(self):
        """The ``entitlements.plist`` file MUST exist in the repo —"""
        assert ENTITLEMENTS_PLIST.is_file(), (
            f"entitlements.plist MUST exist at {ENTITLEMENTS_PLIST}, "
            f"it's consumed by codesign when signing the .app bundle. "
            f"Without signing, macOS notifications silently fail."
        )

    def test_signing_guide_documents_developer_id_requirement(self):
        """The signing guide MUST document the Developer ID Application"""
        signing_guide = _REPO_ROOT / "docs" / "migration" / "signing-guide.md"
        assert signing_guide.is_file(), (
            f"signing-guide.md MUST exist at {signing_guide}, it documents "
            f"the macOS Developer ID signing flow required for notifications."
        )
        src = _read(signing_guide)
        assert "Developer ID" in src, (
            "signing-guide.md MUST mention 'Developer ID', the Developer ID "
            "Application certificate is required for macOS notifications on "
            "macOS 13+ (unsigned dev builds silently fail to post)."
        )

    def test_macos_runbook_documents_signing_prerequisite(self):
        """
        The macOS validation runbook MUST list the Developer ID
        This pins the docs-side contract: a developer running gate
        """
        src = _read(MACOS_RUNBOOK)
        assert "Developer ID" in src, (
            "macos-validation-runbook.md MUST mention 'Developer ID' in its "
            "prerequisites, gate check 6 (toast) requires a signed .app bundle."
        )


class TestEntitlementsDoNotIncludeNotificationEntitlement:
    """Gate 7: the entitlements.plist does NOT need a notification-specific"""

    def test_entitlements_does_not_contain_notification_entitlement(self):
        """The ``entitlements.plist`` MUST NOT contain any of:"""
        src = _read(ENTITLEMENTS_PLIST)
        forbidden_keys = [
            "com.apple.developer.usernotifications.communication",
            "com.apple.developer.usernotifications.time-sensitive",
            "aps-environment",
        ]
        for key in forbidden_keys:
            assert key not in src, (
                f"entitlements.plist MUST NOT contain '{key}', this is an "
                f"iOS-only or APNs-only entitlement. Local macOS notifications "
                f"via UNUserNotificationCenter need NO notification-specific "
                f"entitlement; the existing audio-input + allow-jit + "
                f"disable-library-validation entitlements are sufficient. "
                f"Adding it is cargo-cult and makes the file harder to audit."
            )

    def test_entitlements_contains_required_hardened_runtime_keys(self):
        """three hardened-runtime entitlements required for notarization"""
        src = _read(ENTITLEMENTS_PLIST)
        required_keys = [
            "com.apple.security.cs.allow-jit",
            "com.apple.security.cs.disable-library-validation",
            "com.apple.security.device.audio-input",
        ]
        for key in required_keys:
            assert key in src, (
                f"entitlements.plist MUST contain '{key}', it's a "
                f"hardened-runtime minimum required for notarization (per "
                f"ADR-0020 §13.2). Without it, the signing step fails, "
                f"which means macOS notifications silently fail too."
            )


class TestMacOSNotificationPermissionGrant:
    """Gate 8: macOS 13+ (Ventura and newer) requires the user to grant"""

    def test_macos_runbook_documents_unusernotificationcenter_authorization(self):
        """The macOS runbook §6.4 MUST mention"""
        src = _read(MACOS_RUNBOOK)
        assert "UNUserNotificationCenter" in src, (
            "macos-validation-runbook.md MUST mention 'UNUserNotificationCenter' "
            "— this is the macOS API surface that requires user authorization "
            "for notifications on macOS 11+."
        )

    def test_macos_runbook_documents_system_settings_notifications_fallback(self):
        """The macOS runbook MUST document the manual fallback path"""
        src = _read(MACOS_RUNBOOK)
        src_lower = src.lower()
        assert "system settings" in src_lower or "system preferences" in src_lower, (
            "macos-validation-runbook.md MUST mention 'System Settings' (or "
            "the legacy 'System Preferences'), the macOS UI path where the "
            "user manually grants notification permission."
        )
        assert "notification" in src_lower, (
            "macos-validation-runbook.md MUST mention 'notification', the "
            "System Settings panel where the user grants per-app notification "
            "permission on macOS 13+."
        )

    def test_macos_runbook_documents_notification_permission_pass_criteria(self):
        """the notification gate (a banner appears in the top-right"""
        src = _read(MACOS_RUNBOOK)
        # The runbook should mention "top-right" (where macOS banners
        assert "top-right" in src or "top right" in src, (
            "macos-validation-runbook.md MUST mention 'top-right', the "
            "screen position where macOS notification banners appear. This "
            "is the human-verifiable pass criteria for gate check 6."
        )

    def test_macos_runbook_documents_nsusernotificationsusagedescription(self):
        """``NSUserNotificationsUsageDescription`` Info.plist key. This"""
        src = _read(MACOS_RUNBOOK)
        assert "NSUserNotificationsUsageDescription" in src, (
            "macos-validation-runbook.md MUST mention "
            "'NSUserNotificationsUsageDescription', the Info.plist key "
            "that provides the human-readable description for the macOS "
            "TCC notification authorization prompt."
        )


class TestSourceInspectionBeltAndBraces:
    """
    Gate 9: belt-and-braces source-inspection tests.
    These don't correspond to a single gate point, they pin additional
    """

    def test_ws_rs_emits_python_event_envelope(self):
        """``ws.rs`` MUST also emit the generic ``python-event``"""
        src = _read_ws_bridge_rs()
        assert 'emit("python-event"' in src, (
            "ws.rs must also emit the generic 'python-event' envelope "
            "(ADR-0020 §6.3), this is the catch-all path the usePython "
            "hook uses to learn about notification events on macOS."
        )

    def test_ws_rs_emits_specific_event_with_emit_name(self):
        """``ws.rs`` MUST emit the specific event (using ``emit_name``)"""
        src = _read_ws_bridge_rs()
        assert "emit(emit_name" in src, (
            "ws.rs must emit the specific event using `emit_name` (the "
            "result of the match arm), this is what carries the canonical "
            "'notification' name to direct UI listeners on macOS."
        )

    def test_ws_rs_has_other_arm_passthrough(self):
        """``ws.rs`` forwards every event type through"""
        src = _read_ws_bridge_rs()
        assert re.search(r"let\s+emit_name\s*=\s*translate_event_name\s*\(\s*event_type\s*\)\s*;", src), (
            "ws.rs must forward every event type via `let emit_name = translate_event_name(event_type);`."
        )

    def test_system_handlers_publishes_notification_event(self):
        """The Python sidecar's ``system_handlers.py`` publishes a"""
        src = _read(SYSTEM_HANDLERS_PY)
        assert '"type": "notification"' in src, "system_handlers.py MUST publish with type='notification' (per CR-8)."

    def test_macos_runbook_lists_toast_as_gate_point(self):
        """a numbered gate point (§6.4: \"tauri-plugin-notification posts"""
        src = _read(MACOS_RUNBOOK)
        # The runbook's gate-point header for notifications:
        assert "tauri-plugin-notification" in src, (
            "macos-validation-runbook.md MUST mention 'tauri-plugin-notification' "
            "as a gate-point header, gate check 6 is the toast gate."
        )
        assert "gate point 5" in src, (
            "macos-validation-runbook.md MUST label the toast gate as "
            "'gate point 5' (per ADR-0020 §6.4, the gate-point numbering "
            "is the canonical reference for the 9 Phase 0-M gates)."
        )


class TestValidateOnMacOSHostBlock:
    """Gate 10: the VALIDATE ON MACOS HOST block (in this file's module"""

    @staticmethod
    def _module_docstring() -> str:
        """Return the module-level docstring of this file."""
        return __doc__ or ""

    def test_docstring_contains_validate_on_macos_host_header(self):
        """``VALIDATE ON MACOS HOST:`` header, this is the canonical"""
        doc = self._module_docstring()
        assert "VALIDATE ON MACOS HOST:" in doc, (
            "Module docstring MUST contain 'VALIDATE ON MACOS HOST:' header, "
            "this is the canonical marker the macOS host validator scans for."
        )

    def test_docstring_documents_signing_prerequisite(self):
        """The VALIDATE ON MACOS HOST block MUST mention the Developer"""
        doc = self._module_docstring()
        assert "Developer ID" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'Developer ID', "
            "unsigned dev builds silently fail to post notifications on macOS."
        )
        assert "signed" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'signed', the signing prerequisite for macOS notifications."
        )

    def test_docstring_documents_system_settings_fallback(self):
        """The VALIDATE ON MACOS HOST block MUST document the System"""
        doc = self._module_docstring()
        assert "System Settings" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'System Settings', "
            "the macOS UI path where the user manually grants notification "
            "permission if the TCC prompt was dismissed."
        )
        assert "Notifications" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'Notifications', the "
            "System Settings panel name for per-app notification permission."
        )

    def test_docstring_documents_log_path(self):
        """The VALIDATE ON MACOS HOST block MUST document the expected"""
        doc = self._module_docstring()
        assert "~/Library/Logs/voice-typer/voice-typer.log" in doc, (
            "VALIDATE ON MACOS HOST block MUST document the macOS log path "
            "(~/Library/Logs/voice-typer/voice-typer.log) so the validator "
            "can confirm the notification event was emitted."
        )

    def test_docstring_documents_unsigned_dev_build_caveat(self):
        """The VALIDATE ON MACOS HOST block MUST document the unsigned-"""
        doc = self._module_docstring()
        assert "Unsigned dev builds" in doc, (
            "VALIDATE ON MACOS HOST block MUST document the unsigned-dev-build "
            "caveat ('Unsigned dev builds may not show notifications, sign "
            "with Developer ID first.'), this is the troubleshooting hint "
            "for the most common silent-failure mode on macOS."
        )

    def test_docstring_documents_expected_timing(self):
        """The VALIDATE ON MACOS HOST block MUST document the expected"""
        doc = self._module_docstring()
        assert "within 1s" in doc, (
            "VALIDATE ON MACOS HOST block MUST document the expected timing "
            "('within 1s'), the upper bound for how long the validator should "
            "wait for the banner before declaring the gate failed."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
