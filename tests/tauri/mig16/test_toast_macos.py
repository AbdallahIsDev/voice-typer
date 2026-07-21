"""MIG-1.6 Phase 0-M Gate Check 6 — toast notification wiring validation (macOS).

Source-inspection + behavior tests that validate the wiring required for
``tauri-plugin-notification`` to post macOS notification banners on a real
macOS host (Apple Silicon AND Intel). These tests run in the Linux
sandbox; the actual "does a notification banner appear on screen?"
assertion MUST be executed by a human on a real macOS host using the
VALIDATE ON MACOS HOST block at the bottom of this docstring.

This is gate check 6 of 9 for Phase 0-M. The corresponding Windows gate
check 6 lives at ``tests/tauri/mig15/test_toast_windows.py``. The Rust
host wiring is identical across platforms (Tauri abstracts the underlying
UNUserNotificationCenter on macOS / WinRT ToastNotification on Windows);
the macOS-specific concerns (signing, entitlements, TCC permission) are
what this file pins in addition.

What this file pins (the macOS toast wiring contract):

1. ``src-tauri/src/main.rs`` registers ``tauri_plugin_notification::init()``
   so the webview can call ``invoke('plugin:notification|notify', ...)``.
   (Cross-platform — same call as Windows.)
2. ``src-tauri/tauri.conf.json`` declares ``"notification": {}`` in the
   ``plugins`` section (Tauri v2 requires both the plugin registration
   in Rust AND the config entry — the config block enables the JS
   bindings to be generated).
3. ``src-tauri/capabilities/migrate-runtime.json`` grants at least one
   ``notification:*`` permission (the least-privilege gate; Tauri v2
   ships zero permissions by default, so this MUST be explicit). The
   runbook §6.4 pass criteria specifically calls out
   ``notification:allow-notify``.
4. ``src-tauri/src/sidecar/ws.rs`` has a CR-8 backward-compat alias that
   re-emits incoming ``electron_notification`` events under the canonical
   ``notification`` name (so new UI code subscribing to ``notification``
   keeps working during a rolling upgrade from an old Python sidecar).
   This is a CROSS-PLATFORM rename — the same code path runs on macOS,
   Windows, and Linux.
5. The published notification payload shape is
   ``{"type":"notification","data":{"title":"...","message":"...",
   "duration_ms":int,"critical":bool}}`` (per CR-8). This is the
   platform-agnostic shape — the renderer's notification handler reads
   the same fields regardless of OS.
6. macOS notifications require the app to be SIGNED with a Developer ID
   Application certificate (see ``docs/migration/signing-guide.md``).
   Unsigned dev builds (``cargo tauri build`` without ``--target
   universal-apple-darwin`` + codesign) silently fail to post
   notifications — the UNUserNotificationCenter API returns
   ``notificationNotScheduled`` without any visible error. This is a
   GAP if unsigned — documented below + in the VALIDATE ON MACOS HOST
   block.
7. The ``entitlements.plist`` does NOT need a notification-specific
   entitlement. Unlike iOS (which requires the
   ``com.apple.developer.usernotifications.communication`` entitlement
   for communication notifications) or push notifications (which require
   ``aps-environment``), local macOS notifications via
   ``UNUserNotificationCenter`` need NO entitlement beyond the hardened-
   runtime minimum. The existing ``entitlements.plist`` (audio-input +
   allow-jit + disable-library-validation) is sufficient.
8. macOS 13+ (Ventura and newer — the project's minimum per
   ``LSMinimumSystemVersion: 13.0`` in ADR-0020 §13.2) requires the
   user to grant notification permission in System Settings →
   Notifications → Voice Typer. The app must call
   ``UNUserNotificationCenter.requestAuthorization(...)`` on first
   launch (the Tauri notification plugin handles this internally when
   the renderer calls ``requestPermission()``). Without the user grant,
   notifications silently no-op.

IMPLEMENTATION GAPS (reported, not fixed)
=========================================

GAP-A — No Info.plist template in the repo:

  The macOS runbook §6.4 says the built ``.app/Contents/Info.plist``
  MUST contain ``NSUserNotificationsUsageDescription`` (and
  ``NSMicrophoneUsageDescription``). However, there is NO ``Info.plist``
  template in the repo (``src-tauri/`` has no ``Info.plist`` file —
  Tauri generates one at build time from its defaults + the
  ``bundle.macOS`` section of ``tauri.conf.json``, which is currently
  empty). This means the ``NSUserNotificationsUsageDescription`` key
  is NOT in source — it must be set via a Tauri ``Info.plist`` template
  (or merged in via a build script) before Phase 0-M can pass §6.4.
  This test file DOCUMENTS the gap; the actual fix lives outside this
  gate check (it's a build-script change tracked separately).

GAP-B — Signing gap for dev builds:

  Unsigned dev builds (``cargo tauri build`` without a Developer ID
  certificate) silently fail to post notifications on macOS 13+.
  This is by design (Apple's TCC framework gates notifications behind
  the signing identity), but it means a developer running the gate
  check 6 in a local dev build will see "no notification appears" with
  no error in the log. The VALIDATE ON MACOS HOST block documents the
  workaround (sign with Developer ID first).

GAP-C — No requestPermission() call in main.rs:

  The Tauri notification plugin exposes a ``requestPermission()``
  JavaScript API that wraps
  ``UNUserNotificationCenter.requestAuthorization(...)``. The Python
  sidecar's ``show_electron_notification`` IPC handler publishes the
  event but does NOT call ``requestPermission()`` first — it relies on
  the renderer's notification handler to do so. If the renderer doesn't
  call ``requestPermission()`` BEFORE the first ``notify()`` call, the
  notification silently no-ops on macOS 13+. This is a renderer-side
  concern; the Rust + Python wiring tested here is correct.

VALIDATE ON MACOS HOST:
1. Launch Voice Typer (must be signed with Developer ID for notifications to work)
2. Trigger a notification (e.g. complete a dictation → toast)
3. Verify a macOS notification appears (top-right corner)
4. If notification doesn't appear: System Settings → Notifications → Voice Typer → enable
5. Check ~/Library/Logs/voice-typer/voice-typer.log for:
   - notification event emitted
Expected: notification appears within 1s; title + message match
(Unsigned dev builds may not show notifications — sign with Developer ID first.)
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
# (tests/tauri/mig16/test_toast_macos.py → repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_TAURI = _REPO_ROOT / "src-tauri"
MAIN_RS = _SRC_TAURI / "src" / "main.rs"
WS_RS = _SRC_TAURI / "src" / "sidecar" / "ws.rs"
TAURI_CONF_JSON = _SRC_TAURI / "tauri.conf.json"
CAPABILITIES_JSON = (
    _SRC_TAURI / "capabilities" / "main-runtime.json"
)  # CR-5: migrate-runtime split; notification perms live in main-runtime
ENTITLEMENTS_PLIST = _SRC_TAURI / "entitlements.plist"
SYSTEM_HANDLERS_PY = _REPO_ROOT / "voice_typer" / "server" / "handlers" / "system_handlers.py"
MACOS_RUNBOOK = _REPO_ROOT / "docs" / "migration" / "macos-validation-runbook.md"


# ─── Helpers ──────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    """Read a file as UTF-8 text. Fail loud if missing — every path this
    module reads is a hard dependency of the macOS toast wiring, so a
    missing file is a real regression (not a soft skip)."""
    assert path.is_file(), f"required macOS toast-wiring artifact missing: {path}"
    return path.read_text(encoding="utf-8")


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
    """Gate 1: the Rust host must register the notification plugin.

    This is the CROSS-PLATFORM wiring — the same call works on macOS,
    Windows, and Linux. Tauri's notification plugin internally dispatches
    to ``UNUserNotificationCenter`` on macOS, ``WinRT ToastNotification``
    on Windows, and ``libnotify`` on Linux.
    """

    def test_main_rs_registers_tauri_plugin_notification_init(self):
        """``main.rs`` must contain a ``.plugin(tauri_plugin_notification::init())``
        call inside the ``tauri::Builder::default()`` chain.

        Without this, ``invoke('plugin:notification|notify', ...)`` from
        the webview returns "plugin not registered" — no notification
        banner ever appears on macOS, regardless of capability grants,
        tauri.conf.json config, or TCC permission.
        """
        src = _read(MAIN_RS)
        assert "tauri_plugin_notification::init()" in src, (
            "main.rs must register tauri_plugin_notification::init() in the "
            "Builder chain — without it, the webview's notification invoke() "
            "calls fail with 'plugin not registered' on macOS."
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


# ─── Test 2: tauri.conf.json declares "notification": {} in plugins ──────


class TestTauriConfDeclaresNotificationPlugin:
    """Gate 2: tauri.conf.json must list ``notification`` in the plugins section."""

    def test_tauri_conf_json_has_notification_in_plugins(self):
        """``tauri.conf.json`` MUST declare ``"notification": {}`` (or a
        populated object) under the top-level ``plugins`` key.

        Tauri v2 requires BOTH the Rust plugin registration AND the
        config entry — the config block is what triggers generation of
        the JS bindings the webview imports. Missing config ⇒
        ``@tauri-apps/plugin-notification`` import fails at runtime
        (this is identical on macOS + Windows + Linux).
        """
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        assert "plugins" in conf, (
            "tauri.conf.json must have a top-level 'plugins' object — Tauri v2 generates JS bindings from this section."
        )
        assert "notification" in conf["plugins"], (
            "tauri.conf.json plugins section must declare 'notification' — "
            "without it, the @tauri-apps/plugin-notification JS bindings "
            "are not generated and the webview's notify() call fails on macOS."
        )

    def test_notification_plugin_config_is_an_object(self):
        """The ``notification`` plugin config MUST be a JSON object (even
        if empty — ``{}``). A non-object value (e.g. ``null`` or
        ``true``) is a config schema violation that breaks
        ``cargo tauri build`` (which is the macOS universal-apple-darwin
        build that produces the signed ``.app`` bundle)."""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        notif_cfg = conf["plugins"]["notification"]
        assert isinstance(notif_cfg, dict), (
            f"tauri.conf.json plugins.notification must be a JSON object, got {type(notif_cfg).__name__}: {notif_cfg!r}"
        )


# ─── Test 3: capabilities grant notification permission ─────────────────


class TestCapabilitiesGrantNotificationPermission:
    """Gate 3: the migrate-runtime capability must grant a notification permission.

    Tauri v2 ships zero permissions by default — even with the plugin
    registered + the config entry, the webview's notify() call returns
    ``PermissionDenied`` unless an explicit ``notification:*`` permission
    is granted in a capability file the window matches. This is the
    same gate on macOS + Windows + Linux (capability enforcement is
    host-side, not OS-side).
    """

    def test_capabilities_grants_notification_allow_notify_or_default(self):
        """The ``migrate-runtime`` capability MUST grant at least one
        ``notification:*`` permission. We accept ``notification:default``
        (a Tauri v2 permission set bundling ``allow-notify`` + the
        permission-check helpers) OR ``notification:allow-notify`` (the
        canonical grant per runbook §6.4)."""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        assert "permissions" in cap, "migrate-runtime.json must declare a 'permissions' array."
        perms = cap["permissions"]
        assert isinstance(perms, list), f"capabilities 'permissions' must be a list, got {type(perms).__name__}"
        notif_perms = [p for p in perms if isinstance(p, str) and p.startswith("notification:")]
        assert notif_perms, (
            f"migrate-runtime.json must grant at least one 'notification:*' "
            f"permission — found none in {perms!r}. Without this, the "
            f"webview's notify() call returns PermissionDenied on macOS."
        )

    def test_capabilities_grants_notification_allow_notify_specifically(self):
        """The macOS runbook §6.4 specifically calls out
        ``notification:allow-notify`` as the required grant (the
        ``allow-notify`` permission gates the ``notify`` command, which
        is what posts the banner via ``UNUserNotificationCenter`` on
        macOS). Verify it's present (not just any ``notification:*``
        permission)."""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-notify" in perms, (
            f"migrate-runtime.json must grant 'notification:allow-notify' "
            f"(per macOS runbook §6.4 pass criteria). Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )

    def test_capabilities_grants_notification_request_permission(self):
        """Belt-and-braces: the capability also grants
        ``notification:allow-request-permission``. This is what lets the
        renderer call ``requestPermission()`` (which wraps
        ``UNUserNotificationCenter.requestAuthorization(...)`` on macOS).
        Without it, the renderer can't trigger the macOS TCC prompt
        described in gate 8 below, and notifications silently no-op
        because the user was never asked."""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-request-permission" in perms, (
            "migrate-runtime.json must grant 'notification:allow-request-permission' "
            "so the renderer can trigger the macOS UNUserNotificationCenter "
            "authorization prompt on first launch."
        )


# ─── Test 4: ws.rs renames electron_notification → notification ──────────


class TestWsRsRenamesElectronNotificationToNotification:
    """Gate 4: the WS reader has a CR-8 backward-compat alias that re-emits
    incoming ``electron_notification`` events under the canonical
    ``notification`` name.

    This is a CROSS-PLATFORM rename — the same ``ws.rs`` code runs on
    macOS, Windows, and Linux. The CR-8 rename moved the event-name
    migration logic out of the platform-specific tray code and into the
    Rust WS bridge so all three platforms get the same behavior for free.

    Source-inspection test: we read ``ws.rs`` as a string and assert the
    alias branch exists. We don't compile/run the Rust code (the Linux
    sandbox can't build the Tauri app — that's the whole point of the
    Phase 0-M gate).
    """

    def test_ws_rs_has_electron_notification_alias_branch(self):
        """``ws.rs`` MUST contain a branch that detects
        ``event_type == "electron_notification"`` and emits the payload
        under the ``notification`` name.

        This is the CR-8 backward-compat alias: a new UI subscribing to
        ``notification`` keeps working even if an old Python sidecar
        (still emitting ``electron_notification``) is rolling-upgraded
        in. The alias fires on all platforms (the WS bridge code is
        platform-independent).
        """
        src = _read(WS_RS)
        # The exact branch form (from ws.rs:143-145):
        #   if event_type == "electron_notification" {
        #       let _ = app_for_reader.emit("notification", payload.clone());
        #   }
        assert 'event_type == "electron_notification"' in src, (
            "ws.rs must have a backward-compat alias branch that detects "
            "'electron_notification' and re-emits under 'notification'."
        )
        assert 'emit("notification"' in src, (
            "ws.rs must emit under the canonical 'notification' name in the electron_notification alias branch."
        )

    def test_ws_rs_alias_branch_emits_notification_with_payload(self):
        """The alias branch must emit ``notification`` WITH the payload
        (``payload.clone()``), not just an empty event. Otherwise the
        webview's notification handler receives no title/message and the
        banner renders blank on macOS."""
        src = _read(WS_RS)
        # Confirm the alias emits with payload.clone() — the canonical
        # form per ws.rs:144.
        assert 'emit("notification", payload.clone())' in src or 'emit("notification", payload)' in src, (
            "ws.rs alias branch must emit 'notification' WITH the payload "
            "(payload.clone()), not just an empty event — otherwise the "
            "macOS banner renders blank."
        )

    def test_ws_rs_renames_relaunch_electron_to_relaunch_app(self):
        """Belt-and-braces: the same match arm also renames
        ``relaunch_electron`` → ``relaunch_app``. This is the other
        half of the CR-8 rename — it's how the Python sidecar's
        relaunch command maps to Tauri's ``app.restart()``. Verified
        here because the same match expression that carries the
        notification rename also carries this one; both renames MUST
        be present together."""
        src = _read(WS_RS)
        assert '"relaunch_electron" => "relaunch_app"' in src, (
            "ws.rs must rename 'relaunch_electron' → 'relaunch_app' in the "
            "same match arm that handles the electron_notification alias. "
            "Both renames MUST be present together (CR-8)."
        )


# ─── Test 5: notification payload shape ─────────────────────────────────


class TestNotificationPayloadShape:
    """Gate 5: the published notification payload shape.

    The published payload MUST match the platform-agnostic shape (per
    CR-8):

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

    This is the SAME shape on macOS, Windows, and Linux — the renderer's
    notification handler reads ``data.title`` + ``data.message`` and
    passes them to ``tauri-plugin-notification``'s ``notify()`` call,
    which on macOS dispatches to ``UNUserNotificationCenter.add(...)``.

    The ``duration_ms`` field is a hint for the auto-close timeout
    (macOS ignores it — banners follow the user's System Settings →
    Notifications → Voice Typer → banner style — but it's honored on
    Windows + Linux). The ``critical`` field requests critical-priority
    delivery (on macOS, this maps to a "Critical" banner that bypasses
    Do Not Disturb IF the user has granted critical-alert permission;
    without that grant it's silently downgraded to a regular banner).
    """

    def test_payload_shape_matches_actual_implementation(self):
        """Dispatching ``show_electron_notification`` MUST result in
        ``event_bus.publish`` being called with the canonical payload
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

        Notes:
          - Event name is ``notification`` (NOT ``electron_notification``)
            per CR-8. The legacy name only flows from OLD Python sidecars;
            the Rust-side alias in ``ws.rs`` re-emits it as
            ``notification`` for new UI code.
          - Body field is ``message`` (NOT ``body``) — this is the field
            name the renderer's notification handler reads when calling
            ``tauri-plugin-notification``'s ``notify({title, body})``.
          - Two extra fields (``duration_ms``, ``critical``) control the
            toast's auto-close timeout and priority (see class docstring
            for macOS behavior).
        """
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {
                        "title": "Transcription complete",
                        "message": "Inserted 42 words.",
                        "duration_ms": 4000,
                        "critical": False,
                    },
                    "id": "mig16-toast-shape",
                }
            )
        # Top-level shape.
        assert set(captured.keys()) == {"type", "data"}, (
            f"payload top-level keys must be {{'type', 'data'}}, got {set(captured.keys())!r}"
        )
        assert captured["type"] == "notification", (
            f"event name must be 'notification' (per CR-8) — got {captured.get('type')!r}"
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
        """The body field MUST be named ``message`` (NOT ``body``).

        This pins the actual implementation's field name. The renderer's
        notification handler reads ``data.message`` and passes it as the
        ``body`` argument to ``tauri-plugin-notification``'s
        ``notify({title, body})`` call. Renaming the field would break
        the renderer on macOS + Windows + Linux simultaneously.
        """
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {"title": "T", "message": "M"},
                    "id": "mig16-toast-field-name",
                }
            )
        assert "message" in captured["data"], (
            "payload data must have a 'message' field — this is the field "
            "name the renderer reads when calling tauri-plugin-notification's "
            "notify({title, body}) on macOS."
        )
        assert "body" not in captured["data"], (
            "payload data must NOT have a 'body' field — the actual "
            "implementation uses 'message' (the renderer maps data.message "
            "→ notify body)."
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
            server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {},
                    "id": "mig16-toast-defaults",
                }
            )
        assert captured["type"] == "notification"
        # APP_NAME is "Voice Typer" per voice_typer/server/branding.py.
        assert captured["data"]["title"] == "Voice Typer"
        assert captured["data"]["message"] == ""
        assert captured["data"]["duration_ms"] == 0
        assert captured["data"]["critical"] is False

    def test_payload_critical_field_is_bool_not_string(self):
        """The ``critical`` field MUST be a Python ``bool`` (which
        JSON-serializes to ``true``/``false``), NOT a string
        ``"true"``/``"false"``.

        On macOS, the renderer's notification handler reads
        ``data.critical`` and (if true) sets the
        ``UNNotificationContent.interruptionLevel`` to ``timeSensitive``
        (or ``critical`` if the user has granted critical-alert
        permission). A string value would always be truthy in JS,
        causing every notification to be escalated to critical — which
        would bypass Do Not Disturb without the user's consent."""
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {"title": "T", "message": "M", "critical": True},
                    "id": "mig16-toast-critical-type",
                }
            )
        assert isinstance(captured["data"]["critical"], bool), (
            f"payload 'critical' must be a bool (so JSON-serializes to "
            f"true/false), got {type(captured['data']['critical']).__name__}. "
            f"A string 'true' would always be truthy in JS, escalating every "
            f"notification to critical on macOS."
        )

    def test_payload_duration_ms_field_is_int_not_string(self):
        """The ``duration_ms`` field MUST be a Python ``int`` (which
        JSON-serializes to a number), NOT a string.

        macOS itself ignores ``duration_ms`` (banners follow the user's
        System Settings preference), but the renderer still reads it to
        decide whether to schedule an auto-dismiss timer on the
        in-app toast fallback (the small in-window toast the renderer
        shows IN ADDITION to the native banner, for cases where the
        banner is suppressed). A string value would break the renderer's
        ``setTimeout(..., data.duration_ms)`` call (NaN → no auto-dismiss)."""
        server = _make_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {"title": "T", "message": "M", "duration_ms": 3000},
                    "id": "mig16-toast-duration-type",
                }
            )
        assert isinstance(captured["data"]["duration_ms"], int), (
            f"payload 'duration_ms' must be an int (so JSON-serializes to a "
            f"number), got {type(captured['data']['duration_ms']).__name__}."
        )


