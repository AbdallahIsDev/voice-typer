"""toast notification wiring validation (Linux)."""

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
POSTINST_SCRIPT = _REPO_ROOT / "scripts" / "linux" / "postinst"
POSTINST_RPM_SCRIPT = _REPO_ROOT / "scripts" / "linux" / "postinst.rpm"
SYSTEM_HANDLERS_PY = _REPO_ROOT / "voice_typer" / "server" / "handlers" / "system_handlers.py"
LINUX_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "linux-validation-runbook.md"


def _read(path: Path) -> str:
    """Read a file as UTF-8 text. Fail loud if missing, every path this"""
    assert path.is_file(), f"required Linux toast-wiring artifact missing: {path}"
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
            "calls fail with 'plugin not registered' on Linux."
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
            "are not generated and the webview's notify() call fails on Linux."
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

    def test_capabilities_grants_notification_default_or_allow_notify(self):
        """The ``main-runtime`` capability MUST grant either"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        assert "permissions" in cap, "main-runtime.json must declare a 'permissions' array."
        perms = cap["permissions"]
        assert isinstance(perms, list), f"capabilities 'permissions' must be a list, got {type(perms).__name__}"
        # Accept either notification:default OR notification:allow-notify.
        has_default = "notification:default" in perms
        has_allow_notify = "notification:allow-notify" in perms
        assert has_default or has_allow_notify, (
            f"main-runtime.json must grant either 'notification:default' OR "
            f"'notification:allow-notify' (per Linux runbook §6.5 / Step 9). "
            f"Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )

    def test_capabilities_grants_notification_allow_notify_specifically(self):
        """The Linux runbook §6.5 / Step 9 \"Common failures\" specifically"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-notify" in perms, (
            f"main-runtime.json must grant 'notification:allow-notify' "
            f"(per Linux runbook §6.5 / Step 9 pass criteria). Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )

    def test_capabilities_grants_notification_request_permission(self):
        """Belt-and-braces: the capability also grants"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-request-permission" in perms, (
            "main-runtime.json must grant 'notification:allow-request-permission' "
            "— the capability file is cross-platform; macOS + Windows paths "
            "need this grant to trigger the TCC / Action Center prompt. On "
            "Linux it's a NO-OP (libnotify has no per-app authorization)."
        )


class TestNotificationPayloadShape:
    """Gate 5: the published notification payload shape."""

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
                {"id": "mig17-toast-shape"},
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
        """The body field MUST be named ``message`` (NOT ``body``)."""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {"title": "T", "message": "M"},
                {"id": "mig17-toast-field-name"},
            )
        assert "message" in captured["data"], (
            "payload data must have a 'message' field, this is the field "
            "name the renderer reads when calling tauri-plugin-notification's "
            "notify({title, body}) on Linux (which becomes the body arg of "
            "the D-Bus org.freedesktop.Notifications.Notify call)."
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
                {"id": "mig17-toast-defaults"},
            )
        assert captured["type"] == "notification"
        # APP_NAME is "Lausu" per voice_typer/server/branding.py.
        assert captured["data"]["title"] == "Lausu"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False

    def test_payload_critical_field_is_bool_not_string(self):
        """
        JSON-serializes to ``true``/``false``), NOT a string
        Do Not Disturb). A string value would always be truthy in JS,
        """
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {"title": "T", "message": "M", "critical": True},
                {"id": "mig17-toast-critical-type"},
            )
        assert isinstance(captured["data"]["critical"], bool), (
            f"payload 'critical' must be a bool (so JSON-serializes to "
            f"true/false), got {type(captured['data']['critical']).__name__}. "
            f"A string 'true' would always be truthy in JS, escalating every "
            f"notification to critical urgency on Linux (bypasses DND)."
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
                {"id": "mig17-toast-duration-type"},
            )
        assert isinstance(captured["data"]["duration_ms"], int), (
            f"payload 'duration_ms' must be an int (so JSON-serializes to a "
            f"number), got {type(captured['data']['duration_ms']).__name__}."
        )


class TestLinuxNotificationsRequireLibnotify:
    """Gate 6: Linux notifications require ``libnotify4`` to be installed."""

    def test_deb_depends_includes_libnotify4(self):
        """The ``.deb`` package's ``Depends`` field MUST list"""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
        depends = deb.get("depends", [])
        assert "libnotify4" in depends, (
            f"tauri.conf.json bundle.linux.deb.depends MUST include "
            f"'libnotify4', without it, apt install lausu*.deb "
            f"doesn't pull in libnotify, and tauri-plugin-notification's "
            f"notify() call silently fails (D-Bus message never sent). "
            f"Found deb depends: {depends!r}"
        )

    def test_rpm_depends_includes_libnotify(self):
        """The ``.rpm`` package's ``Requires`` field MUST list"""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        rpm = conf.get("bundle", {}).get("linux", {}).get("rpm", {})
        depends = rpm.get("depends", [])
        assert "libnotify" in depends, (
            f"tauri.conf.json bundle.linux.rpm.depends MUST include "
            f"'libnotify' (the Fedora/RHEL package name; Debian's "
            f"libnotify4 maps to libnotify in the rpm namespace). "
            f"Found rpm depends: {depends!r}"
        )

    def test_libnotify_is_listed_in_runbook_prerequisites(self):
        """The Linux validation runbook MUST list ``libnotify4`` (apt)"""
        src = _read(LINUX_RUNBOOK)
        # The runbook lists both apt and dnf package names.
        assert "libnotify4" in src, (
            "linux-validation-runbook.md MUST mention 'libnotify4' in its "
            "apt system-libs prerequisites, it's a build + runtime dep "
            "for the Linux Tauri host."
        )
        assert "libnotify" in src, (
            "linux-validation-runbook.md MUST mention 'libnotify' in its "
            "dnf system-libs prerequisites (the Fedora/RHEL package name)."
        )


