"""Unit tests for ``MicrophoneTestHandlersMixin`` (CR-12).

Covers the 4 microphone-test IPC handlers defined in
``voice_typer/server/handlers/microphone_test_handlers.py``:

- ``_handle_microphone_test_start`` — start a recording test
  (``mic_id``, ``duration``, optional ``filters``).
- ``_handle_microphone_test_stop`` — stop an in-progress test early.
- ``_handle_microphone_test_cancel`` — cancel an in-progress test.
- ``_handle_microphone_test_get_level`` — poll the real-time audio level.

All four handlers are thin pass-throughs to the service layer with
the standard try/except error envelope.  The interesting invariant
is in ``_handle_microphone_test_start``: it gracefully coerces
non-dict ``data`` to ``{}`` and applies defaults (``duration=10.0``)
so a missing field doesn't crash.

UE-15 (2026-07-30): ``_handle_microphone_test_status`` was deleted —
the renderer polls ``microphone_test_get_level`` at 60 Hz during a
test; the separate status query was unused. The corresponding
``TestMicrophoneTestStatus`` class was removed in lockstep.
"""

from __future__ import annotations


class TestMicrophoneTestStart:
    """``_handle_microphone_test_start`` — start a recording test."""

    def test_happy_path_with_all_params(self, ipc_server, fake_service):
        fake_service.microphone_test_start.return_value = {
            "ok": True,
            "duration": 5.0,
            "sample_rate": 16000,
        }
        # ``filters`` is the ADR 0007 filter-config DICT the renderer's
        # ``buildTestFilters`` sends — not a list. Downstream consumers
        # (``level_monitor.test_recording``) require a mapping
        # (``filters.get(...)`` / ``SimpleNamespace(**filters)``).
        filters = {
            "noise_filter_enabled": True,
            "noise_suppression_method": "rnnoise",
        }
        resp = ipc_server._handle_microphone_test_start(
            {"mic_id": "usb_mic_1", "duration": 5.0, "filters": filters},
            {},
        )
        assert resp["type"] == "microphone_test_result"
        assert resp["data"] == {
            "ok": True,
            "duration": 5.0,
            "sample_rate": 16000,
        }
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id="usb_mic_1",
            duration=5.0,
            filters=filters,
        )

    def test_non_dict_data_uses_defaults(self, ipc_server, fake_service):
        """Non-dict ``data`` → defaults (``mic_id=None``, ``duration=10.0``).

        The handler's ``d = data if isinstance(data, dict) else {}``
        guard means a list/string/None payload doesn't crash.
        """
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start(None, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=10.0,
            filters=None,
        )

    def test_string_duration_is_coerced_to_float(self, ipc_server, fake_service):
        """``duration: "5"`` (string from a form input) → coerced to 5.0.

        The handler's ``float(d.get("duration") or 10.0)`` accepts
        numeric strings; an empty string falls back to the default.
        """
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "7.5"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.microphone_test_start.side_effect = RuntimeError("mic busy")
        resp = ipc_server._handle_microphone_test_start({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_consent_missing_returns_consent_required_envelope(self, ipc_server, fake_service, fake_app):
        """XZ-PRIV-03: ``voice_biometric_consent=False`` → ``client.consent_required``.

        The mic test records up to 60s of audio and returns base64 WAV
        over IPC. Without consent gating, a renderer bug or compromised
        renderer could exfiltrate biometric voice data. The handler
        raises ``ConsentRequiredError`` BEFORE touching the service
        layer; ``_respond_with_error`` maps it to the structured
        ``client.consent_required`` envelope so the renderer can
        surface a consent dialog instead of a generic error toast.
        """
        fake_app.config.voice_biometric_consent = False
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 5.0}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.consent_required"
        # The structured fields let the renderer deep-link to the
        # exact toggle in Settings ().
        assert resp["data"]["consent_field"] == "voice_biometric_consent"
        assert resp["data"]["engine_name"] == "microphone_test"
        # Service must NOT have been called — the gate fires BEFORE
        # the validation/dispatch block.
        fake_service.microphone_test_start.assert_not_called()

    def test_consent_missing_logs_warning_not_error(self, ipc_server, fake_service, fake_app, caplog):
        """Consent-denied mic tests are normal control-flow, not errors.

        Regression (from a real session log): the consent rejection was
        logged at ERROR with an exception-class header line, so every
        consent-denied microphone test looked like a crash in
        ``voice-typer.log``::

            ERROR [IPC] *** failed: voice biometric consent required to
            start microphone test
            voice_typer.server.asr_errors.***: voice biometric ...

        The rejection is now logged at WARNING with no traceback — the
        renderer surfaces the consent dialog from the structured
        ``client.consent_required`` envelope. Unexpected exceptions keep
        the ERROR + exc_info contract (pinned in
        ``test_error_envelope_code_field.py``).
        """
        import logging

        fake_app.config.voice_biometric_consent = False
        fake_service.microphone_test_start.return_value = {"ok": True}
        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_microphone_test_start({"duration": 5.0}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.consent_required"
        records = [r for r in caplog.records if "voice biometric consent" in r.getMessage()]
        assert records, "the consent rejection must produce a log record"
        assert all(r.levelno == logging.WARNING for r in records), (
            f"consent rejection must log at WARNING, not ERROR; levels={[r.levelno for r in records]}"
        )
        assert all(r.exc_info is None for r in records), "consent rejection must not carry a traceback (exc_info)"
        assert all("rejected:" in r.getMessage() for r in records), (
            f"consent log line should use the 'rejected' phrasing; got: {[r.getMessage() for r in records]}"
        )

    def test_consent_present_proceeds_to_service(self, ipc_server, fake_service, fake_app):
        """XZ-PRIV-03: ``voice_biometric_consent=True`` → service is called.

        Positive-path regression: the consent gate must NOT block
        legitimate use when the user has explicitly opted in.
        """
        fake_app.config.voice_biometric_consent = True
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 5.0}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once()


class TestMicrophoneTestStop:
    """``_handle_microphone_test_stop`` — stop an in-progress test."""

    def test_happy_path_returns_microphone_test_result(self, ipc_server, fake_service):
        fake_service.microphone_test_stop.return_value = {
            "ok": True,
            "reason": "user_stopped",
        }
        resp = ipc_server._handle_microphone_test_stop({}, {})
        assert resp["type"] == "microphone_test_result"
        assert resp["data"] == {"ok": True, "reason": "user_stopped"}
        fake_service.microphone_test_stop.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.microphone_test_stop.side_effect = RuntimeError("no test running")
        resp = ipc_server._handle_microphone_test_stop({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestMicrophoneTestCancel:
    """``_handle_microphone_test_cancel`` — cancel an in-progress test."""

    def test_happy_path_returns_microphone_test_result(self, ipc_server, fake_service):
        fake_service.microphone_test_cancel.return_value = {"ok": True, "reason": "cancelled"}
        resp = ipc_server._handle_microphone_test_cancel({}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_cancel.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.microphone_test_cancel.side_effect = RuntimeError("already finished")
        resp = ipc_server._handle_microphone_test_cancel({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestMicrophoneTestGetLevel:
    """``_handle_microphone_test_get_level`` — poll the real-time audio level."""

    def test_happy_path_returns_microphone_test_level(self, ipc_server, fake_service):
        fake_service.microphone_test_get_level.return_value = {
            "rms": 0.05,
            "peak": 0.42,
            "clipping": False,
        }
        resp = ipc_server._handle_microphone_test_get_level({}, {})
        assert resp["type"] == "microphone_test_level"
        assert resp["data"] == {"rms": 0.05, "peak": 0.42, "clipping": False}

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.microphone_test_get_level.side_effect = RuntimeError("no test")
        resp = ipc_server._handle_microphone_test_get_level({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
