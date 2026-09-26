"""toast notification wiring validation."""

from __future__ import annotations

import json
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
TRAY_PY = _REPO_ROOT / "voice_typer" / "server" / "tray.py"
SYSTEM_HANDLERS_PY = _REPO_ROOT / "voice_typer" / "server" / "handlers" / "system_handlers.py"


def _read(path: Path) -> str:
    """Read a file as UTF-8 text. Fail loud if missing, every path this"""
    assert path.is_file(), f"required toast-wiring artifact missing: {path}"
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
            "calls fail with 'plugin not registered'."
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
            "are not generated and the webview's notify() call fails."
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
            f"webview's notify() call returns PermissionDenied."
        )

    def test_capabilities_grants_notification_allow_notify_specifically(self):
        """The runbook §6.5 specifically calls out"""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-notify" in perms, (
            f"main-runtime.json must grant 'notification:allow-notify' "
            f"(per runbook §6.5 pass criteria). Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )


class TestWsRsNotificationEventName:
    """Gate 4: the WS reader emits the canonical ``notification`` event"""

    def test_ws_rs_uses_translate_event_name_for_specific_events(self):
        """``ws.rs`` MUST derive the emitted event name via"""
        src = _read_ws_bridge_rs()
        assert "let emit_name = translate_event_name(event_type);" in src, (
            "ws.rs must derive the emitted name via `translate_event_name(event_type)` "
            "— the generic rename helper that passes the canonical 'notification' "
            "name through unchanged (CR-8 source-side rename)."
        )
        assert "emit(emit_name, payload.clone())" in src, (
            "ws.rs must emit the specific event with `emit(emit_name, payload.clone())` "
            "so the webview's direct listener receives the payload."
        )

    def test_ws_rs_emits_specific_event_with_payload(self):
        """The specific-event emit must carry the payload"""
        src = _read_ws_bridge_rs()
        # The current emit form (from the reader task):
        assert "emit(emit_name, payload.clone())" in src, (
            "ws.rs must emit the specific event WITH the payload "
            "(emit(emit_name, payload.clone())), otherwise the toast "
            "renders blank."
        )


class TestWsRsEmitsSpecificEventForDirectListeners:
    """Gate 5: ``ws.rs`` emits the specific (translated) event name so"""

    def test_ws_rs_passes_through_event_types_via_translate_arm(self):
        """``translate_event_name``'s ``other => other`` arm passes ANY"""
        src = _read_ws_bridge_rs()
        # The current form (from the reader task):
        assert "let emit_name = translate_event_name(event_type);" in src, (
            "ws.rs must derive the emit name via `translate_event_name(event_type)` "
            "(the `other => other` arm passes canonical names like 'notification' "
            "through unchanged, the Python sidecar publishes the canonical name "
            "directly per CR-8)."
        )

    def test_ws_rs_emits_specific_event_with_emit_name(self):
        """``ws.rs`` MUST emit the specific event (using ``emit_name``)"""
        src = _read_ws_bridge_rs()
        # The emit form (from ws.rs:130):
        assert "emit(emit_name" in src, (
            "ws.rs must emit the specific event using `emit_name` (the "
            "result of the match arm), this is what carries the "
            "specific event name through to direct UI listeners."
        )

    def test_ws_rs_also_emits_python_event_envelope(self):
        """Belt-and-braces: ``ws.rs`` also emits the generic"""
        src = _read_ws_bridge_rs()
        assert 'emit("python-event"' in src, (
            "ws.rs must also emit the generic 'python-event' envelope "
            "(ADR-0020 §6.3), this is the catch-all path the usePython "
            "hook uses to learn about notification events."
        )


# We pin the ACTUAL behavior here and document the gap.


class TestTrayNotifyPath:
    """
    Gate 6 (as-implemented): the Python ``tray.notify()`` path.
    ``_icon.notify(message, title)`` directly. We pin the actual
    """

    def test_tray_notify_calls_pystray_icon_notify_directly(self):
        """``TrayIcon.notify()`` MUST call ``self._icon.notify(message, title)``"""
        src = _read(TRAY_PY)
        # The actual call form (from tray.py::_do_notify):
        assert "self._icon.notify(message, title)" in src, (
            "tray.py::_do_notify must call self._icon.notify(message, title) "
            "— this is the pystray native toast path. (NOTE: the task spec "
            "expected tray.notify() to publish via event_bus; the actual "
            "implementation uses pystray directly. See module docstring "
            "IMPLEMENTATION GAP section.)"
        )

    def test_tray_notify_does_not_publish_via_event_bus(self):
        """``TrayIcon.notify()`` MUST NOT call ``event_bus.publish``."""
        src = _read(TRAY_PY)
        # Extract the notify() method body. We use a simple substring
        notify_start = src.find("def notify(self, title: str, message: str)")
        assert notify_start != -1, "tray.py must define notify(self, title, message)"
        # Find the next method def after notify(), search for the next
        next_def = src.find("\n    def ", notify_start + 1)
        notify_body = src[notify_start:] if next_def == -1 else src[notify_start:next_def]
        assert "event_bus.publish" not in notify_body, (
            "tray.py::notify() must NOT call event_bus.publish, it uses "
            "pystray's _icon.notify() directly. (The event_bus.publish "
            "path for notifications lives in system_handlers.py::"
            "_handle_show_notification, NOT in tray.py.)"
        )


class TestIpcHandlerPublishesNotificationViaEventBus:
    """Gate 6 (actual event_bus path): the ``show_notification``"""

    def test_ipc_handler_publishes_notification_event_via_event_bus(self):
        """``event_bus.publish`` being called with ``type == \"notification\"``."""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            resp = server._handle_show_notification(
                {
                    "title": "Hello",
                    "message": "World",
                    "duration_ms": 5000,
                    "critical": True,
                },
                {},
            )
        assert resp["type"] == "ack", f"handler should ack a well-formed payload, got {resp!r}"
        assert captured.get("type") == "notification", (
            f"event_bus.publish must be called with type='notification' (per CR-8). Got: {captured.get('type')!r}"
        )

    def test_ipc_handler_publishes_canonical_event_name(self):
        """The published event uses the canonical name (per CR-8)."""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification(
                {"title": "T", "message": "B"},
                {},
            )
        assert captured.get("type") == "notification", "Python sidecar must publish with type='notification'."


class TestNotificationPayloadShape:
    """Gate 7 (as-implemented): the published notification payload shape."""

    def test_payload_shape_matches_actual_implementation(self):
        """The published payload MUST match the actual implementation"""
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
        assert captured["type"] == "notification"
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
        This pins the actual implementation's field name. The task spec
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
            "payload data must have a 'message' field (the actual "
            "implementation field name. NOT 'body' as the task spec said)."
        )
        assert "body" not in captured["data"], (
            "payload data must NOT have a 'body' field, the actual "
            "implementation uses 'message'. (See module docstring "
            "IMPLEMENTATION GAP section.)"
        )

    def test_payload_defaults_when_data_empty(self):
        """Empty ``data: {}`` MUST still produce a well-formed payload"""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_notification({}, {})
        assert captured["type"] == "notification"
        # APP_NAME is "Lausu" per voice_typer/server/branding.py.
        assert captured["data"]["title"] == "Lausu"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