class TestPostinstDoesNotNeedNotificationLogic:
    """Gate 7: the ``.deb`` postinst script does NOT need to do anything"""

    def test_postinst_script_exists(self):
        """The ``.deb`` postinst script MUST exist at"""
        assert POSTINST_SCRIPT.is_file(), (
            f"postinst script MUST exist at {POSTINST_SCRIPT}, it's "
            f"referenced by tauri.conf.json bundle.linux.deb.postInstallScript."
        )

    def test_postinst_does_not_install_libnotify(self):
        """any libnotify variant), that's the package manager's job (via"""
        src = _read(POSTINST_SCRIPT)
        src_lower = src.lower()
        # The postinst must NOT contain apt/dnf install commands for
        forbidden_patterns = [
            "apt install libnotify",
            "apt-get install libnotify",
            "apt install -y libnotify",
            "apt-get install -y libnotify",
            "dnf install libnotify",
            "dnf install -y libnotify",
            "yum install libnotify",
        ]
        for pat in forbidden_patterns:
            assert pat not in src_lower, (
                f"postinst script MUST NOT install libnotify via '{pat}', "
                f"that's the package manager's job (via the Depends: field "
                f"declared in tauri.conf.json bundle.linux.deb.depends). "
                f"Installing from postinst would duplicate the package "
                f"manager's work AND fail under dpkg -i without the resolver."
            )

    def test_postinst_does_not_register_dbus_notification_service(self):
        """D-Bus notification service (e.g. ``notification-daemon``,"""
        src = _read(POSTINST_SCRIPT)
        src_lower = src.lower()
        forbidden_substrings = [
            "notification-daemon",
            "mako",
            "dunst",
            "org.freedesktop.notifications",
            "systemctl enable notification",
            "systemctl start notification",
        ]
        for sub in forbidden_substrings:
            assert sub not in src_lower, (
                f"postinst script MUST NOT reference '{sub}', the "
                f"notification daemon is a session service owned by the "
                f"user's graphical session, not the system postinst. "
                f"System postinst runs as root during apt install; it has "
                f"no business touching user session services."
            )

    def test_postinst_focuses_on_keyboard_permissions_only(self):
        """Belt-and-braces: the postinst's actual job is the keyboard-"""
        src = _read(POSTINST_SCRIPT)
        assert "install_permissions.py" in src, (
            "postinst script MUST reference 'install_permissions.py', "
            "that's the actual job of the postinst (keyboard permission "
            "setup), in contrast to notifications which are handled "
            "purely via the package's Depends: field."
        )