# ─── Test 6: macOS notifications require app to be signed ────────────────


class TestMacOSNotificationsRequireSigning:
    """Gate 6: macOS notifications require the app to be SIGNED.

    macOS 11+ (Big Sur and newer) gates ``UNUserNotificationCenter``
    behind the app's signing identity. Unsigned dev builds
    (``cargo tauri build`` without a Developer ID Application
    certificate + ``codesign``) silently fail to post notifications —
    ``UNUserNotificationCenter.add(...)`` returns
    ``notificationNotScheduled`` without any visible error in the
    app's log.

    The ``entitlements.plist`` file in this repo IS used by the
    ``codesign`` invocation (per the file's docstring), which means the
    signing step is expected to run as part of the release build. This
    test verifies the entitlements.plist file exists (a hard dependency
    of the macOS signing step) + that the signing-guide doc references
    the Developer ID requirement.

    The actual "is the .app bundle signed with a Developer ID
    certificate?" check MUST be done on the macOS host (see VALIDATE
    ON MACOS HOST block — `codesign -dv --verbose=4 /Applications/Voice
    Typer.app` should show ``Authority=Developer ID Application: ...``).
    """

    def test_entitlements_plist_exists(self):
        """The ``entitlements.plist`` file MUST exist in the repo —
        it's consumed by the ``codesign`` invocation that signs the
        ``.app`` bundle (per the file's own docstring). Without it,
        the signing step can't apply the hardened-runtime entitlements,
        which means the bundle can't be notarized, which means
        notifications silently fail on macOS 11+."""
        assert ENTITLEMENTS_PLIST.is_file(), (
            f"entitlements.plist MUST exist at {ENTITLEMENTS_PLIST} — "
            f"it's consumed by codesign when signing the .app bundle. "
            f"Without signing, macOS notifications silently fail."
        )

    def test_signing_guide_documents_developer_id_requirement(self):
        """The signing guide MUST document the Developer ID Application
        certificate requirement for posting notifications on macOS.

        This is a source-inspection test: we read the signing guide and
        assert it mentions "Developer ID" (the certificate class required
        for notarization + notification posting). Ad-hoc signing (the
        default for unsigned dev builds) is NOT sufficient for
        notifications on macOS 13+."""
        signing_guide = _REPO_ROOT / "docs" / "migration" / "signing-guide.md"
        assert signing_guide.is_file(), (
            f"signing-guide.md MUST exist at {signing_guide} — it documents "
            f"the macOS Developer ID signing flow required for notifications."
        )
        src = _read(signing_guide)
        assert "Developer ID" in src, (
            "signing-guide.md MUST mention 'Developer ID' — the Developer ID "
            "Application certificate is required for macOS notifications on "
            "macOS 13+ (unsigned dev builds silently fail to post)."
        )

    def test_macos_runbook_documents_signing_prerequisite(self):
        """The macOS validation runbook MUST list the Developer ID
        certificate as a prerequisite (per ADR-0020 §13.2 + the
        runbook's own prerequisites section).

        This pins the docs-side contract: a developer running gate
        check 6 must be able to discover the signing requirement from
        the runbook, not from a GitHub issue or a Slack thread."""
        src = _read(MACOS_RUNBOOK)
        assert "Developer ID" in src, (
            "macos-validation-runbook.md MUST mention 'Developer ID' in its "
            "prerequisites — gate check 6 (toast) requires a signed .app bundle."
        )


