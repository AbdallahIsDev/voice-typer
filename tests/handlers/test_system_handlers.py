"""Unit tests for ``SystemHandlersMixin`` (CR-12).

Covers the 6 system-level IPC handlers defined in
``voice_typer/server/handlers/system_handlers.py``:

- ``_handle_restart_app`` — sends ack, then calls ``service.restart()``.
- ``_handle_quit_app`` — sends ack, then calls ``service.quit()``.
- ``_handle_check_accessibility`` — returns ``accessibility_status``
  with ``granted`` (True on non-macOS) and ``platform`` fields.
- ``_handle_set_tray_locale`` — validates a ``locale`` field, returns
  ack echoing the locale.
- ``_handle_set_esc_cancel_paused`` — sets the keyboard ownership
  state, returns ack with ``paused`` flag.
- ``_handle_show_electron_notification`` — validates ``title``,
  ``message``, ``duration_ms``, ``critical`` fields and publishes an
  ``electron_notification`` event.

Each test calls the handler directly with a fresh ``resp={}`` dict
and asserts on the returned dict (or, for handlers that return
``None`` because they call ``self._send(resp)`` internally, asserts
on the call arguments captured by the mocked ``_send``).

UE-15 (2026-07-30): ``_handle_export_diagnostics`` was deleted — the
Tauri host now handles it via a dedicated Rust command. The
corresponding ``TestExportDiagnostics`` class was removed in
lockstep; the catch-all envelope-shape regression it covered is
still exercised by ``TestHandlerCatchAllEnvelopeShape`` in
``tests/handlers/test_r13_f3_error_envelope_code_field.py`` via
``_handle_cancel_model_download``.
"""

from __future__ import annotations


class TestRestartApp:
    """``_handle_restart_app`` — sends ack then calls ``service.restart()``."""

    def test_happy_path_sends_ack_and_calls_service_restart(self, ipc_server, fake_service):
        """Valid input → ack is sent and ``service.restart()`` is invoked.

        ``_handle_restart_app`` returns ``None`` (it sends the ack via
        ``self._send`` and then restarts the process).  We assert on
        the captured ``_send`` payload rather than the return value.
        """
        captured: list[dict] = []
        ipc_server._send = lambda msg: captured.append(msg)

        result = ipc_server._handle_restart_app({}, {})

        assert result is None, "restart_app should return None (sends ack internally)"
        assert len(captured) == 1, "exactly one _send call expected (the ack)"
        assert captured[0]["type"] == "ack"
        assert captured[0]["data"] == {}, "ack data must be an empty object"
        fake_service.restart.assert_called_once_with()

    def test_service_restart_failure_does_not_raise(self, ipc_server, fake_service):
        """If ``service.restart()`` raises, the handler logs and returns None.

        The ack has already been sent before ``service.restart()`` is
        called, so a restart failure can't be reported back to the
        client — but the handler must not propagate the exception
        (which would crash the IPC dispatch thread).
        """
        fake_service.restart.side_effect = RuntimeError("restart exploded")
        ipc_server._send = lambda msg: None  # swallow the ack send

        # Must not raise — the surrounding try/except must swallow.
        result = ipc_server._handle_restart_app({}, {})
        assert result is None

    def test_service_restart_failure_pushes_error_event(self, ipc_server, fake_service):
        """If ``service.restart()`` raises, a follow-up ``error`` event is pushed.

        The ack has already been sent, so the failure can't be reported
        via the response envelope. Instead, the handler publishes an
        ``error`` event with ``kind="restart_failed"`` so the renderer
        can surface a toast. Without this push, the client would assume
        the restart succeeded.
        """
        fake_service.restart.side_effect = RuntimeError("restart exploded")
        ipc_server._send = lambda msg: None
        captured: list[dict] = []
        from voice_typer.server import event_bus

        event_bus.subscribe(captured.append)
        try:
            ipc_server._handle_restart_app({}, {})
        finally:
            event_bus.unsubscribe(captured.append)

        assert len(captured) == 1, "exactly one error event expected on restart failure"
        evt = captured[0]
        assert evt["type"] == "error"
        assert evt["data"]["kind"] == "restart_failed"
        assert "restart exploded" in evt["data"]["message"]

    def test_service_restart_failure_publish_exception_is_swallowed(self, ipc_server, fake_service, monkeypatch):
        """If ``event_bus.publish`` itself raises, the handler must not crash.

        A broken event-bus subscriber must not take down the IPC dispatch
        thread. The handler logs the publish failure at debug and returns.
        """
        fake_service.restart.side_effect = RuntimeError("restart exploded")
        ipc_server._send = lambda msg: None

        def _boom(_evt):
            raise RuntimeError("event bus broken")

        monkeypatch.setattr("voice_typer.server.event_bus.publish", _boom)
        # Must not raise — the publish try/except must swallow.
        result = ipc_server._handle_restart_app({}, {})
        assert result is None


