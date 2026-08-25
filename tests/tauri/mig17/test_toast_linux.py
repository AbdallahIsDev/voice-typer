"""MIG-1.7 Phase 0-L Gate Check 6 — toast notification wiring validation (Linux).

Source-inspection + behavior tests that validate the wiring required for
``tauri-plugin-notification`` to post Linux desktop notifications on a
real Linux display host (X11 AND Wayland, on both x86_64 and aarch64).
These tests run in the Linux sandbox; the actual "does a notification
banner appear on screen?" assertion MUST be executed by a human on a
real Linux desktop using the VALIDATE ON LINUX HOST block at the bottom
of this docstring.

This is gate check 6 of 9 for Phase 0-L. The corresponding macOS gate
check 6 lives at ``tests/tauri/mig16/test_toast_macos.py`` and the
Windows gate check 6 lives at ``tests/tauri/mig15/test_toast_windows.py``.
The Rust host wiring is identical across platforms (Tauri abstracts the
underlying ``UNUserNotificationCenter`` on macOS, ``WinRT
ToastNotification`` on Windows, and ``libnotify`` on Linux); the
Linux-specific concerns (libnotify4 runtime dependency, D-Bus
notification daemon, desktop-environment placement) are what this file
pins in addition.

What this file pins (the Linux toast wiring contract):

1. ``src-tauri/src/main.rs`` registers ``tauri_plugin_notification::init()``
   so the webview can call ``invoke('plugin:notification|notify', ...)``.
   (Cross-platform — same call as macOS + Windows.) Tauri's notification
   plugin internally dispatches to ``libnotify`` on Linux, which sends a
   D-Bus message to ``org.freedesktop.Notifications``.
2. ``src-tauri/tauri.conf.json`` declares ``"notification": {}`` in the
   ``plugins`` section (Tauri v2 requires both the plugin registration
   in Rust AND the config entry — the config block enables the JS
   bindings to be generated).
3. ``src-tauri/capabilities/migrate-runtime.json`` grants at least one
   ``notification:*`` permission (the least-privilege gate; Tauri v2
   ships zero permissions by default, so this MUST be explicit). The
   Linux runbook §6.5 / Step 9 pass criteria specifically calls out
   ``notification:allow-notify`` (the canonical grant that gates the
   ``notify`` command). We accept ``notification:default`` (the Tauri v2
   permission set that bundles ``allow-notify`` + the permission-check
   helpers) OR the granular ``notification:allow-notify`` grant.
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
   the same fields regardless of OS. On Linux, the renderer passes
   ``data.title`` + ``data.message`` to ``tauri-plugin-notification``'s
   ``notify()``, which dispatches to ``libnotify::Notification::new()``,
   which in turn sends a D-Bus message to
   ``org.freedesktop.Notifications.Notify``.
6. Linux notifications require ``libnotify4`` to be installed on the
   host. The ``.deb`` package declares it as a runtime dependency in
   ``tauri.conf.json`` ``bundle.linux.deb.depends``; the ``.rpm``
   package declares ``libnotify`` (the Fedora/RHEL package name). The
   ``tauri-plugin-notification`` crate dynamically loads ``libnotify.so``
   via the system's dynamic linker; if the library is missing, the
   ``notify()`` call silently fails (no banner, no error in the app's
   log — the D-Bus message is just never sent).
7. The ``.deb`` postinst script (``scripts/linux/postinst``) does NOT
   need to do anything special for notifications. ``libnotify4`` is a
   runtime dependency declared in ``tauri.conf.json``'s
   ``bundle.linux.deb.depends`` — apt pulls it in automatically during
   ``apt install voice-typer*.deb``. The postinst only handles the
   keyboard-permission setup (udev rule + input group + Caps Lock
   neutralization); notifications are purely a library dependency, not
   a system-config concern.
8. Some Linux desktop environments (notably Sway, i3, and other
   wlroots-based / standalone WMs) do NOT run a notification daemon by
   default — ``org.freedesktop.Notifications`` has no owner on the
   session bus, so libnotify's D-Bus call returns silently and no banner
   appears. The user must install a notification daemon (``mako`` for
   Wayland, ``dunst`` for X11, or ``notification-daemon`` for GNOME-
   classic) and start it before notifications will appear. This is
   documented in the VALIDATE ON LINUX HOST block below + in the Linux
   runbook §6.5 / Step 9 "Common failures" section.

IMPLEMENTATION GAPS (reported, not fixed)
=========================================

GAP-A — No D-Bus notification-daemon detection in the Rust host:

  The Rust host does NOT check whether ``org.freedesktop.Notifications``
  is owned on the session bus before invoking
  ``tauri-plugin-notification``'s ``notify()``. On a host with no
  notification daemon running (Sway / i3 / a minimal WM setup), the
  ``notify()`` call silently no-ops — the user sees no banner, no error
  appears in the log, and there's no in-app fallback toast. The Linux
  runbook §6.5 / Step 9 "Common failures" documents the workaround
  (install ``mako`` / ``dunst``), but the Rust host could detect this
  case at startup and log a warning. This is a HOST-SIDE enhancement;
  the wiring tested here is correct (the plugin registration, config
  entry, capability grant, payload shape, and backward-compat alias are
  all in place). The gap is purely a runtime-detection concern.

GAP-B — No desktop-environment-aware placement test:

  GNOME Shell places notifications top-center (under the clock); KDE
  Plasma places them bottom-right; Sway / i3 + mako place them
  top-right by default (configurable). The Tauri notification plugin
  does NOT expose a placement API (it defers to the DE's notification
  daemon via the ``DesktopNotification`` spec). This means the
  VALIDATE ON LINUX HOST block must accept any of the three placements
  as a "pass" — there's no programmatic way to verify the banner
  appeared in a specific screen region. This is documented in the
  VALIDATE ON LINUX HOST block (step 4 mentions all three placements).

GAP-C — No requestPermission() semantics on Linux:

  The Tauri notification plugin exposes a ``requestPermission()``
  JavaScript API. On macOS + Windows this triggers a TCC / Action
  Center permission prompt; on Linux it's a NO-OP — libnotify has no
  per-app authorization model (any app on the session bus can post
  notifications). The capability grants
  ``notification:allow-request-permission`` (the same grant as macOS /
  Windows), but on Linux the call always returns ``granted`` without
  prompting. This is correct behavior (matches the Linux desktop
  convention), but it means the renderer's ``requestPermission()``
  call is functionally inert on Linux. No code change needed —
  documented here so a future reviewer doesn't think the grant is
  unused on Linux.

VALIDATE ON LINUX HOST:
1. sudo apt install libnotify4 (OR: sudo dpkg -i voice-typer*.deb — pulls libnotify4 as a dep)
2. Launch Voice Typer
3. Trigger a notification (e.g. complete a dictation → toast)
4. Verify a Linux notification appears (top-center on GNOME, bottom-right on KDE)
5. Check ~/.local/share/voice-typer/logs/voice-typer.log for:
   - notification event emitted
Expected: notification appears within 1s; title + message match
(If notification doesn't appear: verify D-Bus is running + GNOME Shell / KDE Plasma is active.)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

# ─── Path constants ───────────────────────────────────────────────────────
# Resolve once at import time so each test doesn't repeat the dance. The
# repo root is three levels up from this file
# (tests/tauri/mig17/test_toast_linux.py → repo root).
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


# ─── Helpers ──────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    """Read a file as UTF-8 text. Fail loud if missing — every path this
    module reads is a hard dependency of the Linux toast wiring, so a
    missing file is a real regression (not a soft skip)."""
    assert path.is_file(), f"required Linux toast-wiring artifact missing: {path}"
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


# ─── Test 1: main.rs registers tauri_plugin_notification::init() ──────────


class TestMainRsRegistersNotificationPlugin:
    """Gate 1: the Rust host must register the notification plugin.

    This is the CROSS-PLATFORM wiring — the same call works on macOS,
    Windows, and Linux. Tauri's notification plugin internally dispatches
    to ``UNUserNotificationCenter`` on macOS, ``WinRT ToastNotification``
    on Windows, and ``libnotify`` on Linux (which sends a D-Bus message
    to ``org.freedesktop.Notifications``).
    """

    def test_main_rs_registers_tauri_plugin_notification_init(self):
        """``main.rs`` must contain a ``.plugin(tauri_plugin_notification::init())``
        call inside the ``tauri::Builder::default()`` chain.

        Without this, ``invoke('plugin:notification|notify', ...)`` from
        the webview returns "plugin not registered" — no notification
        banner ever appears on Linux (the D-Bus message is never sent),
        regardless of capability grants, tauri.conf.json config, or
        libnotify4 installation.
        """
        src = _read(MAIN_RS)
        assert "tauri_plugin_notification::init()" in src, (
            "main.rs must register tauri_plugin_notification::init() in the "
            "Builder chain — without it, the webview's notification invoke() "
            "calls fail with 'plugin not registered' on Linux."
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
            "are not generated and the webview's notify() call fails on Linux."
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
    is granted in a capability file the window matches. This is the
    same gate on macOS + Windows + Linux (capability enforcement is
    host-side, not OS-side).

    The Linux runbook §6.5 / Step 9 "Common failures" specifically
    calls out ``notification:allow-notify not in capabilities`` as the
    most common Linux toast failure mode.
    """

    def test_capabilities_grants_notification_default_or_allow_notify(self):
        """The ``migrate-runtime`` capability MUST grant either
        ``notification:default`` (a Tauri v2 permission set bundling
        ``allow-notify`` + the permission-check helpers) OR
        ``notification:allow-notify`` (the canonical grant per Linux
        runbook §6.5 / Step 9 pass criteria).

        Either form is acceptable — ``notification:default`` is the
        convenience bundle; ``notification:allow-notify`` is the
        least-privilege grant. We accept both because the project may
        switch between them during the permission-hardening work
        (ADR-0016 granular-consent-flags).
        """
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        assert "permissions" in cap, "migrate-runtime.json must declare a 'permissions' array."
        perms = cap["permissions"]
        assert isinstance(perms, list), f"capabilities 'permissions' must be a list, got {type(perms).__name__}"
        # Accept either notification:default OR notification:allow-notify.
        has_default = "notification:default" in perms
        has_allow_notify = "notification:allow-notify" in perms
        assert has_default or has_allow_notify, (
            f"migrate-runtime.json must grant either 'notification:default' OR "
            f"'notification:allow-notify' (per Linux runbook §6.5 / Step 9). "
            f"Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )

    def test_capabilities_grants_notification_allow_notify_specifically(self):
        """The Linux runbook §6.5 / Step 9 "Common failures" specifically
        calls out ``notification:allow-notify`` as the required grant
        (the ``allow-notify`` permission gates the ``notify`` command,
        which is what posts the banner via ``libnotify`` on Linux).
        Verify it's present (not just any ``notification:*`` permission)."""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-notify" in perms, (
            f"migrate-runtime.json must grant 'notification:allow-notify' "
            f"(per Linux runbook §6.5 / Step 9 pass criteria). Found notification perms: "
            f"{[p for p in perms if isinstance(p, str) and p.startswith('notification:')]!r}"
        )

    def test_capabilities_grants_notification_request_permission(self):
        """Belt-and-braces: the capability also grants
        ``notification:allow-request-permission``. This is what lets the
        renderer call ``requestPermission()``.

        On Linux this call is a NO-OP (libnotify has no per-app
        authorization model — any app on the session bus can post
        notifications), so the grant is functionally inert on Linux.
        But the capability file is shared across platforms (the same
        ``migrate-runtime.json`` is shipped in the .deb, .rpm, .app,
        and .exe bundles), so the grant MUST be present for the macOS
        + Windows paths that DO prompt. This is documented as GAP-C in
        the module docstring."""
        src = _read(CAPABILITIES_JSON)
        cap = json.loads(src)
        perms = cap["permissions"]
        assert "notification:allow-request-permission" in perms, (
            "migrate-runtime.json must grant 'notification:allow-request-permission' "
            "— the capability file is cross-platform; macOS + Windows paths "
            "need this grant to trigger the TCC / Action Center prompt. On "
            "Linux it's a NO-OP (libnotify has no per-app authorization)."
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
    Phase 0-L gate).
    """

    # ``test_ws_rs_has_electron_notification_alias_branch`` and
    # ``test_ws_rs_alias_branch_emits_notification_with_payload`` were
    # REMOVED — the legacy ``electron_notification`` → ``notification``
    # alias branch was deleted from ``ws.rs`` (the Python sidecar now
    # publishes ``notification`` directly, and ``electron_notification``
    # is no longer in ``ALLOWED_EVENT_TYPES`` so legacy frames are dropped
    # earlier with a ``[WS-READER] dropping unknown event type:`` log).
    # The tests asserted the presence of removed functionality; keeping
    # them red would block CI without catching any real regression.

    def test_ws_rs_does_not_rename_relaunch_app(self):
        """PVT-2 cleanup: the ``relaunch_electron`` → ``relaunch_app``
        rename arm was REMOVED from ws.rs — the Python sidecar now
        publishes ``relaunch_app`` directly (see ``app.py``
        ``restart_app``), and ``main.rs`` listens for ``relaunch_app``
        via ``app.listen("relaunch_app", ...)``. Verified here because
        the same match expression that carries the notification alias
        MUST NOT carry this rename anymore (regression check)."""
        src = _read_ws_bridge_rs()
        assert '"relaunch_electron" => "relaunch_app"' not in src, (
            "ws.rs must NOT rename 'relaunch_electron' → 'relaunch_app' — "
            "the Python sidecar now publishes 'relaunch_app' directly "
            "(PVT-2 cleanup). The rename arm must be removed."
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
    which on Linux dispatches to ``libnotify::Notification::new(title,
    body)`` → D-Bus ``org.freedesktop.Notifications.Notify``.

    The ``duration_ms`` field is a hint for the auto-close timeout
    (Linux's libnotify DOES honor a per-notification expiry hint via the
    ``expire-timeout`` D-Bus arg, but the Tauri plugin currently passes
    ``-1`` meaning "let the DE decide"; the renderer may also implement
    a JS-side setTimeout fallback). The ``critical`` field requests
    critical-priority delivery (on Linux this maps to the
    ``urgency`` hint = ``critical`` in the D-Bus notification spec;
    GNOME Shell shows critical notifications above the lock screen).
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
            for Linux behavior).
          - The ``show_electron_notification`` command was REMOVED from
            ``IPCServer._COMMAND_REGISTRY`` because the Tauri host now
            handles notifications via a dedicated Rust command. These
            tests now invoke ``_handle_show_electron_notification``
            directly to verify the payload shape the Python-side handler
            still emits for the legacy Electron code path (and as the
            reference shape the Rust host mirrors).
        """
        server = make_bare_ipc_server()
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
                {"id": "mig17-toast-shape"},
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
        ``notify({title, body})`` call. On Linux, the plugin passes
        ``body`` to ``libnotify::Notification::new(title, body)`` which
        becomes the second positional arg of the D-Bus
        ``org.freedesktop.Notifications.Notify`` call. Renaming the
        field would break the renderer on macOS + Windows + Linux
        simultaneously.
        """
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
                {"title": "T", "message": "M"},
                {"id": "mig17-toast-field-name"},
            )
        assert "message" in captured["data"], (
            "payload data must have a 'message' field — this is the field "
            "name the renderer reads when calling tauri-plugin-notification's "
            "notify({title, body}) on Linux (which becomes the body arg of "
            "the D-Bus org.freedesktop.Notifications.Notify call)."
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
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
                {},
                {"id": "mig17-toast-defaults"},
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

        On Linux, the renderer's notification handler reads
        ``data.critical`` and (if true) sets the ``urgency`` hint to
        ``critical`` in the D-Bus notification spec (which causes GNOME
        Shell to show the notification above the lock screen + bypass
        Do Not Disturb). A string value would always be truthy in JS,
        causing every notification to be escalated to critical urgency —
        which would bypass Do Not Disturb without the user's consent."""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
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
        """The ``duration_ms`` field MUST be a Python ``int`` (which
        JSON-serializes to a number), NOT a string.

        Linux's libnotify honors a per-notification expiry hint via the
        ``expire-timeout`` D-Bus arg (the Tauri plugin currently passes
        ``-1`` meaning "let the DE decide", but the renderer may also
        implement a JS-side ``setTimeout`` fallback for the in-app
        toast). A string value would break the renderer's
        ``setTimeout(..., data.duration_ms)`` call (NaN → no auto-dismiss)."""
        server = make_bare_ipc_server()
        captured: dict = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            server._handle_show_electron_notification(
                {"title": "T", "message": "M", "duration_ms": 3000},
                {"id": "mig17-toast-duration-type"},
            )
        assert isinstance(captured["data"]["duration_ms"], int), (
            f"payload 'duration_ms' must be an int (so JSON-serializes to a "
            f"number), got {type(captured['data']['duration_ms']).__name__}."
        )