# ─── Test 7: entitlements.plist does NOT need notification entitlement ───


class TestEntitlementsDoNotIncludeNotificationEntitlement:
    """Gate 7: the entitlements.plist does NOT need a notification-specific
    entitlement.

    Unlike iOS (where ``com.apple.developer.usernotifications.communication``
    is required for communication notifications) or APNs push
    notifications (where ``aps-environment`` is required), LOCAL macOS
    notifications via ``UNUserNotificationCenter`` need NO
    notification-specific entitlement. The existing entitlements
    (audio-input + allow-jit + disable-library-validation) are
    sufficient — adding a notification entitlement would be cargo-cult
    and would NOT change behavior.

    This is a NEGATIVE test: we verify the entitlements.plist does NOT
    contain notification-specific keys. (If it did, that would indicate
    a misunderstanding of macOS TCC vs. iOS entitlements, and would
    risk breaking the hardened-runtime validation.)
    """

    def test_entitlements_does_not_contain_notification_entitlement(self):
        """The ``entitlements.plist`` MUST NOT contain any of:

          - ``com.apple.developer.usernotifications.communication``
            (iOS-only — communication-notification entitlement)
          - ``com.apple.developer.usernotifications.time-sensitive``
            (iOS-only — time-sensitive entitlement; macOS handles this
            via the ``interruptionLevel`` field on
            ``UNNotificationContent``, not via entitlement)
          - ``aps-environment`` (APNs push — irrelevant for local
            notifications)

        These keys are documented in Apple's entitlements reference as
        iOS-only or APNs-only. Adding them to a macOS app's entitlements
        would be cargo-cult: they don't change behavior, but they DO
        make the entitlements file harder to audit (a reviewer would
        reasonably wonder "why is aps-environment here — are we using
        push?").
        """
        src = _read(ENTITLEMENTS_PLIST)
        forbidden_keys = [
            "com.apple.developer.usernotifications.communication",
            "com.apple.developer.usernotifications.time-sensitive",
            "aps-environment",
        ]
        for key in forbidden_keys:
            assert key not in src, (
                f"entitlements.plist MUST NOT contain '{key}' — this is an "
                f"iOS-only or APNs-only entitlement. Local macOS notifications "
                f"via UNUserNotificationCenter need NO notification-specific "
                f"entitlement; the existing audio-input + allow-jit + "
                f"disable-library-validation entitlements are sufficient. "
                f"Adding it is cargo-cult and makes the file harder to audit."
            )

    def test_entitlements_contains_required_hardened_runtime_keys(self):
        """Belt-and-braces: the entitlements.plist MUST contain the
        three hardened-runtime entitlements required for notarization
        (per the file's own docstring + ADR-0020 §13.2):

          - ``com.apple.security.cs.allow-jit``
          - ``com.apple.security.cs.disable-library-validation``
          - ``com.apple.security.device.audio-input``

        These are NOT notification-specific — they're the hardened-
        runtime minimum. But they're a hard dependency of the signing
        step that gates notifications (unsigned ⇒ no notifications),
        so we verify them here as a proxy for "the entitlements file
        is well-formed for the signing flow that gates notifications."""
        src = _read(ENTITLEMENTS_PLIST)
        required_keys = [
            "com.apple.security.cs.allow-jit",
            "com.apple.security.cs.disable-library-validation",
            "com.apple.security.device.audio-input",
        ]
        for key in required_keys:
            assert key in src, (
                f"entitlements.plist MUST contain '{key}' — it's a "
                f"hardened-runtime minimum required for notarization (per "
                f"ADR-0020 §13.2). Without it, the signing step fails, "
                f"which means macOS notifications silently fail too."
            )