class TestQuitApp:
    """``_handle_quit_app`` — same shape as ``restart_app``."""

    def test_happy_path_sends_ack_and_calls_service_quit(self, ipc_server, fake_service):
        captured: list[dict] = []
        ipc_server._send = lambda msg: captured.append(msg)

        result = ipc_server._handle_quit_app({}, {})

        assert result is None
        assert captured[0]["type"] == "ack"
        assert captured[0]["data"] == {}
        fake_service.quit.assert_called_once_with()

    def test_service_quit_failure_does_not_raise(self, ipc_server, fake_service):
        fake_service.quit.side_effect = RuntimeError("quit failed")
        ipc_server._send = lambda msg: None
        result = ipc_server._handle_quit_app({}, {})
        assert result is None

    def test_service_quit_failure_pushes_error_event(self, ipc_server, fake_service):
        """If ``service.quit()`` raises, a follow-up ``error`` event is pushed.

        Mirrors :meth:`TestRestartApp.test_service_restart_failure_pushes_error_event`:
        the ack is already sent, so the failure is surfaced via a push
        event with ``kind="quit_failed"`` instead of the response envelope.
        """
        fake_service.quit.side_effect = RuntimeError("quit failed")
        ipc_server._send = lambda msg: None
        captured: list[dict] = []
        from voice_typer.server import event_bus

        event_bus.subscribe(captured.append)
        try:
            ipc_server._handle_quit_app({}, {})
        finally:
            event_bus.unsubscribe(captured.append)

        assert len(captured) == 1, "exactly one error event expected on quit failure"
        evt = captured[0]
        assert evt["type"] == "error"
        assert evt["data"]["kind"] == "quit_failed"
        assert "quit failed" in evt["data"]["message"]


