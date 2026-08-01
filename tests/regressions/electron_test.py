"""Regression tests split out of the former ``tests/test_bugfix_regressions.py``.

This module is part of the ``tests/regressions/`` package created by
REF-4. The class/method names, assertion logic, and imports below are
preserved verbatim from the original 4446-line monolith — only file
location has changed.

Common preamble (imports + Linux test-env shim) is identical to the
original file so that every test in this module sees the same global
state the monolith provided.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch


# the previous Linux test-env shim that aliased
# ``ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE`` and inserted a ``MagicMock``
# for ``voice_typer.server.crash_handler`` into ``sys.modules`` has been
# removed. ``crash_handler.py`` now gates the ``@ctypes.WINFUNCTYPE(...)``
# decorator behind ``sys.platform == "win32"``, so the module imports
# cleanly on Linux/macOS without any test-infrastructure shim.
class TestElectronLogFilesCaptured:
    """RACE-009.

    The finding: subprocess.DEVNULL used for Electron launches, making
    crashes invisible. Fix: added ``_electron_log_files()`` helper that
    opens log files in the config dir; replaced DEVNULL at all 3
    Electron launch sites.
    """

    def test_electron_log_files_helper_exists(self):
        from voice_typer.server import autostart_launcher

        assert hasattr(autostart_launcher, "_electron_log_files"), (
            "RACE-009: _electron_log_files helper must exist in autostart_launcher."
        )
        assert callable(autostart_launcher._electron_log_files)

    def test_electron_log_files_returns_file_objects(self, tmp_path, monkeypatch):
        """The helper must return a dict with stdout/stderr as open file
        objects (not DEVNULL) when the log dir is writable.
        """
        from voice_typer.server import config as cfg_mod
        from voice_typer.server.autostart_launcher import _electron_log_files

        # Patch _config_dir to point to tmp_path
        monkeypatch.setattr(cfg_mod, "_config_dir", lambda: tmp_path)

        result = _electron_log_files()
        assert "stdout" in result
        assert "stderr" in result
        assert "stdin" in result
        # stdout and stderr should be file objects, not DEVNULL
        assert result["stdout"] is not __import__("subprocess").DEVNULL
        assert result["stderr"] is not __import__("subprocess").DEVNULL
        # stdin can stay as DEVNULL (Electron doesn't need stdin)
        # Close the file objects to avoid leaks
        if hasattr(result["stdout"], "close"):
            result["stdout"].close()
        if hasattr(result["stderr"], "close"):
            result["stderr"].close()


class TestElectronNotificationIpcEndpoint:
    """TRAY-035.

    The finding: notification duration controlled by OS, not app.
    pystray's `notify()` has no duration parameter. Fix: added
    `show_electron_notification` IPC handler that pushes an
    `electron_notification` event to the Electron UI, which can
    display a persistent toast/banner with user-controlled duration.

    Stale-test refresh: the ``show_electron_notification``
    command was REMOVED from ``IPCServer._COMMAND_REGISTRY`` because
    the Tauri host now handles notifications via a dedicated Rust
    command. The Python-side handler method
    ``SystemHandlersMixin._handle_show_electron_notification`` still
    exists for the legacy Electron path. These regression tests now
    assert the handler method exists and is callable directly
    (instead of routing through ``_dispatch`` which no longer
    recognises the command).
    """

    def test_ipc_handler_exists(self):
        from voice_typer.server import ipc_server

        # The handler method must exist on the IPCServer class (mixed in
        # via SystemHandlersMixin). The command was REMOVED from
        # _COMMAND_REGISTRY because the Tauri host handles the
        # notification path natively now; the Python handler remains
        # for the legacy Electron code path.
        assert hasattr(ipc_server.IPCServer, "_handle_show_electron_notification"), (
            "TRAY-035: IPCServer must expose '_handle_show_electron_notification' "
            "(via SystemHandlersMixin) so the legacy Electron path can still "
            "push a notification event"
        )
        assert callable(ipc_server.IPCServer._handle_show_electron_notification)

    def test_handler_validates_data_is_dict(self):
        """The handler must reject non-dict data with an error response."""
        from voice_typer.server.ipc_server import IPCServer

        # Build a minimal server with a mock app
        app = MagicMock()
        app._config_mutation_lock = __import__("threading").RLock()
        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        server.app = app
        server.service = MagicMock()

        # Call the handler method directly. The command was removed
        # from _COMMAND_REGISTRY (Tauri host handles it natively), so
        # _dispatch would route to ``unknown_command`` — but the
        # handler method itself is unchanged.
        resp = server._handle_show_electron_notification("not a dict", {"id": "test"})
        assert resp["type"] == "error"
        # the validation helper now emits the namespaced
        # ``client.invalid_payload`` as the primary ``code`` (with the
        # legacy bare ``invalid_payload`` preserved in ``legacy_code``).
        # Accept either form so the test survives the one-release-cycle
        # migration window.
        assert resp["data"]["code"].endswith("invalid_payload"), (
            f"expected code endswith 'invalid_payload', got {resp['data']['code']!r}"
        )


class TestElectronNotificationFieldValidation:
    """SEC-VALIDATE-001: per-field input validation on the
    ``show_electron_notification`` IPC handler.

    Before this fix the handler coerced every field with ``str()`` /
    ``int()`` / ``bool()`` and relied on the surrounding try/except
    to convert ``ValueError`` (from ``int("abc")``) into a generic
    "error" response that echoed the raw Python exception text.  It
    also treated ``bool("false")`` as ``True`` because any non-empty
    string is truthy.  Both behaviours are wrong: the client should
    see a structured ``code: "invalid_field"`` error with the field
    name and a human-readable message, and a stringly-typed
    ``"critical": "false"`` should be rejected rather than silently
    escalate the notification.

    Stale-test refresh: the test now calls the handler method
    directly (``_handle_show_electron_notification``) instead of
    routing through ``_dispatch`` — the command was removed from
    ``_COMMAND_REGISTRY`` when the Tauri host took over the
    notification path natively.
    """

    def _make_server(self):
        """Build a minimal IPCServer with a mock app + service.

        Reused across every test so we don't pay the cost of
        constructing a real VoiceTyperApp per case.
        """
        from threading import RLock
        from unittest.mock import MagicMock

        from voice_typer.server.ipc_server import IPCServer

        app = MagicMock()
        app._config_mutation_lock = RLock()
        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        server.app = app
        server.service = MagicMock()
        return server

    def test_non_numeric_duration_ms_returns_invalid_field(self):
        """``duration_ms: "abc"`` must return code=invalid_field, not a ValueError echo."""
        server = self._make_server()
        resp = server._handle_show_electron_notification(
            {"title": "Hi", "message": "Body", "duration_ms": "abc"},
            {"id": "t1"},
        )
        assert resp["type"] == "error"
        # code is now namespaced as ``client.invalid_field``
        # (legacy bare form preserved in ``legacy_code``). Accept either.
        assert resp["data"]["code"].endswith("invalid_field"), (
            f"expected code endswith 'invalid_field', got {resp['data']['code']!r}"
        )
        assert resp["data"]["field"] == "duration_ms"
        # The message must NOT contain Python's internal ValueError text.
        assert "invalid literal" not in resp["data"]["message"]

    def test_stringly_critical_is_rejected(self):
        """``critical: "false"`` (string) must be rejected, not silently coerced to True."""
        server = self._make_server()
        resp = server._handle_show_electron_notification(
            {"title": "Hi", "message": "Body", "critical": "false"},
            {"id": "t2"},
        )
        assert resp["type"] == "error"
        # code is now namespaced as ``client.invalid_field``
        # (legacy bare form preserved in ``legacy_code``). Accept either.
        assert resp["data"]["code"].endswith("invalid_field"), (
            f"expected code endswith 'invalid_field', got {resp['data']['code']!r}"
        )
        assert resp["data"]["field"] == "critical"

    def test_non_string_title_is_rejected(self):
        """``title: 42`` must be rejected with code=invalid_field rather than silently stringified."""
        server = self._make_server()
        resp = server._handle_show_electron_notification(
            {"title": 42, "message": "Body"},
            {"id": "t3"},
        )
        assert resp["type"] == "error"
        # code is now namespaced as ``client.invalid_field``
        # (legacy bare form preserved in ``legacy_code``). Accept either.
        assert resp["data"]["code"].endswith("invalid_field"), (
            f"expected code endswith 'invalid_field', got {resp['data']['code']!r}"
        )
        assert resp["data"]["field"] == "title"

    def test_duration_ms_is_clamped_to_24h(self):
        """A huge ``duration_ms`` is clamped, not rejected — callers can pass any int."""

        server = self._make_server()
        captured = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            resp = server._handle_show_electron_notification(
                {
                    "title": "Hi",
                    "message": "Body",
                    "duration_ms": 10_000_000_000,  # ~115 days — well over the 24h cap
                },
                {"id": "t4"},
            )
        assert resp["type"] == "ack"
        assert captured["data"]["duration_ms"] == 24 * 60 * 60 * 1000

    def test_well_formed_payload_still_works(self):
        """Sanity: a well-formed payload must still push the event and ack."""

        server = self._make_server()
        captured = {}
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
                {"id": "t5"},
            )
        assert resp["type"] == "ack"
        # event renamed from `electron_notification` → `notification`
        # (platform-agnostic — the Tauri Rust host no longer renames it).
        assert captured["type"] == "notification"
        assert captured["data"] == {
            "title": "Hello",
            "message": "World",
            "duration_ms": 5000,
            "critical": True,
        }

    def test_default_values_when_fields_omitted(self):
        """Sanity: omitted fields default to title='Voice Typer', message='', duration_ms=0, critical=False."""

        server = self._make_server()
        captured = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            resp = server._handle_show_electron_notification(
                {},
                {"id": "t6"},
            )
        assert resp["type"] == "ack"
        assert captured["data"] == {
            "title": "Voice Typer",
            "message": "",
            "duration_ms": 0,
            "critical": False,
        }


class TestUpxDisabledInPyinstallerSpec:
    """TEST-034.

    The finding: upx=True triggers AV false positives. Investigation:
    upx is already set to False in voice-typer.spec. This test pins
    that state.
    """

    def test_upx_is_false_in_spec(self):
        # KEEP — pins  (upx=False in voice-typer.spec).
        # A behavioral test would need to run PyInstaller and inspect the
        # build output, which is heavy; the file-content check catches
        # reintroduction of upx=True directly.
        from pathlib import Path

        spec_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "build" / "voice-typer.spec"
        src = spec_path.read_text(encoding="utf-8")
        assert "upx=False" in src, "TEST-034: voice-typer.spec must set upx=False to prevent AV false positives"


class TestSettingsRendererCallsPythonBridgeCall:
    """TypeScript error: Property 'ipc' does not exist on type 'PythonBridge'.

    The finding: Settings.tsx:394 called ``window.python?.ipc(...)``
    but the PythonBridge type only exposes ``call`` and ``onEvent``.
    Fix: replaced ``.ipc(...)`` with ``.call(...)``.
    """

    def test_settings_uses_call_not_ipc(self):
        # KEEP — pins TS error fix (Settings uses window.python?.call(),
        # not .ipc()). A behavioral test would need to render the component
        # and click a setting, but the TypeScript compiler already catches
        # .ipc() usage at build time; the file-content check is a belt-and-
        # suspenders guard against reintroduction in case the type check
        # is bypassed.
        #
        # The Settings UI was refactored: GeneralSettingsSection.tsx now
        # delegates to the i18n module's ``setLocale()`` helper, which in
        # turn calls ``window.python.call({type: "set_tray_locale", ...})``
        # in ``pushLocaleToPythonBackend`` (i18n.ts). The actual
        # ``window.python.call(...)`` invocation therefore lives in i18n.ts
        # — both files MUST use ``.call(`` and MUST NOT use ``.ipc(`` so
        # the TypeScript PythonBridge type (which only exposes ``call`` and
        # ``onEvent``) does not break the build.
        client_root = Path(__file__).resolve().parent.parent.parent / "voice_typer" / "client"
        settings_path = (
            client_root / "src" / "renderer" / "src" / "components" / "settings" / "GeneralSettingsSection.tsx"
        )
        i18n_path = client_root / "src" / "renderer" / "src" / "i18n" / "i18n.ts"
        settings_src = settings_path.read_text(encoding="utf-8")
        i18n_src = i18n_path.read_text(encoding="utf-8")

        # GeneralSettingsSection.tsx delegates to ``setLocale()`` and must
        # NOT call the Python bridge directly via ``.ipc(`` (the original
        # TS error). It also must not use ``.call(`` directly — that's now
        # encapsulated in i18n's ``pushLocaleToPythonBackend``.
        assert "window.python?.ipc(" not in settings_src, (
            "TS error: GeneralSettingsSection.tsx must NOT use window.python?.ipc() — "
            "the PythonBridge type does not expose an 'ipc' method"
        )
        assert "window.python?.call(" not in settings_src, (
            "TS error: GeneralSettingsSection.tsx must NOT call "
            "window.python?.call(...) directly — setLocale dispatch is "
            "encapsulated in i18n.ts's pushLocaleToPythonBackend. This "
            "negative check pins the delegation boundary."
        )

        # i18n.ts owns the actual ``window.python.call(...)`` dispatch
        # (inside ``pushLocaleToPythonBackend``). It MUST use ``.call(``
        # (positive check) and MUST NOT use ``.ipc(`` (negative check).
        assert "set_tray_locale" in i18n_src, (
            "i18n.ts must dispatch a 'set_tray_locale' message so the tray "
            "menu / tooltip / OS notifications localise with the renderer."
        )
        assert ".call(" in i18n_src, (
            "TS error: i18n.ts must dispatch set_tray_locale via "
            "window.python.call(...) — the PythonBridge type only exposes "
            "'call' and 'onEvent' (not 'ipc')."
        )
        assert "window.python?.ipc(" not in i18n_src, (
            "TS error: i18n.ts must NOT use window.python?.ipc() — "
            "the PythonBridge type does not expose an 'ipc' method"
        )

    def test_python_bridge_type_has_no_ipc_method(self):
        """The PythonBridge interface must NOT expose an 'ipc' method."""
        ipc_types_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "types"
            / "ipc.ts"
        )
        src = ipc_types_path.read_text(encoding="utf-8")
        # Extract the PythonBridge interface block
        bridge_start = src.find("export interface PythonBridge")
        assert bridge_start >= 0, "PythonBridge interface not found"
        # Find the closing brace
        brace_start = src.find("{", bridge_start)
        brace_end = src.find("}", brace_start)
        bridge_block = src[bridge_start:brace_end]
        assert "ipc" not in bridge_block, "TS error: PythonBridge interface must NOT have an 'ipc' method"
        assert "call:" in bridge_block, "PythonBridge must have a 'call' method"