class TestSourceInspectionBeltAndBraces:
    """
    Gate 8: belt-and-braces source-inspection tests.
    These don't correspond to a single gate point, they pin additional
    """

    def test_ws_rs_emits_python_event_envelope(self):
        """``ws.rs`` MUST also emit the generic ``python-event``"""
        src = _read_ws_bridge_rs()
        assert 'emit("python-event"' in src, (
            "ws.rs must also emit the generic 'python-event' envelope "
            "(ADR-0020 §6.3), this is the catch-all path the usePython "
            "hook uses to learn about notification events on Linux."
        )

    def test_ws_rs_emits_specific_event_with_emit_name(self):
        """``ws.rs`` MUST emit the specific event (using ``emit_name``)"""
        src = _read_ws_bridge_rs()
        assert "emit(emit_name" in src, (
            "ws.rs must emit the specific event using `emit_name` (the "
            "result of the match arm), this is what carries the canonical "
            "'notification' name to direct UI listeners on Linux."
        )

    def test_ws_rs_has_other_arm_passthrough(self):
        """``ws.rs`` forwards every event type unchanged."""
        src = _read_ws_bridge_rs()
        # ``let emit_name = event_type;`` was
        assert re.search(
            r"let\s+emit_name\s*=\s*(?:translate_event_name\(event_type\)|event_type)\s*;",
            src,
        ), "ws.rs must forward every event type via `let emit_name = translate_event_name(event_type);`."

    def test_system_handlers_publishes_notification_event(self):
        """The Python sidecar's ``system_handlers.py`` publishes a"""
        src = _read(SYSTEM_HANDLERS_PY)
        assert '"type": "notification"' in src, "system_handlers.py MUST publish with type='notification' (per CR-8)."

    def test_linux_runbook_lists_toast_as_gate_point(self):
        """a numbered gate point (Step 9: \"libnotify toast appears on X11"""
        src = _read(LINUX_RUNBOOK)
        # The runbook's gate-point header for notifications:
        assert "tauri-plugin-notification" in src or "libnotify" in src, (
            "linux-validation-runbook.md MUST mention 'tauri-plugin-notification' "
            "or 'libnotify' as a gate-point header, gate check 6 is the toast gate."
        )
        assert "gate point 5" in src, (
            "linux-validation-runbook.md MUST label the toast gate as "
            "'gate point 5' (per the 9-Point Validation Gate Summary table, "
            "the gate-point numbering is the canonical reference for the 9 "
            "Phase 0-L gates)."
        )

    def test_linux_runbook_documents_notification_capability_failure(self):
        """The Linux runbook §6.5 / Step 9 \"Common failures\" section"""
        src = _read(LINUX_RUNBOOK)
        assert "notification:allow-notify" in src, (
            "linux-validation-runbook.md MUST mention "
            "'notification:allow-notify' in its Common failures section, "
            "this is the most common Linux toast failure mode (Tauri v2 "
            "silently blocks notification APIs without the capability grant)."
        )

    def test_linux_runbook_documents_libnotify_install_failure(self):
        """The Linux runbook MUST document the ``libnotify: command not"""
        src = _read(LINUX_RUNBOOK)
        src_lower = src.lower()
        assert "apt-get install libnotify4" in src_lower or "apt install libnotify4" in src_lower, (
            "linux-validation-runbook.md MUST document the "
            "'sudo apt-get install libnotify4' fix for the missing-libnotify "
            "failure mode."
        )


class TestValidateOnLinuxHostBlock:
    """Gate 9: the VALIDATE ON LINUX HOST block (in this file's module"""

    @staticmethod
    def _module_docstring() -> str:
        """Return the module-level docstring of this file."""
        return __doc__ or ""

    def test_docstring_contains_validate_on_linux_host_header(self):
        """``VALIDATE ON LINUX HOST:`` header, this is the canonical"""
        doc = self._module_docstring()
        assert "VALIDATE ON LINUX HOST:" in doc, (
            "Module docstring MUST contain 'VALIDATE ON LINUX HOST:' header, "
            "this is the canonical marker the Linux host validator scans for."
        )

    def test_docstring_documents_libnotify_install_step(self):
        """The VALIDATE ON LINUX HOST block MUST mention the libnotify4"""
        doc = self._module_docstring()
        assert "sudo apt install libnotify4" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention 'sudo apt install "
            "libnotify4', without libnotify4, the notify() call silently "
            "fails (D-Bus message never sent)."
        )
        # Also documents the dpkg -i alternative path (which pulls
        assert "dpkg -i lausu*.deb" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention the 'dpkg -i "
            "lausu*.deb' alternative, this path pulls libnotify4 "
            "automatically via the .deb's Depends: field (no manual "
            "apt install needed)."
        )

    def test_docstring_documents_dbus_troubleshooting(self):
        """The VALIDATE ON LINUX HOST block MUST mention the D-Bus /"""
        doc = self._module_docstring()
        assert "D-Bus" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention 'D-Bus', the "
            "troubleshooting hint for the no-notification-daemon failure mode."
        )
        assert "GNOME Shell" in doc or "KDE Plasma" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention 'GNOME Shell' or "
            "'KDE Plasma', the two desktop environments the validator "
            "should verify are active if the notification doesn't appear."
        )

    def test_docstring_documents_log_path(self):
        """The VALIDATE ON LINUX HOST block MUST document the expected"""
        doc = self._module_docstring()
        assert "~/.local/share/lausu/logs/lausu.log" in doc, (
            "VALIDATE ON LINUX HOST block MUST document the Linux log path "
            "(~/.local/share/lausu/logs/lausu.log) so the "
            "validator can confirm the notification event was emitted."
        )

    def test_docstring_documents_expected_timing(self):
        """The VALIDATE ON LINUX HOST block MUST document the expected"""
        doc = self._module_docstring()
        assert "within 1s" in doc, (
            "VALIDATE ON LINUX HOST block MUST document the expected timing "
            "('within 1s'), the upper bound for how long the validator should "
            "wait for the banner before declaring the gate failed."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