# ─── Test 8: macOS 13+ requires user to grant notification permission ────


class TestMacOSNotificationPermissionGrant:
    """Gate 8: macOS 13+ (Ventura and newer) requires the user to grant
    notification permission in System Settings → Notifications → Voice
    Typer.

    macOS 11+ (Big Sur) introduced per-app notification authorization
    via ``UNUserNotificationCenter.requestAuthorization(...)``. The app
    MUST call this on first launch (or on first ``notify()`` invoke);
    the Tauri notification plugin wraps this in its
    ``requestPermission()`` JS API. The renderer is responsible for
    calling ``requestPermission()`` BEFORE the first ``notify()`` —
    otherwise the notification silently no-ops (no banner, no error in
    the log).

    This is documented as GAP-C in the module docstring (the renderer's
    ``requestPermission()`` call is outside the scope of this Linux-
    sandbox test file). What we CAN test in source is that:

      - The capability grants ``notification:allow-request-permission``
        (verified in Test 3 above).
      - The macOS runbook §6.4 documents the user-grant step.
      - The macOS runbook §6.4 documents the System Settings →
        Notifications → Voice Typer fallback path.
    """

    def test_macos_runbook_documents_unusernotificationcenter_authorization(self):
        """The macOS runbook §6.4 MUST mention
        ``UNUserNotificationCenter`` authorization — this is the API
        surface that requires the user grant on macOS 11+. The runbook
        is the source-of-truth docs for the Phase 0-M gate; a developer
        running gate check 6 must be able to discover the
        authorization requirement from §6.4."""
        src = _read(MACOS_RUNBOOK)
        assert "UNUserNotificationCenter" in src, (
            "macos-validation-runbook.md MUST mention 'UNUserNotificationCenter' "
            "— this is the macOS API surface that requires user authorization "
            "for notifications on macOS 11+."
        )

    def test_macos_runbook_documents_system_settings_notifications_fallback(self):
        """The macOS runbook MUST document the manual fallback path
        (System Settings → Notifications → Voice Typer → enable) for
        when the notification doesn't appear. This is the macOS 13+
        equivalent of the iOS Settings → App → Notifications flow —
        a developer whose notification silently no-ops needs to know
        where to look."""
        src = _read(MACOS_RUNBOOK)
        # The runbook should mention both "System Settings" and
        # "Notifications" (case-insensitive) — together they pin the
        # macOS System Settings → Notifications UI path.
        src_lower = src.lower()
        assert "system settings" in src_lower or "system preferences" in src_lower, (
            "macos-validation-runbook.md MUST mention 'System Settings' (or "
            "the legacy 'System Preferences') — the macOS UI path where the "
            "user manually grants notification permission."
        )
        assert "notification" in src_lower, (
            "macos-validation-runbook.md MUST mention 'notification' — the "
            "System Settings panel where the user grants per-app notification "
            "permission on macOS 13+."
        )

    def test_macos_runbook_documents_notification_permission_pass_criteria(self):
        """The macOS runbook §6.4 MUST document the pass criteria for
        the notification gate (a banner appears in the top-right
        corner with the Voice Typer icon). This is the human-verifiable
        assertion that gate check 6 is "passing" — without it, the
        gate check is a no-op (the renderer's notify() call returning
        without error is NOT sufficient; macOS silently swallows
        notifications from unsigned/unauthorized apps)."""
        src = _read(MACOS_RUNBOOK)
        # The runbook should mention "top-right" (where macOS banners
        # appear) AND "Notification Center" (where they persist).
        assert "top-right" in src or "top right" in src, (
            "macos-validation-runbook.md MUST mention 'top-right' — the "
            "screen position where macOS notification banners appear. This "
            "is the human-verifiable pass criteria for gate check 6."
        )

    def test_macos_runbook_documents_nsusernotificationsusagedescription(self):
        """The macOS runbook §6.4 MUST document the
        ``NSUserNotificationsUsageDescription`` Info.plist key. This
        key is the human-readable description shown in the macOS TCC
        prompt when the app first requests notification authorization.
        Without it, the TCC prompt shows a generic "Voice Typer Would
        Like to Send You Notifications" message — functional but not
        user-friendly.

        IMPLEMENTATION GAP (GAP-A in module docstring): the Info.plist
        template that should declare this key is NOT in the repo — it's
        generated by ``cargo tauri build`` from defaults. The runbook
        documents the requirement; the build-script fix is tracked
        separately. This test only verifies the runbook documents it."""
        src = _read(MACOS_RUNBOOK)
        assert "NSUserNotificationsUsageDescription" in src, (
            "macos-validation-runbook.md MUST mention "
            "'NSUserNotificationsUsageDescription' — the Info.plist key "
            "that provides the human-readable description for the macOS "
            "TCC notification authorization prompt."
        )