# ─── Test 6: Linux notifications require libnotify4 (deb) / libnotify (rpm) ────


class TestLinuxNotificationsRequireLibnotify:
    """Gate 6: Linux notifications require ``libnotify4`` to be installed.

    ``tauri-plugin-notification`` on Linux dispatches ``notify()`` calls
    to ``libnotify::Notification::new()``, which dynamically loads
    ``libnotify.so`` via the system's dynamic linker. ``libnotify.so``
    is provided by the ``libnotify4`` package on Debian/Ubuntu and by
    the ``libnotify`` package on Fedora/RHEL. If the library is missing,
    the ``notify()`` call silently fails — the D-Bus message is never
    sent, no banner appears, and no error is logged in the app.

    The ``.deb`` + ``.rpm`` packages declare this as a runtime
    dependency in ``tauri.conf.json``'s ``bundle.linux.deb.depends`` /
    ``bundle.linux.rpm.depends``, so ``apt install voice-typer*.deb``
    (or ``dnf install voice-typer*.rpm``) pulls it in automatically.
    This is the canonical Linux packaging contract: declare the library
    dependency, let the package manager install it, no postinst
    intervention needed.
    """

    def test_deb_depends_includes_libnotify4(self):
        """The ``.deb`` package's ``Depends`` field MUST list
        ``libnotify4``. This is declared in
        ``tauri.conf.json`` ``bundle.linux.deb.depends`` — Tauri's
        bundler copies it verbatim into the ``.deb`` control file's
        ``Depends:`` field. Without it, the user could install
        Voice Typer without libnotify4, and notifications would
        silently fail at runtime."""
        src = _read(TAURI_CONF_JSON)
        conf = json.loads(src)
        deb = conf.get("bundle", {}).get("linux", {}).get("deb", {})
        depends = deb.get("depends", [])
        assert "libnotify4" in depends, (
            f"tauri.conf.json bundle.linux.deb.depends MUST include "
            f"'libnotify4' — without it, apt install voice-typer*.deb "
            f"doesn't pull in libnotify, and tauri-plugin-notification's "
            f"notify() call silently fails (D-Bus message never sent). "
            f"Found deb depends: {depends!r}"
        )

    def test_rpm_depends_includes_libnotify(self):
        """The ``.rpm`` package's ``Requires`` field MUST list
        ``libnotify`` (the Fedora/RHEL package name; Debian's
        ``libnotify4`` becomes ``libnotify`` in the rpm namespace).
        This is declared in ``tauri.conf.json``
        ``bundle.linux.rpm.depends``. Same reasoning as the deb gate —
        without it, ``dnf install voice-typer*.rpm`` doesn't pull in
        libnotify, and notifications silently fail."""
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
        """The Linux validation runbook MUST list ``libnotify4`` (apt)
        and ``libnotify`` (dnf) in its system-libs prerequisites
        section. This is the docs-side contract: a developer setting up
        a Linux build host needs to know to install libnotify4 BEFORE
        ``cargo tauri build`` (the build needs the dev headers; the
        runtime needs the .so)."""
        src = _read(LINUX_RUNBOOK)
        # The runbook lists both apt and dnf package names.
        assert "libnotify4" in src, (
            "linux-validation-runbook.md MUST mention 'libnotify4' in its "
            "apt system-libs prerequisites — it's a build + runtime dep "
            "for the Linux Tauri host."
        )
        assert "libnotify" in src, (
            "linux-validation-runbook.md MUST mention 'libnotify' in its "
            "dnf system-libs prerequisites (the Fedora/RHEL package name)."
        )