class TestCheckAccessibility:
    """``_handle_check_accessibility`` — returns ``accessibility_status``."""

    def test_happy_path_non_macos_returns_granted_true(self, ipc_server):
        """On non-macOS (the Linux test env), ``granted`` must be True.

        The handler short-circuits the macOS-only AXIsProcessTrusted()
        path on other platforms, so we don't need to mock any system
        libraries — the result is deterministic.
        """
        import sys as _sys

        resp = ipc_server._handle_check_accessibility({}, {})
        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is True
        assert resp["data"]["platform"] == _sys.platform

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server):
        """XZ-R3-12: non-dict ``data`` → ``code: client.invalid_payload``."""
        resp = ipc_server._handle_check_accessibility("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"

    def test_none_payload_returns_invalid_payload_error(self, ipc_server):
        """XZ-R3-12: ``data=None`` is rejected by the validator."""
        resp = ipc_server._handle_check_accessibility(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"


class TestSetTrayLocale:
    """``_handle_set_tray_locale`` — validates ``locale`` and returns ack."""

    def test_happy_path_with_explicit_locale(self, ipc_server, monkeypatch):
        """Valid ``{"locale": "ar"}`` → ack echoing the locale."""
        # Patch the tray-locale helpers so we don't touch global state.
        monkeypatch.setattr("voice_typer.server.tray.set_tray_locale", lambda loc: None)
        monkeypatch.setattr("voice_typer.server.tray.get_tray_locale", lambda: "ar")

        resp = ipc_server._handle_set_tray_locale({"locale": "ar"}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"locale": "ar"}

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server):
        """Non-dict ``data`` → ``code: invalid_payload`` error.

        The shared ``_validate_dict_payload`` helper rejects
        non-dict payloads with this structured code so the client
        can distinguish a missing-data bug from a bad-field bug.
        """
        resp = ipc_server._handle_set_tray_locale("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"

    def test_wrong_type_locale_returns_invalid_field_error(self, ipc_server):
        """``locale`` not a string → ``code: invalid_field`` error."""
        resp = ipc_server._handle_set_tray_locale({"locale": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "locale"

    def test_missing_locale_uses_default(self, ipc_server, monkeypatch):
        """Empty dict → default locale "en" is used (required=False)."""
        captured: list[str] = []
        monkeypatch.setattr("voice_typer.server.tray.set_tray_locale", lambda loc: captured.append(loc))
        monkeypatch.setattr("voice_typer.server.tray.get_tray_locale", lambda: "en")

        resp = ipc_server._handle_set_tray_locale({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"locale": "en"}
        assert captured == ["en"], "set_tray_locale must be called with the default"

    def test_oversized_locale_returns_invalid_field_error(self, ipc_server):
        """XZ-R3-04: ``locale`` > 64 chars → ``code: invalid_field`` error."""
        resp = ipc_server._handle_set_tray_locale({"locale": "x" * 65}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "locale"

    def test_oversized_labels_value_returns_invalid_field_error(self, ipc_server, monkeypatch):
        """XZ-R3-04: label value > 1024 chars → ``code: invalid_field`` error."""
        monkeypatch.setattr("voice_typer.server.tray.set_tray_locale", lambda loc: None)
        monkeypatch.setattr("voice_typer.server.tray.get_tray_locale", lambda: "en")
        registered: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            "voice_typer.server.tray.register_tray_labels",
            lambda loc, labels: registered.append((loc, labels)),
        )
        resp = ipc_server._handle_set_tray_locale({"locale": "en", "labels": {"app_name": "x" * 1025}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "labels"
        assert registered == []

    def test_non_string_label_key_returns_invalid_field_error(self, ipc_server, monkeypatch):
        """XZ-R3-04: non-string label key → ``code: invalid_field`` error."""
        monkeypatch.setattr("voice_typer.server.tray.set_tray_locale", lambda loc: None)
        monkeypatch.setattr("voice_typer.server.tray.get_tray_locale", lambda: "en")
        monkeypatch.setattr("voice_typer.server.tray.register_tray_labels", lambda loc, labels: None)
        resp = ipc_server._handle_set_tray_locale(
            {"locale": "en", "labels": {123: "value"}},  # type: ignore[dict-item]
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "labels"

    def test_oversized_payload_returns_invalid_payload_error(self, ipc_server, monkeypatch):
        """XZ-R3-04: total payload > 64 KiB → ``code: invalid_payload`` error."""
        monkeypatch.setattr("voice_typer.server.tray.set_tray_locale", lambda loc: None)
        monkeypatch.setattr("voice_typer.server.tray.get_tray_locale", lambda: "en")
        monkeypatch.setattr("voice_typer.server.tray.register_tray_labels", lambda loc, labels: None)
        # Each label value is ≤1024 chars (passes per-field cap) but
        # the total payload exceeds 64 KiB.
        big_labels = {f"key_{i:03d}": "v" * 1000 for i in range(70)}
        resp = ipc_server._handle_set_tray_locale({"locale": "en", "labels": big_labels}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"

    def test_valid_labels_payload_returns_ack(self, ipc_server, monkeypatch):
        """XZ-R3-04 happy path: small valid labels dict → ack."""
        monkeypatch.setattr("voice_typer.server.tray.set_tray_locale", lambda loc: None)
        monkeypatch.setattr("voice_typer.server.tray.get_tray_locale", lambda: "ar")
        registered: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            "voice_typer.server.tray.register_tray_labels",
            lambda loc, labels: registered.append((loc, labels)),
        )
        resp = ipc_server._handle_set_tray_locale({"locale": "ar", "labels": {"app_name": "Voice Typer AR"}}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"locale": "ar"}
        assert registered == [("ar", {"app_name": "Voice Typer AR"})]


class TestSetEscCancelPaused:
    """``_handle_set_esc_cancel_paused`` — toggles keyboard ownership."""

    def test_happy_path_paused_true(self, ipc_server, fake_app, monkeypatch):
        """``{"paused": true}`` → ack with ``paused: True`` and app flag set."""
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        # Reset the singleton to a known state before the test.
        ko = keyboard_ownership()
        ko.set_owner("normal", reason="test setup")

        resp = ipc_server._handle_set_esc_cancel_paused({"paused": True}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"paused": True}
        # Backward-compat alias: the app attribute is updated too.
        assert fake_app._esc_cancel_paused is True
        # Canonical state: the keyboard ownership singleton flipped
        # to "hotkey_capture".
        assert ko.is_hotkey_capture_active() is True

    def test_happy_path_paused_false(self, ipc_server, fake_app):
        """``{"paused": false}`` → ack with ``paused: False``."""
        from voice_typer.server.keyboard_ownership import keyboard_ownership

        ko = keyboard_ownership()
        ko.set_owner("hotkey_capture", reason="test setup")

        resp = ipc_server._handle_set_esc_cancel_paused({"paused": False}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"paused": False}
        assert fake_app._esc_cancel_paused is False
        assert ko.is_hotkey_capture_active() is False

    def test_missing_paused_defaults_to_false(self, ipc_server, fake_app):
        """Empty dict → ``paused`` defaults to False (resume normal)."""
        resp = ipc_server._handle_set_esc_cancel_paused({}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"paused": False}
        assert fake_app._esc_cancel_paused is False


class TestShowElectronNotification:
    """``_handle_show_electron_notification`` — validates 4 fields and publishes."""

    def test_happy_path_publishes_event_and_returns_ack(self, ipc_server, monkeypatch):
        """Valid 4-field payload → event published + ``{type: ack}`` returned."""
        captured: list[dict] = []

        # Subscribe to the event bus.  event_bus.publish is a
        # synchronous broadcast, so the subscriber sees the event
        # before _handle_show_electron_notification returns.
        from voice_typer.server import event_bus

        def _subscriber(evt):
            captured.append(evt)

        event_bus.subscribe(_subscriber)
        try:
            resp = ipc_server._handle_show_electron_notification(
                {
                    "title": "Hello",
                    "message": "World",
                    "duration_ms": 5000,
                    "critical": True,
                },
                {},
            )
        finally:
            event_bus.unsubscribe(_subscriber)

        assert resp["type"] == "ack"
        assert len(captured) == 1
        evt = captured[0]
        # the event type was renamed from "electron_notification"
        # to the platform-agnostic "notification" so the Tauri Rust host
        # doesn't need to rename it on the way through.
        assert evt["type"] == "notification"
        assert evt["data"] == {
            "title": "Hello",
            "message": "World",
            "duration_ms": 5000,
            "critical": True,
        }

    def test_non_dict_data_returns_invalid_payload_error(self, ipc_server):
        """Non-dict ``data`` → ``code: client.invalid_payload`` error.

        The shared ``_validate_dict_payload`` helper rejects non-dict
        payloads with the namespaced ``client.invalid_payload`` code
        (DE-36) and a ``"data must be an object"`` message.
        """
        resp = ipc_server._handle_show_electron_notification("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"

    def test_invalid_title_type_returns_invalid_field(self, ipc_server):
        """``title`` not a string → ``code: invalid_field, field: title``."""
        resp = ipc_server._handle_show_electron_notification({"title": 123, "message": "x"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_invalid_critical_type_rejects_truthy_string(self, ipc_server):
        """``critical: "false"`` must be rejected (not coerced to True).

        SEC-VALIDATE-001: the old handler used ``bool("false")`` which
        is ``True`` (any non-empty string is truthy), silently
        escalating a misbehaving caller's notification to critical.
        The current handler rejects non-bool values explicitly.
        """
        resp = ipc_server._handle_show_electron_notification({"critical": "false"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "critical"

    def test_invalid_duration_ms_rejects_bool(self, ipc_server):
        """``duration_ms: True`` must be rejected (bool is subclass of int).

        SEC-VALIDATE-001: without the explicit ``isinstance(x, bool)``
        exclusion, ``True`` would sneak through as ``duration_ms: 1``
        if a caller swapped the ``critical`` and ``duration_ms`` fields.
        """
        resp = ipc_server._handle_show_electron_notification({"duration_ms": True}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "duration_ms"

    def test_duration_ms_clamped_to_24h_cap(self, ipc_server, monkeypatch):
        """``duration_ms`` > 24h is clamped to 24h (not rejected).

        Prevents a caller from scheduling a ``setTimeout`` that
        effectively never fires, which would leave a "persistent"
        notification that the user can't dismiss via auto-close.
        """
        captured: list[dict] = []
        from voice_typer.server import event_bus

        event_bus.subscribe(captured.append)
        try:
            huge_ms = 25 * 60 * 60 * 1000  # 25 hours
            resp = ipc_server._handle_show_electron_notification({"duration_ms": huge_ms}, {})
        finally:
            event_bus.unsubscribe(captured.append)

        assert resp["type"] == "ack"
        # 24h in ms.
        assert captured[0]["data"]["duration_ms"] == 24 * 60 * 60 * 1000