# ─── Test 9: source-inspection belt-and-braces ───────────────────────────


class TestSourceInspectionBeltAndBraces:
    """Gate 9: belt-and-braces source-inspection tests.

    These don't correspond to a single gate point — they pin additional
    invariants that would be easy to break in a refactor but would
    silently regress the macOS toast path if broken.
    """

    def test_ws_rs_emits_python_event_envelope(self):
        """``ws.rs`` MUST also emit the generic ``python-event``
        envelope (per ADR-0020 §6.3) which the ``usePython`` hook's
        onEvent catch-all listens to. This is the secondary path by
        which the renderer learns about a notification event — both
        paths (specific-event emit + python-event envelope) must be
        present for the toast wiring to be complete on macOS."""
        src = _read(WS_RS)
        assert 'emit("python-event"' in src, (
            "ws.rs must also emit the generic 'python-event' envelope "
            "(ADR-0020 §6.3) — this is the catch-all path the usePython "
            "hook uses to learn about notification events on macOS."
        )

    def test_ws_rs_emits_specific_event_with_emit_name(self):
        """``ws.rs`` MUST emit the specific event (using ``emit_name``)
        so direct listeners like ``appWindow.on('notification')`` keep
        firing. The generic ``python-event`` envelope is NOT sufficient
        — direct listeners don't subscribe to that."""
        src = _read(WS_RS)
        assert "emit(emit_name" in src, (
            "ws.rs must emit the specific event using `emit_name` (the "
            "result of the match arm) — this is what carries the canonical "
            "'notification' name to direct UI listeners on macOS."
        )

    def test_ws_rs_has_other_arm_passthrough(self):
        """``ws.rs`` MUST have an ``other => other`` pass-through arm
        in the emit_name match — this is what carries the legacy
        ``electron_notification`` event name through unchanged (so old
        UI listeners keep working during the rolling upgrade). The
        specific alias branch (Test 4) then re-emits it as
        ``notification`` for new UI code."""
        src = _read(WS_RS)
        assert "other => other" in src, (
            "ws.rs must have an `other => other` pass-through arm in the "
            "emit_name match — this is what carries the legacy "
            "'electron_notification' name through unchanged on macOS."
        )

    def test_system_handlers_publishes_notification_event(self):
        """The Python sidecar's ``system_handlers.py`` MUST publish a
        ``notification`` event (per CR-8) — NOT the legacy
        ``electron_notification`` name. This is a source-inspection
        test: we read ``system_handlers.py`` and assert the canonical
        event name is present in the publish call.

        This pins the Python-side half of the CR-8 rename. The Rust-
        side alias (Test 4) handles OLD Python sidecars that still emit
        the legacy name during a rolling upgrade."""
        src = _read(SYSTEM_HANDLERS_PY)
        assert '"type": "notification"' in src, (
            "system_handlers.py MUST publish with type='notification' "
            "(per CR-8) — NOT the legacy 'electron_notification' name. "
            "The Rust-side alias in ws.rs handles old Python sidecars; "
            "the NEW Python sidecar must emit the canonical name."
        )

    def test_system_handlers_does_not_publish_legacy_event_name(self):
        """The Python sidecar's ``system_handlers.py`` MUST NOT publish
        a ``"type": "electron_notification"`` event (per CR-8 — the
        legacy name was renamed at the source).

        This is a NEGATIVE test: we verify the legacy name is NOT used
        as a published event type. (The legacy name may still appear
        in COMMENTS or docstrings documenting the rename — that's fine.
        What we're checking is that no ``event_bus.publish({"type":
        "electron_notification", ...})`` call exists.)"""
        src = _read(SYSTEM_HANDLERS_PY)
        # The exact form that would indicate a regression: a publish
        # with type "electron_notification". We check for the string
        # literal in a publish context.
        assert '"type": "electron_notification"' not in src, (
            "system_handlers.py MUST NOT publish with type='electron_notification' "
            "(per CR-8 — the legacy name was renamed at the source). Only the "
            "Rust-side alias in ws.rs should reference the legacy name, for "
            "backward compat with old Python sidecars."
        )

    def test_macos_runbook_lists_toast_as_gate_point(self):
        """The macOS runbook MUST list the toast notification check as
        a numbered gate point (§6.4 — "tauri-plugin-notification posts
        a notification (gate point 5, BOTH arches)"). This pins the
        runbook-side contract: the toast gate is one of the 9 Phase
        0-M gates, and a developer running the gate sequence must
        encounter it."""
        src = _read(MACOS_RUNBOOK)
        # The runbook's gate-point header for notifications:
        # "Step 6.4 — `tauri-plugin-notification` posts a notification
        #  (gate point 5, BOTH arches)"
        assert "tauri-plugin-notification" in src, (
            "macos-validation-runbook.md MUST mention 'tauri-plugin-notification' "
            "as a gate-point header — gate check 6 is the toast gate."
        )
        assert "gate point 5" in src, (
            "macos-validation-runbook.md MUST label the toast gate as "
            "'gate point 5' (per ADR-0020 §6.4 — the gate-point numbering "
            "is the canonical reference for the 9 Phase 0-M gates)."
        )


