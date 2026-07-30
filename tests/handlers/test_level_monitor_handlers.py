"""Unit tests for ``LevelMonitorHandlersMixin`` (CR-12).

Covers the 2 level-monitor IPC handlers defined in
``voice_typer/server/handlers/level_monitor_handlers.py``:

- ``_handle_level_monitor_start`` — start the background level monitor
  (optional ``mic_id``).
- ``_handle_level_monitor_stop`` — stop the background level monitor.

Both handlers are thin pass-throughs that return
``{type: level_monitor_status, data: <result>}`` on success.  The
interesting invariant is in ``_handle_level_monitor_start``: a
non-dict ``data`` payload is gracefully coerced to ``{}`` so
``mic_id`` defaults to ``None``.

UE-15 (2026-07-30): ``_handle_level_monitor_status`` was deleted —
the renderer subscribes to the ``level_monitor_level`` push event
instead of polling a status endpoint. The corresponding
``TestLevelMonitorStatus`` class was removed in lockstep.
"""

from __future__ import annotations


class TestLevelMonitorStart:
    """``_handle_level_monitor_start`` — start the background level monitor."""

    def test_happy_path_with_mic_id(self, ipc_server, fake_service):
        fake_service.level_monitor_start.return_value = {
            "running": True,
            "mic_id": "usb_mic_1",
        }
        resp = ipc_server._handle_level_monitor_start({"mic_id": "usb_mic_1"}, {})
        assert resp["type"] == "level_monitor_status"
        assert resp["data"] == {"running": True, "mic_id": "usb_mic_1"}
        fake_service.level_monitor_start.assert_called_once_with(mic_id="usb_mic_1")

    def test_non_dict_data_defaults_mic_id_to_none(self, ipc_server, fake_service):
        """Non-dict ``data`` → ``mic_id=None`` (the default device).

        The handler's ``mic_id = (data or {}).get("mic_id", None) if isinstance(data, dict) else None``
        guard means a list/string payload doesn't crash.
        """
        fake_service.level_monitor_start.return_value = {"running": True}
        resp = ipc_server._handle_level_monitor_start(None, {})
        assert resp["type"] == "level_monitor_status"
        fake_service.level_monitor_start.assert_called_once_with(mic_id=None)

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.level_monitor_start.side_effect = RuntimeError("already running")
        resp = ipc_server._handle_level_monitor_start({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_consent_missing_returns_consent_required_envelope(self, ipc_server, fake_service, fake_app):
        """XZ-PRIV-03: ``voice_biometric_consent=False`` → ``client.consent_required``.

        The level monitor opens an InputStream that captures audio at
        the device native rate (16k–48k samples/sec). Even though only
        dBFS values are returned over IPC (not raw audio), the capture
        itself is biometric-data processing under GDPR Art. 9. The
        handler raises ``ConsentRequiredError`` BEFORE touching the
        service layer; ``_respond_with_error`` maps it to the
        structured ``client.consent_required`` envelope.
        """
        fake_app.config.voice_biometric_consent = False
        fake_service.level_monitor_start.return_value = {"running": True}
        resp = ipc_server._handle_level_monitor_start({"mic_id": "usb"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.consent_required"
        assert resp["data"]["consent_field"] == "voice_biometric_consent"
        assert resp["data"]["engine_name"] == "level_monitor"
        # Service must NOT have been called — the gate fires BEFORE
        # the validation/dispatch block.
        fake_service.level_monitor_start.assert_not_called()

    def test_consent_present_proceeds_to_service(self, ipc_server, fake_service, fake_app):
        """XZ-PRIV-03: ``voice_biometric_consent=True`` → service is called.

        Positive-path regression: the consent gate must NOT block
        legitimate use when the user has explicitly opted in.
        """
        fake_app.config.voice_biometric_consent = True
        fake_service.level_monitor_start.return_value = {"running": True}
        resp = ipc_server._handle_level_monitor_start({"mic_id": "usb"}, {})
        assert resp["type"] == "level_monitor_status"
        fake_service.level_monitor_start.assert_called_once_with(mic_id="usb")


class TestLevelMonitorStop:
    """``_handle_level_monitor_stop`` — stop the background level monitor."""

    def test_happy_path_returns_level_monitor_status(self, ipc_server, fake_service):
        fake_service.level_monitor_stop.return_value = {"running": False}
        resp = ipc_server._handle_level_monitor_stop({}, {})
        assert resp["type"] == "level_monitor_status"
        assert resp["data"] == {"running": False}
        fake_service.level_monitor_stop.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.level_monitor_stop.side_effect = RuntimeError("not running")
        resp = ipc_server._handle_level_monitor_stop({}, {})
        assert resp["type"] == "error"
        # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
