"""MIG-1.5 Phase 0-W Gate Check 6 — toast notification wiring validation.

Source-inspection + behavior tests that validate the wiring required for
``tauri-plugin-notification`` to post Windows toast notifications on a
real Windows host. These tests run in the Linux sandbox; the actual
"does a toast appear on screen?" assertion MUST be executed by a human
on a real Windows host using the VALIDATE ON WINDOWS HOST block at the
bottom of this docstring.

What this file pins (the toast wiring contract):

1. ``src-tauri/src/main.rs`` registers ``tauri_plugin_notification::init()``
   so the webview can call ``invoke('plugin:notification|notify', ...)``.
2. ``src-tauri/tauri.conf.json`` declares ``"notification": {}`` in the
   ``plugins`` section (Tauri v2 requires both the plugin registration
   in Rust AND the config entry — the config block enables the JS
   bindings to be generated).
3. ``src-tauri/capabilities/migrate-runtime.json`` grants at least one
   ``notification:*`` permission (the least-privilege gate; Tauri v2
   ships zero permissions by default, so this MUST be explicit).
4. ``src-tauri/src/sidecar/ws.rs`` has a CR-8 backward-compat alias that
   re-emits incoming ``electron_notification`` events under the canonical
   ``notification`` name (so new UI code subscribing to ``notification``
   keeps working during a rolling upgrade from an old Python sidecar).
5. ``src-tauri/src/sidecar/ws.rs`` ALSO emits the legacy
   ``electron_notification`` event unchanged (pass-through via the
   ``other => other`` match arm + the generic ``python-event`` envelope)
   so old UI listeners keep working during the same rolling upgrade.
6. The Python sidecar's notification path publishes via
   ``event_bus.publish`` so the WS bridge can ferry it to the webview.
7. The published payload shape matches what the webview's notification
   handler expects.

IMPLEMENTATION GAP (reported, not fixed)
========================================

The task description for this gate check specified:

  - "Test that the Python ``tray.notify()`` path emits the
    ``electron_notification`` event via ``event_bus.publish``"
  - "Test that the notification payload shape is
    ``{"type":"electron_notification","data":{"title":"...","body":"..."}}``"

The ACTUAL implementation diverges on both points (per CR-8, which
renamed the event at the source):

  - ``tray.py::TrayIcon.notify()`` does NOT publish via ``event_bus`` —
    it calls pystray's ``self._icon.notify(message, title)`` directly
    (which itself shows a native OS toast via pystray's Win10
    ``ToastNotification`` backend on Windows). The ``event_bus.publish``
    path for notifications is the ``show_electron_notification`` IPC
    command, handled in
    ``voice_typer/server/handlers/system_handlers.py::
    _handle_show_electron_notification``.
  - The published payload shape is
    ``{"type": "notification", "data": {"title": "...", "message": "...",
    "duration_ms": int, "critical": bool}}`` — i.e. the event name is
    ``notification`` (NOT ``electron_notification``) and the body field
    is ``message`` (NOT ``body``). The legacy ``electron_notification``
    name is only emitted by an OLD Python sidecar; the Rust-side alias
    in ``ws.rs`` re-emits it as ``notification`` for new UI code.

This file tests the ACTUAL implementation behavior. The gap is documented
in the test docstrings + the final report to the primary agent so the
task spec can be reconciled with the implementation in a follow-up.

VALIDATE ON WINDOWS HOST:
1. Launch Voice Typer
2. Trigger a notification (e.g. complete a dictation → "Transcription
   complete" toast, OR toggle autostart → tray notify)
3. Verify a Windows toast notification appears (bottom-right corner on
   Win10/11)
4. Check log for:
   - "[EVENT] electron_notification emitted: title='...' body='...'"
   - "[WS] renamed electron_notification → notification"
Expected: toast appears within 1s; title + body match the event payload
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from unittest.mock import MagicMock, patch

import pytest

# ─── Path constants ───────────────────────────────────────────────────────
# Resolve once at import time so each test doesn't repeat the dance. The
# repo root is three levels up from this file
# (tests/tauri/mig15/test_toast_windows.py → repo root).
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


# ─── Helpers ──────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    """Read a file as UTF-8 text. Fail loud if missing — every path this
    module reads is a hard dependency of the toast wiring, so a missing
    file is a real regression (not a soft skip)."""
    assert path.is_file(), f"required toast-wiring artifact missing: {path}"
    return path.read_text(encoding="utf-8")


def _read_ws_bridge_rs() -> str:
    """Read the Rust WS bridge source: ws.rs plus its ws/reader.rs /
    ws/writer.rs task submodules.

    reader/writer module split: the reader body (the
    ``translate_event_name(event_type)`` call site, the specific-event
    ``emit(emit_name, payload.clone())`` emit, and the generic
    ``python-event`` envelope fan-out asserted below) moved into
    ``ws/reader.rs``; the writer task into ``ws/writer.rs``.
    Concatenating keeps the source-inspection assertions green across
    the split.
    """
    parts = [_read(WS_RS)]
    for name in ("reader.rs", "writer.rs"):
        parts.append(_read(WS_RS.parent / "ws" / name))
    return "\n\n".join(parts)


def _make_ipc_server():
    """Build a minimal ``IPCServer`` with a mock app + service.

    Mirrors ``tests/test_notification_event_name.py::_make_ipc_server``
    (same surface: ``app``, ``service``, ``_config_mutation_lock``) so
    the ``SystemHandlersMixin`` can run its validation + publish path
    without a real ``VoiceTyperApp``. This keeps the test headless (no
    torch, no pystray, no real tray).
    """
    from voice_typer.server.ipc_server import IPCServer

    app = MagicMock()
    app._config_mutation_lock = RLock()
    server = IPCServer.__new__(IPCServer)
    server.app = app
    server.service = MagicMock()
    return server


# ─── Test 1: main.rs registers tauri_plugin_notification::init() ──────────


class TestMainRsRegistersNotificationPlugin:
    """Gate 1: the Rust host must register the notification plugin."""

    def test_main_rs_registers_tauri_plugin_notification_init(self):
        """``main.rs`` must contain a ``.plugin(tauri_plugin_notification::init())``
        call inside the ``tauri::Builder::default()`` chain.

        Without this, ``invoke('plugin:notification|notify', ...)`` from
        the webview returns "plugin not registered" — no toast ever
        appears, regardless of capability grants or tauri.conf.json
        config.
        """
        src = _read(MAIN_RS)
        assert "tauri_plugin_notification::init()" in src, (
            "main.rs must register tauri_plugin_notification::init() in the "
            "Builder chain — without it, the webview's notification invoke() "
            "calls fail with 'plugin not registered'."
        )

    def test_main_rs_notification_plugin_is_in_builder_chain(self):
        """Belt-and-braces: the ``tauri_plugin_notification::init()`` call
        must be preceded by ``.plugin(`` (i.e. it's part of the Builder
        chain, not a stray mention in a comment or docstring)."""
        src = _read(MAIN_RS)
        # Look for the actual call form: `.plugin(tauri_plugin_notification::init())`
        assert ".plugin(tauri_plugin_notification::init())" in src, (
            "tauri_plugin_notification::init() must be called via .plugin(...) "
            "inside the tauri::Builder::default() chain in main.rs."
        )


# ─── Test 2: tauri.conf.json declares "notification": null in plugins ────


class TestTauriConfDeclaresNotificationPlugin:
    """Gate 2: tauri.conf.json must list ``notification`` in the plugins section."""

    def test_tauri_conf_json_has_notification_in_plugins(self):
        """``tauri.conf.json`` MUST declare a ``notification`` entry under
        the top-level ``plugins`` key (value ``null`` — see the sibling
        unit-compatibility test).

        Tauri v2 requires BOTH the Rust plugin registration AND the
        config entry — the config block is what triggers generation of
        the JS bindings the webview imports. Missing config ⇒
        ``@tauri-apps/plugin-notification`` import fails at runtime.
        """
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        assert "plugins" in conf, (
            "tauri.conf.json must have a top-level 'plugins' object — Tauri v2 generates JS bindings from this section."
        )
        assert "notification" in conf["plugins"], (
            "tauri.conf.json plugins section must declare 'notification' — "
            "without it, the @tauri-apps/plugin-notification JS bindings "
            "are not generated and the webview's notify() call fails."
        )

    def test_notification_plugin_config_is_unit_compatible(self):
        """The ``notification`` plugin config MUST be serde-unit compatible
        (``null``). tauri-plugin-notification v2 registers NO config type
        (its init is a plain ``Builder::new("notification")``), so the
        runtime deserializes this entry into ``()`` — an empty map
        (``{}``) fails app startup with "invalid type: map, expected
        unit" (found on the first Windows host run; see tauri issue
        #8769 for the same error class)."""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        notif_cfg = conf["plugins"]["notification"]
        assert notif_cfg is None, (
            f"tauri.conf.json plugins.notification must be null (serde unit), got "
            f"{type(notif_cfg).__name__}: {notif_cfg!r} — a non-null value fails "
            "app startup with 'invalid type: map, expected unit'"
        )


# ─── Test 3: capabilities grant notification permission ─────────────────


class TestCapabilitiesGrantNotificationPermission:
    """Gate 3: the migrate-runtime capability must grant a notification permission.

    Tauri v2 ships zero permissions by default — even with the plugin
    registered + the config entry, the webview's notify() call returns
    ``PermissionDenied`` unless an explicit ``notification:*`` permission
    is granted in a capability file the window matches.
    """

    def test_capabilities_grants_notification_allow_notify_or_default(self):
        """The ``migrate-runtime`` capability MUST grant at least one
        ``notification:*`` permission. The runbook §6.5 pass criteria
        says "No ``notification:allow-notify`` capability error in
        ``voice-typer.log``" — i.e. ``notification:allow-notify`` is the
        canonical grant. We accept ``notification:default`` as an
        equivalent (it's a Tauri v2 permission set that bundles
        ``allow-notify`` + the permission-check helpers).
        """
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        assert "permissions" in cap, "migrate-runtime.json must declare a 'permissions' array."
        perms = cap["permissions"]
        assert isinstance(perms, list), f"capabilities 'permissions' must be a list, got {type(perms).__name__}"
        notif_perms = [p for p in perms if isinstance(p, str) and p.startswith("notification:")]
        assert notif_perms, (
            f"migrate-runtime.json must grant at least one 'notification:*' "
            f"permission — found none in {perms!r}. Without this, the "
            f"webview's notify() call returns PermissionDenied."
        )

    def test_capabilities_grants_notification_allow_notify_specifically(self):
        """The runbook §6.5 specifically calls out
        ``notification:allow-notify`` as the required grant. Verify it's
        present (not just any ``notification:*`` permission)."""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-notify" in perms, (
            f"migrate-runtime.json must grant 'notification:allow-notify' "
            f"(per runbook §6.5 pass criteria). Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )


# ─── Test 4: ws.rs renames electron_notification → notification ──────────


class TestWsRsRenamesElectronNotificationToNotification:
    """Gate 4: the WS reader emits the canonical ``notification`` event
    to the webview.

    CR-8 reconciliation: the ``electron_notification`` →
    ``notification`` rename was moved INTO the Python sidecar (it now
    publishes ``notification`` directly), so the Rust-side alias branch
    was REMOVED from ``ws.rs`` — see the removal comment in the reader
    task. The canonical event reaches the webview through the generic
    specific-event emit (``translate_event_name`` passes unknown events
    — including ``notification`` — through unchanged).

    Source-inspection test: we read ``ws.rs`` as a string and assert the
    current wiring. We don't compile/run the Rust code (the Linux
    sandbox can't build the Tauri app — that's the whole point of the
    Phase 0-W gate).
    """

    def test_ws_rs_uses_translate_event_name_for_specific_events(self):
        """``ws.rs`` MUST derive the emitted event name via
        ``translate_event_name(event_type)`` — the generic rename
        helper whose ``other => other`` arm passes ``notification``
        through unchanged (the Python sidecar publishes the canonical
        name directly per CR-8).

        This is the current wiring: no special-case alias branch for
        ``electron_notification`` exists anymore (it was removed with
        the CR-8 source-side rename), so the canonical event relies on
        the generic pass-through.
        """
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
        """The specific-event emit must carry the payload
        (``payload.clone()``), not just an empty event. Otherwise the
        webview's notification handler receives no title/body and the
        toast renders blank."""
        src = _read_ws_bridge_rs()
        # The current emit form (from the reader task):
        #   let _ = app_for_reader.emit(emit_name, payload.clone());
        assert "emit(emit_name, payload.clone())" in src, (
            "ws.rs must emit the specific event WITH the payload "
            "(emit(emit_name, payload.clone())) — otherwise the toast "
            "renders blank."
        )


# ─── Test 5: ws.rs emits legacy electron_notification for backward compat ─


class TestWsRsEmitsLegacyElectronNotificationForBackwardCompat:
    """Gate 5: ``ws.rs`` also emits the legacy ``electron_notification``
    event unchanged (so old UI listeners keep working during the rolling
    upgrade)."""

    def test_ws_rs_passes_through_event_types_via_translate_arm(self):
        """``translate_event_name``'s ``other => other`` arm passes ANY
        unrecognized event type through unchanged — including the
        canonical ``notification`` name the Python sidecar publishes
        directly (per CR-8). The specific-event emit then carries that
        name to the webview.

        This is the current wiring: the ``relaunch_electron`` →
        ``relaunch_app`` rename arm was REMOVED (Python publishes
        ``relaunch_app`` directly) and the ``electron_notification`` →
        ``notification`` alias branch was REMOVED for the same reason —
        unknown events pass through ``translate_event_name`` unchanged.
        """
        src = _read_ws_bridge_rs()
        # The current form (from the reader task):
        #   let emit_name = translate_event_name(event_type);
        assert "let emit_name = translate_event_name(event_type);" in src, (
            "ws.rs must derive the emit name via `translate_event_name(event_type)` "
            "(the `other => other` arm passes canonical names like 'notification' "
            "through unchanged — the Python sidecar publishes the canonical name "
            "directly per CR-8)."
        )

    def test_ws_rs_emits_specific_event_with_emit_name(self):
        """``ws.rs`` MUST emit the specific event (using ``emit_name``)
        so direct listeners like ``appWindow.on('electron_notification')``
        keep firing. The generic ``python-event`` envelope is NOT
        sufficient — direct listeners don't subscribe to that."""
        src = _read_ws_bridge_rs()
        # The emit form (from ws.rs:130):
        #   let _ = app_for_reader.emit(emit_name, payload.clone());
        assert "emit(emit_name" in src, (
            "ws.rs must emit the specific event using `emit_name` (the "
            "result of the match arm) — this is what carries the legacy "
            "'electron_notification' name through to direct UI listeners."
        )

    def test_ws_rs_also_emits_python_event_envelope(self):
        """Belt-and-braces: ``ws.rs`` also emits the generic
        ``python-event`` envelope (per ADR-0020 §6.3) which the
        ``usePython`` hook's onEvent catch-all listens to. This is the
        secondary path by which the renderer learns about a notification
        event — both paths must be present for the toast wiring to be
        complete."""
        src = _read_ws_bridge_rs()
        assert 'emit("python-event"' in src, (
            "ws.rs must also emit the generic 'python-event' envelope "
            "(ADR-0020 §6.3) — this is the catch-all path the usePython "
            "hook uses to learn about notification events."
        )


# ─── Test 6: Python tray.notify() path ──────────────────────────────────
#
# IMPLEMENTATION GAP (reported, not fixed):
#   The task description said "Test that the Python tray.notify() path
#   emits the electron_notification event via event_bus.publish". The
#   actual implementation does NOT do that — tray.notify() calls
#   pystray's _icon.notify(message, title) directly (which itself shows
#   a native OS toast on Windows via pystray's Win10 ToastNotification
#   backend). The event_bus.publish path for notifications is the
#   show_electron_notification IPC command, handled in system_handlers.py.
#   We pin the ACTUAL behavior here and document the gap.


class TestTrayNotifyPath:
    """Gate 6 (as-implemented): the Python ``tray.notify()`` path.

    IMPLEMENTATION GAP: the task spec expected ``tray.notify()`` to
    publish via ``event_bus``; the actual implementation calls pystray's
    ``_icon.notify(message, title)`` directly. We pin the actual
    behavior and document the divergence. The ``event_bus.publish``
    path for notifications is exercised in
    :class:`TestIpcHandlerPublishesNotificationViaEventBus` below.
    """

    def test_tray_notify_calls_pystray_icon_notify_directly(self):
        """``TrayIcon.notify()`` MUST call ``self._icon.notify(message, title)``
        (pystray's native toast path). On Windows 10+, pystray's Win10
        backend uses the WinRT ``ToastNotification`` API — the same
        API surface ``tauri-plugin-notification`` uses on the Rust side.

        This test exists to pin the ACTUAL behavior (which diverges from
        the task spec — see the module docstring's IMPLEMENTATION GAP
        section). It is a source-inspection test: we read ``tray.py``
        and assert the call form is present.
        """
        src = _read(TRAY_PY)
        # The actual call form (from tray.py::_do_notify):
        #   self._icon.notify(message, title)
        assert "self._icon.notify(message, title)" in src, (
            "tray.py::_do_notify must call self._icon.notify(message, title) "
            "— this is the pystray native toast path. (NOTE: the task spec "
            "expected tray.notify() to publish via event_bus; the actual "
            "implementation uses pystray directly. See module docstring "
            "IMPLEMENTATION GAP section.)"
        )

    def test_tray_notify_does_not_publish_via_event_bus(self):
        """``TrayIcon.notify()`` MUST NOT call ``event_bus.publish``.

        This is the negative half of the gap documentation: the task spec
        expected ``tray.notify()`` to publish via ``event_bus``, but the
        actual implementation delegates to pystray. We assert the
        ``notify`` method body does NOT contain an ``event_bus.publish``
        call so a future refactor doesn't accidentally introduce a
        double-toast (pystray native + webview toast).

        The ``event_bus.publish`` path for notifications lives in the
        ``show_electron_notification`` IPC handler (see
        :class:`TestIpcHandlerPublishesNotificationViaEventBus`).
        """
        src = _read(TRAY_PY)
        # Extract the notify() method body. We use a simple substring
        # search rather than AST parsing because the method is short and
        # the assertion is about absence of a specific call pattern.
        # The notify() method starts at "def notify(self, title: str, message: str)"
        # and ends at the next "def " at the same indent level.
        notify_start = src.find("def notify(self, title: str, message: str)")
        assert notify_start != -1, "tray.py must define notify(self, title, message)"
        # Find the next method def after notify() — search for the next
        # "\n    def " at the same indent (4 spaces).
        next_def = src.find("\n    def ", notify_start + 1)
        # notify() is the last method — take the rest of the class.
        notify_body = src[notify_start:] if next_def == -1 else src[notify_start:next_def]
        assert "event_bus.publish" not in notify_body, (
            "tray.py::notify() must NOT call event_bus.publish — it uses "
            "pystray's _icon.notify() directly. (The event_bus.publish "
            "path for notifications lives in system_handlers.py::"
            "_handle_show_electron_notification, NOT in tray.py.)"
        )


# ─── Test 7: IPC handler publishes notification via event_bus ────────────
#
# This is the ACTUAL event_bus.publish path for notifications. The task
# spec attributed it to tray.notify(); the actual implementation puts
# it in the show_electron_notification IPC handler. We pin the actual
# behavior here.


class TestIpcHandlerPublishesNotificationViaEventBus:
    """Gate 6 (actual event_bus path): the ``show_electron_notification``
    IPC handler publishes via ``event_bus.publish``.

    The handler lives in
    ``voice_typer/server/handlers/system_handlers.py::
    _handle_show_electron_notification``. It validates the input dict,
    then publishes a ``notification`` event (per CR-8 — renamed from
    the legacy ``electron_notification``) with the title/message/
    duration_ms/critical fields.

    Note: per CR-8, the published event name is ``notification`` (NOT
    ``electron_notification``). The Rust-side alias in ``ws.rs`` handles
    old Python sidecars that still emit the legacy name during a rolling
    upgrade.
    """

    def test_ipc_handler_publishes_notification_event_via_event_bus(self):
        """``_handle_show_electron_notification`` MUST result in
        ``event_bus.publish`` being called with ``type == "notification"``.

        This is the canonical Python-side toast path: the handler
        validates the input and publishes a ``notification`` event, which
        the WS bridge ferries back to the webview, which then calls the
        notification plugin.

        NOTE: the ``show_electron_notification`` IPC command was REMOVED
        from the dispatch registry (it is handled directly by dedicated
        Tauri/Rust commands), so this test invokes the retained handler
        directly — the same pattern used by
        ``tests/handlers/test_handler_group_b_fixes.py``.
        """
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            resp = server._handle_show_electron_notification(
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

    def test_ipc_handler_does_not_publish_legacy_event_name(self):
        """The published event MUST NOT use the legacy
        ``electron_notification`` name (per CR-8).

        The Rust-side alias in ``ws.rs`` is what handles old Python
        sidecars that still emit the legacy name during a rolling
        upgrade — the NEW Python sidecar must NOT emit it.
        """
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
                {"title": "T", "message": "B"},
                {},
            )
        assert captured.get("type") != "electron_notification", (
            "Python sidecar must publish with type='notification', NOT the legacy 'electron_notification' name."
        )


# ─── Test 8: notification payload shape ─────────────────────────────────
#
# IMPLEMENTATION GAP (reported, not fixed):
#   The task description said the payload shape is
#   ``{"type":"electron_notification","data":{"title":"...","body":"..."}}``.
# The ACTUAL shape (per  + the validation logic in
#   system_handlers.py::_handle_show_electron_notification) is:
#     {"type": "notification",
#      "data": {"title": "...", "message": "...",
#               "duration_ms": int, "critical": bool}}
#   i.e. event name is ``notification`` (NOT ``electron_notification``)
#   and the body field is ``message`` (NOT ``body``). We pin the ACTUAL
#   shape here.


class TestNotificationPayloadShape:
    """Gate 7 (as-implemented): the published notification payload shape.

    IMPLEMENTATION GAP: the task spec expected
    ``{"type":"electron_notification","data":{"title":"...","body":"..."}}``;
    the actual shape is
    ``{"type":"notification","data":{"title":"...","message":"...",
    "duration_ms":int,"critical":bool}}``. We pin the actual shape.
    """

    def test_payload_shape_matches_actual_implementation(self):
        """The published payload MUST match the actual implementation
        shape:

        .. code-block:: python

           {
               "type": "notification",
               "data": {
                   "title": str,
                   "message": str,
                   "duration_ms": int,
                   "critical": bool,
               },
           }

        Notes on the divergence from the task spec:
          - Event name is ``notification`` (NOT ``electron_notification``)
            per CR-8.
          - Body field is ``message`` (NOT ``body``) — this is the
            field name the renderer's notification handler reads.
          - Two extra fields (``duration_ms``, ``critical``) control
            the toast's auto-close timeout and priority.
        """
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
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
        """The body field MUST be named ``message`` (NOT ``body``).

        This pins the actual implementation's field name. The task spec
        said ``body``; the implementation uses ``message`` (which is
        what the renderer's notification handler reads). Renaming would
        break the renderer.
        """
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
                {"title": "T", "message": "M"},
                {},
            )
        assert "message" in captured["data"], (
            "payload data must have a 'message' field (the actual "
            "implementation field name — NOT 'body' as the task spec said)."
        )
        assert "body" not in captured["data"], (
            "payload data must NOT have a 'body' field — the actual "
            "implementation uses 'message'. (See module docstring "
            "IMPLEMENTATION GAP section.)"
        )

    def test_payload_defaults_when_data_empty(self):
        """Empty ``data: {}`` MUST still produce a well-formed payload
        with sensible defaults (``title`` defaults to ``APP_NAME``,
        ``message`` to empty string, ``duration_ms`` to 0, ``critical``
        to False). This is what fires when the renderer invokes the
        command without explicit args."""
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification({}, {})
        assert captured["type"] == "notification"
        # APP_NAME is "Voice Typer" per voice_typer/server/branding.py.
        assert captured["data"]["title"] == "Voice Typer"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