# ─── Test 10: VALIDATE ON MACOS HOST block is documented ────────────────


class TestValidateOnMacOSHostBlock:
    """Gate 10: the VALIDATE ON MACOS HOST block (in this file's module
    docstring) MUST contain the canonical validation commands.

    This is a meta-test: we verify the module docstring (which is the
    human-readable runbook for the macOS host validation step) contains
    the expected command sequence. The actual validation is performed
    by a human on a real macOS host — this test just pins the docs
    contract so the commands don't drift.
    """

    @staticmethod
    def _module_docstring() -> str:
        """Return the module-level docstring of this file.

        We use ``__doc__`` rather than re-reading the file so the test
        is robust to formatting changes (e.g. if someone reformats the
        docstring with ``black``).
        """
        return __doc__ or ""

    def test_docstring_contains_validate_on_macos_host_header(self):
        """The module docstring MUST contain the
        ``VALIDATE ON MACOS HOST:`` header — this is the canonical
        marker the macOS host validator scans for."""
        doc = self._module_docstring()
        assert "VALIDATE ON MACOS HOST:" in doc, (
            "Module docstring MUST contain 'VALIDATE ON MACOS HOST:' header — "
            "this is the canonical marker the macOS host validator scans for."
        )

    def test_docstring_documents_signing_prerequisite(self):
        """The VALIDATE ON MACOS HOST block MUST mention the Developer
        ID signing requirement ("must be signed with Developer ID for
        notifications to work"). This is the single most common reason
        a developer's local macOS toast test silently fails — the
        block must call it out."""
        doc = self._module_docstring()
        assert "Developer ID" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'Developer ID' — "
            "unsigned dev builds silently fail to post notifications on macOS."
        )
        assert "signed" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'signed' — the signing prerequisite for macOS notifications."
        )

    def test_docstring_documents_system_settings_fallback(self):
        """The VALIDATE ON MACOS HOST block MUST document the System
        Settings → Notifications fallback path (step 4 in the canonical
        command sequence). This is what the validator does when the
        notification doesn't appear — grant the permission manually."""
        doc = self._module_docstring()
        assert "System Settings" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'System Settings' — "
            "the macOS UI path where the user manually grants notification "
            "permission if the TCC prompt was dismissed."
        )
        assert "Notifications" in doc, (
            "VALIDATE ON MACOS HOST block MUST mention 'Notifications' — the "
            "System Settings panel name for per-app notification permission."
        )

    def test_docstring_documents_log_path(self):
        """The VALIDATE ON MACOS HOST block MUST document the expected
        log path (``~/Library/Logs/voice-typer/voice-typer.log``) so the
        validator can check for the ``notification event emitted`` line
        that confirms the Python sidecar fired the event (even if the
        native banner was suppressed)."""
        doc = self._module_docstring()
        assert "~/Library/Logs/voice-typer/voice-typer.log" in doc, (
            "VALIDATE ON MACOS HOST block MUST document the macOS log path "
            "(~/Library/Logs/voice-typer/voice-typer.log) so the validator "
            "can confirm the notification event was emitted."
        )

    def test_docstring_documents_unsigned_dev_build_caveat(self):
        """The VALIDATE ON MACOS HOST block MUST document the unsigned-
        dev-build caveat (the last line of the block: "Unsigned dev
        builds may not show notifications — sign with Developer ID
        first."). This is the troubleshooting hint for the most common
        silent-failure mode on a developer's local macOS host."""
        doc = self._module_docstring()
        assert "Unsigned dev builds" in doc, (
            "VALIDATE ON MACOS HOST block MUST document the unsigned-dev-build "
            "caveat ('Unsigned dev builds may not show notifications — sign "
            "with Developer ID first.') — this is the troubleshooting hint "
            "for the most common silent-failure mode on macOS."
        )

    def test_docstring_documents_expected_timing(self):
        """The VALIDATE ON MACOS HOST block MUST document the expected
        timing ("notification appears within 1s") so the validator knows
        how long to wait before declaring the gate failed. macOS banners
        appear near-instantly (the UNUserNotificationCenter.add() call
        is async but completes in <500ms in practice); 1s is the
        generous upper bound."""
        doc = self._module_docstring()
        assert "within 1s" in doc, (
            "VALIDATE ON MACOS HOST block MUST document the expected timing "
            "('within 1s') — the upper bound for how long the validator should "
            "wait for the banner before declaring the gate failed."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
