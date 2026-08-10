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

    def test_macos_stale_grant_suggests_reset_with_runtime_command(self, ipc_server, monkeypatch):
        """Finding #919 part b: a CONFIRMED stale grant (AXIsProcessTrusted
        ran and returned False) must extend the response with
        ``suggest_reset: True`` + the runtime ``tccutil`` reset command
        built from the resolved host bundle ID."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.tccutil_reset_command_str",
            lambda service, bundle_id: f"tccutil reset {service} {bundle_id}",
            raising=False,
        )

        import ctypes
        from unittest.mock import MagicMock

        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 0
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        resp = ipc_server._handle_check_accessibility({}, {})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is False
        assert resp["data"]["platform"] == "macos"
        assert resp["data"]["suggest_reset"] is True
        assert resp["data"]["reset_command"] == "tccutil reset Accessibility com.voicetyper.desktop"

    def test_macos_stale_grant_unresolved_bundle_omits_command(self, ipc_server, monkeypatch):
        """Finding #919 part b: when the host bundle ID cannot be
        resolved, ``suggest_reset`` is False and NO ``reset_command`` key
        is attached (a wrong bundle ID in a tccutil command is worse than
        no command — mirrors the reset handler's convention)."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.tccutil_reset_command_str",
            lambda service, bundle_id: f"tccutil reset {service} {bundle_id}",
            raising=False,
        )

        import ctypes
        from unittest.mock import MagicMock

        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 0
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        resp = ipc_server._handle_check_accessibility({}, {})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is False
        assert resp["data"]["platform"] == "macos"
        assert resp["data"]["suggest_reset"] is False
        assert "reset_command" not in resp["data"]

    def test_macos_granted_keeps_original_shape(self, ipc_server, monkeypatch):
        """Finding #919 part b: a granted response must keep the original
        two-field shape — NO ``suggest_reset`` / ``reset_command`` keys."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
            raising=False,
        )

        import ctypes
        from unittest.mock import MagicMock

        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 1
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        resp = ipc_server._handle_check_accessibility({}, {})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is True
        assert resp["data"]["platform"] == "macos"
        assert "suggest_reset" not in resp["data"]
        assert "reset_command" not in resp["data"]

    def test_macos_check_failed_keeps_original_shape(self, ipc_server, monkeypatch):
        """Finding #919 part b: the ``check_failed`` fallback (ctypes load
        errored) must NOT suggest a reset — an un-runnable probe cannot
        substantiate a stale grant."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
            raising=False,
        )

        import ctypes
        from unittest.mock import MagicMock

        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.side_effect = OSError("no ApplicationServices")
        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        resp = ipc_server._handle_check_accessibility({}, {})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is False
        assert resp["data"]["platform"] == "macos"
        assert resp["data"]["reason"] == "check_failed"
        assert "suggest_reset" not in resp["data"]
        assert "reset_command" not in resp["data"]