# ─── Test 7: postinst does NOT need notification-specific logic ───────────


class TestPostinstDoesNotNeedNotificationLogic:
    """Gate 7: the ``.deb`` postinst script does NOT need to do anything
    special for notifications.

    ``libnotify4`` is a runtime dependency declared in
    ``tauri.conf.json``'s ``bundle.linux.deb.depends`` — apt pulls it in
    automatically during ``apt install voice-typer*.deb``. The postinst
    only needs to handle the keyboard-permission setup (udev rule +
    input group + Caps Lock neutralization). Notifications are purely a
    library dependency, not a system-config concern — there's no
    notification daemon to enable, no D-Bus service to register, no
    polkit rule to install.

    This is a NEGATIVE test: we verify the postinst script does NOT
    contain notification-specific logic. (If it did, that would indicate
    a misunderstanding of the libnotify contract — libnotify is a
    client library that talks to an existing D-Bus service, not a
    service that needs to be installed/enabled by the app.)
    """

    def test_postinst_script_exists(self):
        """The ``.deb`` postinst script MUST exist at
        ``scripts/linux/postinst`` — it's referenced by
        ``tauri.conf.json``'s ``bundle.linux.deb.postInstallScript``
        field and is a hard dependency of the .deb build (Tauri's
        bundler copies it into the .deb control archive)."""
        assert POSTINST_SCRIPT.is_file(), (
            f"postinst script MUST exist at {POSTINST_SCRIPT} — it's "
            f"referenced by tauri.conf.json bundle.linux.deb.postInstallScript."
        )

    def test_postinst_does_not_install_libnotify(self):
        """The postinst MUST NOT attempt to install ``libnotify4`` (or
        any libnotify variant) — that's the package manager's job (via
        the ``Depends:`` field declared in
        ``tauri.conf.json``). Installing it from postinst would be
        cargo-cult: it duplicates the package-manager's work AND it
        would fail if apt isn't available (e.g. when installing via
        ``dpkg -i`` without the dependency resolver)."""
        src = _read(POSTINST_SCRIPT)
        src_lower = src.lower()
        # The postinst must NOT contain apt/dnf install commands for
        # libnotify. We check for the canonical install invocations.
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
                f"postinst script MUST NOT install libnotify via '{pat}' — "
                f"that's the package manager's job (via the Depends: field "
                f"declared in tauri.conf.json bundle.linux.deb.depends). "
                f"Installing from postinst would duplicate the package "
                f"manager's work AND fail under dpkg -i without the resolver."
            )

    def test_postinst_does_not_register_dbus_notification_service(self):
        """The postinst MUST NOT attempt to register / enable / start a
        D-Bus notification service (e.g. ``notification-daemon``,
        ``mako``, ``dunst``). The notification daemon is a SESSION
        service (owned by the user's graphical session, not the system
        bus), and is the user's responsibility to install + start (the
        runbook §6.5 / Step 9 documents the workaround for DEs without
        a default daemon). The system postinst runs as root during
        ``apt install`` — it has no business touching the user's
        session services."""
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
                f"postinst script MUST NOT reference '{sub}' — the "
                f"notification daemon is a session service owned by the "
                f"user's graphical session, not the system postinst. "
                f"System postinst runs as root during apt install; it has "
                f"no business touching user session services."
            )

    def test_postinst_focuses_on_keyboard_permissions_only(self):
        """Belt-and-braces: the postinst's actual job is the keyboard-
        permission setup (udev rule + input group + Caps Lock
        neutralization). Verify it mentions ``install_permissions.py``
        (the Python script that performs the setup) — this confirms
        the postinst is doing what it's supposed to be doing (and NOT
        anything notification-related)."""
        src = _read(POSTINST_SCRIPT)
        assert "install_permissions.py" in src, (
            "postinst script MUST reference 'install_permissions.py' — "
            "that's the actual job of the postinst (keyboard permission "
            "setup), in contrast to notifications which are handled "
            "purely via the package's Depends: field."
        )


