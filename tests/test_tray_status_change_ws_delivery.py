"""WS-path delivery contract for ``status_change`` push events.

DEFECT (found by the 2026-08-31 headless e2e checklist): the tray
``set_state`` hook in ``ipc/lifecycle.py`` forwarded every state change
via ``server.push`` — the TCP-only path. In the Tauri ws-mode sidecar no
TCP client ever exists, so ``status_change`` frames were never delivered
to the renderer (the status pill lagged up to the 15 s ``get_status``
health poll, while the Electron runtime received them live — the exact
cross-runtime divergence this delivery contract targets).

The fix publishes through ``event_bus`` (same rationale as the
documented ``ready``-event conversion in
``sidecar_ws_internals/connection.py::_emit_ready_if_first``): in TCP
mode the server's own ``_push_fn`` subscriber bridges the bus to the TCP
client; in WS mode the sidecar writer task's ``_push_to_ws`` subscriber
delivers it over the WebSocket.

These tests pin the FIXED contract in the exact ws-mode process shape.
"""

from __future__ import annotations

import pytest

_AUTH_TOKEN = "test-ws-auth-token-0123456789abcdef"
_AUTOSTART = "voice_typer.server.server_platform.autostart"


@pytest.fixture
def ws_mode_app(tmp_config_dir, monkeypatch):
    """A ``VoiceTyperApp`` shaped like the ``--ws`` sidecar's.

    Same mocking discipline as the ws mic-population suite: autostart
    platform helpers stubbed; everything else real; TAURI_SIDECAR=1.
    """
    monkeypatch.setattr(f"{_AUTOSTART}.is_autostart_enabled", lambda: False, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.enable_autostart", lambda: True, raising=False)
    monkeypatch.setattr(f"{_AUTOSTART}.disable_autostart", lambda: True, raising=False)
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _AUTH_TOKEN)
    from voice_typer.server.app import VoiceTyperApp

    return VoiceTyperApp()


class TestStatusChangeWsDelivery:
    """``tray.set_state`` must publish ``status_change`` on the event bus."""

    def test_set_state_publishes_status_change(self, ws_mode_app, monkeypatch):
        """Every ``tray.set_state`` call lands a ``status_change`` event
        on the event bus — the transport the WS writer task subscribes
        to. (Pre-fix this was a direct ``server.push`` — TCP-only, never
        delivered in ws-mode; this test fails against that code.)"""
        from voice_typer.server import event_bus

        captured: list[dict] = []
        monkeypatch.setattr(event_bus, "publish", lambda msg: captured.append(msg))

        server = _build_ws_mode_server(ws_mode_app)
        try:
            from voice_typer.server.tray_state import AppState

            ws_mode_app.tray.set_state(AppState.ERROR, "No speech model selected")
            status_events = [e for e in captured if e.get("type") == "status_change"]
            assert status_events, (
                "tray.set_state must publish status_change on event_bus — "
                "a direct server.push dead-ends in the TCP-only path and the "
                "Tauri renderer never receives live status updates"
            )
            payload = status_events[-1]["data"]
            assert payload["status"] == "error"
            assert payload["message"] == "No speech model selected"
        finally:
            server.stop()

    def test_wrapped_hook_survives_restart_idempotently(self, ws_mode_app, monkeypatch):
        """The hook wrapper is installed once (the ``_vt_wrapped`` guard)
        and repeated ``server.start()`` cycles do not duplicate the
        publish path — N set_state calls still produce exactly N
        status_change events."""
        from voice_typer.server import event_bus

        captured: list[dict] = []
        monkeypatch.setattr(event_bus, "publish", lambda msg: captured.append(msg))

        server = _build_ws_mode_server(ws_mode_app)
        try:
            from voice_typer.server.tray_state import AppState

            for expected_message in ("first", "second", "third"):
                ws_mode_app.tray.set_state(AppState.LOADING, expected_message)
            status_events = [e for e in captured if e.get("type") == "status_change"]
            assert len(status_events) == 3, (
                f"exactly one status_change per set_state call — no duplicate delivery (got {len(status_events)})"
            )
            assert [e["data"]["message"] for e in status_events] == ["first", "second", "third"]
        finally:
            server.stop()


def _build_ws_mode_server(app):
    """Build + start the IPC server exactly like entrypoint's ws branch."""
    from voice_typer.server.providers import build_ipc_server

    server = build_ipc_server(app)
    server._tcp_mode = True
    server.start()
    return server