class TestResetMacosAccessibility:
    """``_handle_reset_macos_accessibility`` (finding #127 part b).

    Runs ``tccutil reset Accessibility <bundle-id>`` (bundle ID resolved
    at runtime) and re-opens System Settings. Returns an ``ack`` with
    ``{ok, command, error}``.
    """

    def test_non_macos_returns_unsupported_platform(self, ipc_server, monkeypatch):
        """On non-macOS hosts the command is a no-op (no subprocess)."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: False,
        )
        calls: list = []
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.subprocess.run",
            lambda *a, **k: calls.append(a),
        )

        resp = ipc_server._handle_reset_macos_accessibility({}, {})

        assert resp["type"] == "ack"
        assert resp["data"] == {
            "ok": False,
            "command": None,
            "error": "unsupported_platform",
        }
        assert calls == [], "must NOT spawn tccutil on non-macOS"

    def test_macos_runs_tccutil_with_resolved_bundle_id_and_reopens_settings(self, ipc_server, monkeypatch):
        """macOS + resolved bundle ID → tccutil runs with the RUNTIME
        bundle ID and System Settings is re-opened."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
        )
        run_calls: list = []

        class _FakeCompleted:
            returncode = 0
            stderr = ""

        def _fake_run(args, **kwargs):
            run_calls.append(args)
            return _FakeCompleted()

        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.subprocess.run",
            _fake_run,
        )
        opened: list = []
        monkeypatch.setattr(
            "voice_typer.server.permissions._open_macos_accessibility_settings",
            lambda: opened.append(True),
        )

        resp = ipc_server._handle_reset_macos_accessibility({}, {})

        assert resp["type"] == "ack"
        assert resp["data"]["ok"] is True
        assert resp["data"]["command"] == "tccutil reset Accessibility com.voicetyper.desktop"
        assert resp["data"]["error"] is None
        assert run_calls == [["tccutil", "reset", "Accessibility", "com.voicetyper.desktop"]]
        assert opened == [True], "System Settings must be re-opened after the reset"

    def test_macos_embeds_any_runtime_bundle_id(self, ipc_server, monkeypatch):
        """The command must follow the resolved value, not a fixed one —
        the whole point of runtime resolution (e.g. a future Tauri
        build with a different identifier)."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: "com.voicetyper.some-other-build",
        )
        run_calls: list = []

        class _FakeCompleted:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.subprocess.run",
            lambda args, **k: (run_calls.append(args), _FakeCompleted())[1],
        )
        monkeypatch.setattr(
            "voice_typer.server.permissions._open_macos_accessibility_settings",
            lambda: None,
        )

        resp = ipc_server._handle_reset_macos_accessibility({}, {})

        assert resp["data"]["ok"] is True
        assert resp["data"]["command"] == "tccutil reset Accessibility com.voicetyper.some-other-build"
        assert run_calls == [["tccutil", "reset", "Accessibility", "com.voicetyper.some-other-build"]]

    def test_macos_unresolved_bundle_id_returns_no_command(self, ipc_server, monkeypatch):
        """Unresolvable bundle ID → ok=False with no command (a wrong
        bundle ID in a tccutil command is worse than no command)."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: None,
        )
        run_calls: list = []
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.subprocess.run",
            lambda *a, **k: run_calls.append(a),
        )
        monkeypatch.setattr(
            "voice_typer.server.permissions._open_macos_accessibility_settings",
            lambda: None,
        )

        resp = ipc_server._handle_reset_macos_accessibility({}, {})

        assert resp["data"] == {
            "ok": False,
            "command": None,
            "error": "bundle_id_unresolved",
        }
        assert run_calls == [], "must NOT spawn tccutil without a bundle ID"

    def test_macos_tccutil_failure_reports_error_but_still_reopens_settings(self, ipc_server, monkeypatch):
        """tccutil failure → ok=False with the stderr; System Settings is
        still re-opened so the user can re-grant manually."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_macos",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.server_platform.macos_bundle_id.resolve_host_bundle_id",
            lambda: "com.voicetyper.desktop",
        )

        class _FakeFailed:
            returncode = 1
            stderr = "tccutil: reset failed"

        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.subprocess.run",
            lambda *a, **k: _FakeFailed(),
        )
        opened: list = []
        monkeypatch.setattr(
            "voice_typer.server.permissions._open_macos_accessibility_settings",
            lambda: opened.append(True),
        )

        resp = ipc_server._handle_reset_macos_accessibility({}, {})

        assert resp["data"]["ok"] is False
        assert resp["data"]["error"] == "tccutil: reset failed"
        assert resp["data"]["command"] == "tccutil reset Accessibility com.voicetyper.desktop"
        assert opened == [True]

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server):
        """Non-dict ``data`` → ``code: client.invalid_payload`` (consistent
        with sibling handlers)."""
        resp = ipc_server._handle_reset_macos_accessibility("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"


class TestResetLinuxPermissions:
    """``_handle_reset_linux_permissions`` (finding #127 part b, Linux
    sibling of the macOS TCC reset).

    Clears a stale polkit authorization by restarting the polkit daemon
    via pkexec (``pkaction`` enumerates the Voice Typer actions,
    ``pkcheck`` verifies the post-reset state). Returns an ``ack`` with
    ``{ok, command, error, actions, checks}``.
    """

    def test_non_linux_returns_unsupported_platform(self, ipc_server, monkeypatch):
        """On non-Linux hosts the command is a no-op (no subprocess)."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_linux",
            lambda: False,
        )
        calls: list = []
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.subprocess.run",
            lambda *a, **k: calls.append(a),
        )

        resp = ipc_server._handle_reset_linux_permissions({}, {})

        assert resp["type"] == "ack"
        assert resp["data"] == {
            "ok": False,
            "command": None,
            "error": "unsupported_platform",
            "actions": [],
            "checks": {},
        }
        assert calls == [], "must NOT spawn pkexec/pkaction on non-Linux"

    def test_linux_happy_path_restarts_polkit_and_reports_post_reset_state(self, ipc_server, monkeypatch):
        """Linux → polkit daemon restarted via pkexec; the canonical
        ``com.voicetyper.install-permissions`` action registration is
        surfaced and pkcheck'd to ``not_authorized`` (the cleared state).

        The legacy (pre-Tauri Electron) action is NOT enumerated: the
        legacy policy file is removed at install/upgrade time (see
        install_permissions.py), so no current install registers it."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_linux",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers._enumerate_polkit_actions",
            lambda: ["com.voicetyper.install-permissions"],
        )
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers._reset_polkit_authorization",
            lambda: ("pkexec systemctl restart polkit", True, None),
        )
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers._polkit_check_authorization",
            lambda action: "not_authorized",
        )

        resp = ipc_server._handle_reset_linux_permissions({}, {})

        assert resp["type"] == "ack"
        assert resp["data"]["ok"] is True
        assert resp["data"]["command"] == "pkexec systemctl restart polkit"
        assert resp["data"]["error"] is None
        assert resp["data"]["actions"] == ["com.voicetyper.install-permissions"]
        assert resp["data"]["checks"] == {
            "com.voicetyper.install-permissions": "not_authorized",
        }

    def test_linux_reset_failure_reports_error_and_skips_checks(self, ipc_server, monkeypatch):
        """pkexec failure (e.g. the user dismisses the polkit dialog →
        exit 126) → ok=False with the error; pkcheck must NOT run."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers.is_linux",
            lambda: True,
        )
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers._enumerate_polkit_actions",
            lambda: ["com.voicetyper.install-permissions"],
        )
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers._reset_polkit_authorization",
            lambda: (None, False, "pkexec: authentication dismissed"),
        )
        checked: list = []
        monkeypatch.setattr(
            "voice_typer.server.handlers.system_handlers._polkit_check_authorization",
            lambda action: checked.append(action),
        )

        resp = ipc_server._handle_reset_linux_permissions({}, {})

        assert resp["data"]["ok"] is False
        assert resp["data"]["command"] is None
        assert resp["data"]["error"] == "pkexec: authentication dismissed"
        assert resp["data"]["actions"] == ["com.voicetyper.install-permissions"]
        assert resp["data"]["checks"] == {}, "pkcheck must NOT run after a failed reset"
        assert checked == []

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server):
        """Non-dict ``data`` → ``code: client.invalid_payload`` (consistent
        with sibling handlers)."""
        resp = ipc_server._handle_reset_linux_permissions("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"


class TestPolkitResetHelpers:
    """Module-level ``_enumerate_polkit_actions`` / ``_polkit_check_authorization``
    / ``_reset_polkit_authorization`` — the subprocess glue behind
    ``reset_linux_permissions``."""

    def test_enumerate_filters_voicetyper_actions_and_dedupes(self, monkeypatch):
        """Only action IDs mentioning ``voicetyper`` are surfaced (the
        canonical namespace); unrelated polkit actions are dropped;
        duplicates collapse."""
        import voice_typer.server.handlers.system_handlers as sh

        class _FakeCompleted:
            returncode = 0
            stdout = (
                "com.voicetyper.install-permissions\n"
                "org.freedesktop.policykit.exec\n"
                "com.voicetyper.install-permissions\n"
            )

        monkeypatch.setattr(sh.subprocess, "run", lambda *a, **k: _FakeCompleted())

        assert sh._enumerate_polkit_actions() == [
            "com.voicetyper.install-permissions",
        ]

    def test_enumerate_tolerates_missing_pkaction(self, monkeypatch):
        """No ``pkaction`` binary → empty list (the reset still runs)."""
        import voice_typer.server.handlers.system_handlers as sh

        def _boom(*a, **k):
            raise FileNotFoundError("pkaction")

        monkeypatch.setattr(sh.subprocess, "run", _boom)

        assert sh._enumerate_polkit_actions() == []

    def test_enumerate_tolerates_nonzero_exit(self, monkeypatch):
        """``pkaction`` exiting non-zero → empty list (tolerant)."""
        import voice_typer.server.handlers.system_handlers as sh

        class _FakeFailed:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(sh.subprocess, "run", lambda *a, **k: _FakeFailed())

        assert sh._enumerate_polkit_actions() == []

    def test_check_authorization_maps_pkcheck_exit_codes(self, monkeypatch):
        """pkcheck exit 0 → authorized, 1 → not_authorized, anything
        else → check_error."""
        import voice_typer.server.handlers.system_handlers as sh

        class _FakeCompleted:
            def __init__(self, rc: int) -> None:
                self.returncode = rc

        codes = iter([0, 1, 3])

        monkeypatch.setattr(
            sh.subprocess,
            "run",
            lambda *a, **k: _FakeCompleted(next(codes)),
        )

        assert sh._polkit_check_authorization("com.voicetyper.install-permissions") == "authorized"
        assert sh._polkit_check_authorization("com.voicetyper.install-permissions") == "not_authorized"
        assert sh._polkit_check_authorization("com.voicetyper.install-permissions") == "check_error"

    def test_check_authorization_tolerates_missing_pkcheck(self, monkeypatch):
        """No ``pkcheck`` binary / timeout → check_error, never raises."""
        import subprocess

        import voice_typer.server.handlers.system_handlers as sh

        def _boom(*a, **k):
            raise subprocess.TimeoutExpired("pkcheck", 10)

        monkeypatch.setattr(sh.subprocess, "run", _boom)

        assert sh._polkit_check_authorization("com.voicetyper.install-permissions") == "check_error"

    def test_reset_tries_candidates_until_one_succeeds(self, monkeypatch):
        """First candidate (polkit) failing → the polkitd fallback wins;
        the successful command is reported verbatim."""
        import voice_typer.server.handlers.system_handlers as sh

        class _FakeCompleted:
            def __init__(self, rc: int) -> None:
                self.returncode = rc
                self.stderr = ""

        codes = iter([127, 0])
        calls: list = []

        def _fake_run(args, **kwargs):
            calls.append(args)
            return _FakeCompleted(next(codes))

        monkeypatch.setattr(sh.subprocess, "run", _fake_run)

        command, ok, error = sh._reset_polkit_authorization()

        assert ok is True
        assert command == "pkexec systemctl restart polkitd"
        assert error is None
        assert calls == [
            ["pkexec", "systemctl", "restart", "polkit"],
            ["pkexec", "systemctl", "restart", "polkitd"],
        ]

    def test_reset_all_candidates_fail_reports_pkexec_dismissal(self, monkeypatch):
        """pkexec exit 126 = the user dismissed the polkit dialog →
        reported as such (not a raw exit code)."""
        import voice_typer.server.handlers.system_handlers as sh

        class _FakeCompleted:
            def __init__(self, rc: int) -> None:
                self.returncode = rc
                self.stderr = ""

        monkeypatch.setattr(sh.subprocess, "run", lambda *a, **k: _FakeCompleted(126))

        command, ok, error = sh._reset_polkit_authorization()

        assert ok is False
        assert command is None
        assert error == "pkexec: authentication dismissed"

    def test_reset_reports_last_stderr_when_commands_exit_nonzero(self, monkeypatch):
        """Non-pkexec exit codes fall back to the command's stderr."""
        import voice_typer.server.handlers.system_handlers as sh

        class _FakeCompleted:
            def __init__(self) -> None:
                self.returncode = 1
                self.stderr = "polkit.service not found"

        monkeypatch.setattr(sh.subprocess, "run", lambda *a, **k: _FakeCompleted())

        command, ok, error = sh._reset_polkit_authorization()

        assert ok is False
        assert command is None
        assert error == "polkit.service not found"

    def test_reset_tolerates_missing_pkexec(self, monkeypatch):
        """No ``pkexec`` binary → ok=False with an actionable error
        (never raises)."""
        import voice_typer.server.handlers.system_handlers as sh

        def _boom(*a, **k):
            raise FileNotFoundError("pkexec")

        monkeypatch.setattr(sh.subprocess, "run", _boom)

        command, ok, error = sh._reset_polkit_authorization()

        assert ok is False
        assert command is None
        assert "pkexec" in (error or "")


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