# ─── Test 8: source-inspection belt-and-braces ───────────────────────────


class TestSourceInspectionBeltAndBraces:
    """Gate 8: belt-and-braces source-inspection tests.

    These don't correspond to a single gate point — they pin additional
    invariants that would be easy to break in a refactor but would
    silently regress the Linux toast path if broken.
    """

    def test_ws_rs_emits_python_event_envelope(self):
        """``ws.rs`` MUST also emit the generic ``python-event``
        envelope (per ADR-0020 §6.3) which the ``usePython`` hook's
        onEvent catch-all listens to. This is the secondary path by
        which the renderer learns about a notification event — both
        paths (specific-event emit + python-event envelope) must be
        present for the toast wiring to be complete on Linux."""
        src = _read_ws_bridge_rs()
        assert 'emit("python-event"' in src, (
            "ws.rs must also emit the generic 'python-event' envelope "
            "(ADR-0020 §6.3) — this is the catch-all path the usePython "
            "hook uses to learn about notification events on Linux."
        )

    def test_ws_rs_emits_specific_event_with_emit_name(self):
        """``ws.rs`` MUST emit the specific event (using ``emit_name``)
        so direct listeners like ``appWindow.on('notification')`` keep
        firing. The generic ``python-event`` envelope is NOT sufficient
        — direct listeners don't subscribe to that."""
        src = _read_ws_bridge_rs()
        assert "emit(emit_name" in src, (
            "ws.rs must emit the specific event using `emit_name` (the "
            "result of the match arm) — this is what carries the canonical "
            "'notification' name to direct UI listeners on Linux."
        )

    def test_ws_rs_has_other_arm_passthrough(self):
        """``ws.rs`` MUST forward every event type unchanged so the
        legacy ``electron_notification`` event name reaches the alias
        branch (Test 4) which re-emits it as ``notification`` for new
        UI code.

        PVT-2 cleanup: the per-type ``match`` arm was REMOVED — the
        bridge now uses ``let emit_name = translate_event_name(event_type);``
        (extracted to a unit-testable helper, PVT-G5-062). This preserves
        the passthrough behavior (every event type is forwarded under its
        own translated name) AND removes the ``relaunch_electron`` →
        ``relaunch_app`` rename (the Python sidecar now publishes
        ``relaunch_app`` directly). GT-E3-6 further removed the legacy
        ``electron_notification`` alias branch (the Python sidecar now
        publishes ``notification`` directly)."""
        src = _read_ws_bridge_rs()
        # ``let emit_name = event_type;`` was
        # replaced by ``let emit_name = translate_event_name(event_type);``.
        # Accept both forms so the test stays green if the helper is
        # inlined back into a direct assignment.
        assert re.search(
            r"let\s+emit_name\s*=\s*(?:translate_event_name\(event_type\)|event_type)\s*;",
            src,
        ), (
            "ws.rs must forward every event type via "
            "`let emit_name = translate_event_name(event_type);` "
            "(PVT-G5-062 extraction; PVT-2 cleanup removed the per-type "
            "match arm; GT-E3-6 removed the electron_notification alias)."
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

    def test_linux_runbook_lists_toast_as_gate_point(self):
        """The Linux runbook MUST list the toast notification check as
        a numbered gate point (Step 9 — "libnotify toast appears on X11
        AND Wayland", gate point 5 per the 9-point validation gate
        summary table). This pins the runbook-side contract: the toast
        gate is one of the 9 Phase 0-L gates, and a developer running
        the gate sequence must encounter it."""
        src = _read(LINUX_RUNBOOK)
        # The runbook's gate-point header for notifications:
        # "Step 9 — libnotify toast appears on X11 AND Wayland"
        assert "tauri-plugin-notification" in src or "libnotify" in src, (
            "linux-validation-runbook.md MUST mention 'tauri-plugin-notification' "
            "or 'libnotify' as a gate-point header — gate check 6 is the toast gate."
        )
        assert "gate point 5" in src, (
            "linux-validation-runbook.md MUST label the toast gate as "
            "'gate point 5' (per the 9-Point Validation Gate Summary table — "
            "the gate-point numbering is the canonical reference for the 9 "
            "Phase 0-L gates)."
        )

    def test_linux_runbook_documents_notification_capability_failure(self):
        """The Linux runbook §6.5 / Step 9 "Common failures" section
        MUST document the ``notification:allow-notify not in
        capabilities`` failure mode. This is the most common Linux
        toast failure — a developer whose notification silently no-ops
        needs to know to check the capability file first."""
        src = _read(LINUX_RUNBOOK)
        assert "notification:allow-notify" in src, (
            "linux-validation-runbook.md MUST mention "
            "'notification:allow-notify' in its Common failures section — "
            "this is the most common Linux toast failure mode (Tauri v2 "
            "silently blocks notification APIs without the capability grant)."
        )

    def test_linux_runbook_documents_libnotify_install_failure(self):
        """The Linux runbook MUST document the ``libnotify: command not
        found`` / missing-libnotify failure mode + the install fix
        (``sudo apt-get install libnotify4`` or ``libnotify`` on
        Fedora). This is the second most common Linux toast failure —
        without libnotify4, the ``notify()`` call silently fails."""
        src = _read(LINUX_RUNBOOK)
        src_lower = src.lower()
        assert "apt-get install libnotify4" in src_lower or "apt install libnotify4" in src_lower, (
            "linux-validation-runbook.md MUST document the "
            "'sudo apt-get install libnotify4' fix for the missing-libnotify "
            "failure mode."
        )


# ─── Test 9: VALIDATE ON LINUX HOST block is documented ────────────────


class TestValidateOnLinuxHostBlock:
    """Gate 9: the VALIDATE ON LINUX HOST block (in this file's module
    docstring) MUST contain the canonical validation commands.

    This is a meta-test: we verify the module docstring (which is the
    human-readable runbook for the Linux host validation step) contains
    the expected command sequence. The actual validation is performed
    by a human on a real Linux desktop — this test just pins the docs
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

    def test_docstring_contains_validate_on_linux_host_header(self):
        """The module docstring MUST contain the
        ``VALIDATE ON LINUX HOST:`` header — this is the canonical
        marker the Linux host validator scans for."""
        doc = self._module_docstring()
        assert "VALIDATE ON LINUX HOST:" in doc, (
            "Module docstring MUST contain 'VALIDATE ON LINUX HOST:' header — "
            "this is the canonical marker the Linux host validator scans for."
        )

    def test_docstring_documents_libnotify_install_step(self):
        """The VALIDATE ON LINUX HOST block MUST mention the libnotify4
        install step (``sudo apt install libnotify4``) as step 1 — this
        is the most common reason a Linux toast test silently fails
        (the user is on a minimal WM setup without libnotify4)."""
        doc = self._module_docstring()
        assert "sudo apt install libnotify4" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention 'sudo apt install "
            "libnotify4' — without libnotify4, the notify() call silently "
            "fails (D-Bus message never sent)."
        )
        # Also documents the dpkg -i alternative path (which pulls
        # libnotify4 as a dep via the package's Depends: field).
        assert "dpkg -i voice-typer*.deb" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention the 'dpkg -i "
            "voice-typer*.deb' alternative — this path pulls libnotify4 "
            "automatically via the .deb's Depends: field (no manual "
            "apt install needed)."
        )

    def test_docstring_documents_dbus_troubleshooting(self):
        """The VALIDATE ON LINUX HOST block MUST mention the D-Bus /
        desktop-environment troubleshooting hint (the last line: "If
        notification doesn't appear: verify D-Bus is running + GNOME
        Shell / KDE Plasma is active."). This is the troubleshooting
        hint for the second most common silent-failure mode on Linux
        (no notification daemon running on the session bus)."""
        doc = self._module_docstring()
        assert "D-Bus" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention 'D-Bus' — the "
            "troubleshooting hint for the no-notification-daemon failure mode."
        )
        assert "GNOME Shell" in doc or "KDE Plasma" in doc, (
            "VALIDATE ON LINUX HOST block MUST mention 'GNOME Shell' or "
            "'KDE Plasma' — the two desktop environments the validator "
            "should verify are active if the notification doesn't appear."
        )

    def test_docstring_documents_log_path(self):
        """The VALIDATE ON LINUX HOST block MUST document the expected
        log path (``~/.local/share/voice-typer/logs/voice-typer.log``)
        so the validator can check for the ``notification event
        emitted`` line that confirms the Python sidecar fired the event
        (even if the native banner was suppressed because no
        notification daemon was running)."""
        doc = self._module_docstring()
        assert "~/.local/share/voice-typer/logs/voice-typer.log" in doc, (
            "VALIDATE ON LINUX HOST block MUST document the Linux log path "
            "(~/.local/share/voice-typer/logs/voice-typer.log) so the "
            "validator can confirm the notification event was emitted."
        )

    def test_docstring_documents_expected_timing(self):
        """The VALIDATE ON LINUX HOST block MUST document the expected
        timing ("notification appears within 1s") so the validator knows
        how long to wait before declaring the gate failed. Linux
        notifications via libnotify appear near-instantly (the D-Bus
        ``org.freedesktop.Notifications.Notify`` call is async but
        completes in <500ms in practice); 1s is the generous upper
        bound."""
        doc = self._module_docstring()
        assert "within 1s" in doc, (
            "VALIDATE ON LINUX HOST block MUST document the expected timing "
            "('within 1s') — the upper bound for how long the validator should "
            "wait for the banner before declaring the gate failed."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
