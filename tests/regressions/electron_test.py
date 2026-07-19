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

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# WP-1: the previous Linux test-env shim that aliased
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

    @pytest.mark.skip(
        reason="RW-8: PORT-CANDIDATE — ported to "
        "tests/test_bugfix_regressions_behavioral.py::"
        "TestElectronLogFilesBehavioral::test_all_electron_launch_sites_call_log_files_helper"
    )
    def test_electron_launch_sites_use_log_files_not_devnull(self):
        # RW-8: PORT-CANDIDATE — see
        # tests/test_bugfix_regressions_behavioral.py::TestElectronLogFilesBehavioral::
        # test_all_electron_launch_sites_call_log_files_helper.
        # The source-string count (>= 3 occurrences of `_electron_log_files()`)
        # is brittle: production may consolidate the 3 launch sites into a
        # shared helper without losing the invariant. The behavioral test
        # mocks subprocess.Popen for each launch entry point and verifies
        # the helper is invoked.
        from voice_typer.server import autostart_launcher

        src = inspect.getsource(autostart_launcher)
        # All 3 Electron launch functions must call _electron_log_files
        assert src.count("_electron_log_files()") >= 3, (
            "RACE-009: all 3 Electron launch sites must call _electron_log_files()."
        )


class TestElectronNotificationIpcEndpoint:
    """TRAY-035.

    The finding: notification duration controlled by OS, not app.
    pystray's `notify()` has no duration parameter. Fix: added
    `show_electron_notification` IPC handler that pushes an
    `electron_notification` event to the Electron UI, which can
    display a persistent toast/banner with user-controlled duration.
    """

    def test_ipc_handler_exists(self):
        from voice_typer.server import ipc_server

        # REFACTOR: _dispatch was converted to a command registry.
        assert "show_electron_notification" in ipc_server.IPCServer._COMMAND_REGISTRY, (
            "TRAY-035: IPC _COMMAND_REGISTRY must include 'show_electron_notification'"
        )

    @pytest.mark.skip(
        reason="RW-8: DELETE-CANDIDATE — redundant with "
        "TestElectronNotificationFieldValidation (same file), which "
        "dispatches the handler behaviorally and verifies the published "
        "event contains electron_notification, duration_ms, and critical "
        "fields."
    )
    def test_handler_pushes_electron_notification_event(self):
        # RW-8: DELETE-CANDIDATE — redundant with TestElectronNotificationFieldValidation
        # (same file), which dispatches the handler behaviorally with various
        # payloads and verifies the published event contains electron_notification,
        # duration_ms, and critical fields. The source-string check here adds
        # no additional coverage. Skipped to avoid double-maintenance.
        from voice_typer.server import ipc_server

        # REFACTOR: check the handler method source instead of _dispatch.
        src = inspect.getsource(ipc_server.IPCServer._handle_show_electron_notification)
        assert "electron_notification" in src, "TRAY-035: handler must push an 'electron_notification' event"
        assert "duration_ms" in src, "TRAY-035: handler must support a duration_ms parameter"
        assert "critical" in src, "TRAY-035: handler must support a critical flag"

    def test_handler_validates_data_is_dict(self):
        """The handler must reject non-dict data with an error response."""
        from voice_typer.server.ipc_server import IPCServer

        # Build a minimal server with a mock app
        app = MagicMock()
        app._config_mutation_lock = __import__("threading").RLock()
        server = IPCServer.__new__(IPCServer)
        server.app = app
        server.service = MagicMock()

        # Dispatch with non-dict data
        resp = server._dispatch({"type": "show_electron_notification", "data": "not a dict", "id": "test"})
        assert resp["type"] == "error"
        assert "data: object" in resp["data"]["message"]


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
        server.app = app
        server.service = MagicMock()
        return server

    def test_non_numeric_duration_ms_returns_invalid_field(self):
        """``duration_ms: "abc"`` must return code=invalid_field, not a ValueError echo."""
        server = self._make_server()
        resp = server._dispatch(
            {
                "type": "show_electron_notification",
                "data": {"title": "Hi", "message": "Body", "duration_ms": "abc"},
                "id": "t1",
            }
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "duration_ms"
        # The message must NOT contain Python's internal ValueError text.
        assert "invalid literal" not in resp["data"]["message"]

    def test_stringly_critical_is_rejected(self):
        """``critical: "false"`` (string) must be rejected, not silently coerced to True."""
        server = self._make_server()
        resp = server._dispatch(
            {
                "type": "show_electron_notification",
                "data": {"title": "Hi", "message": "Body", "critical": "false"},
                "id": "t2",
            }
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "critical"

    def test_non_string_title_is_rejected(self):
        """``title: 42`` must be rejected with code=invalid_field rather than silently stringified."""
        server = self._make_server()
        resp = server._dispatch(
            {
                "type": "show_electron_notification",
                "data": {"title": 42, "message": "Body"},
                "id": "t3",
            }
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "title"

    def test_duration_ms_is_clamped_to_24h(self):
        """A huge ``duration_ms`` is clamped, not rejected — callers can pass any int."""

        server = self._make_server()
        captured = {}
        with patch(
            "voice_typer.server.event_bus.publish",
            lambda msg: captured.update(msg),
        ):
            resp = server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {
                        "title": "Hi",
                        "message": "Body",
                        "duration_ms": 10_000_000_000,  # ~115 days — well over the 24h cap
                    },
                    "id": "t4",
                }
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
            resp = server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {
                        "title": "Hello",
                        "message": "World",
                        "duration_ms": 5000,
                        "critical": True,
                    },
                    "id": "t5",
                }
            )
        assert resp["type"] == "ack"
        # CR-8: event renamed from `electron_notification` → `notification`
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
            resp = server._dispatch(
                {
                    "type": "show_electron_notification",
                    "data": {},
                    "id": "t6",
                }
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
        # RW-8: KEEP — pins TEST-034 (upx=False in voice-typer.spec).
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
        # RW-8: KEEP — pins TS error fix (Settings uses window.python?.call(),
        # not .ipc()). A behavioral test would need to render the component
        # and click a setting, but the TypeScript compiler already catches
        # .ipc() usage at build time; the file-content check is a belt-and-
        # suspenders guard against reintroduction in case the type check
        # is bypassed.
        # The Settings UI was refactored: ``window.python?.call(...)`` now
        # lives in the dedicated GeneralSettingsSection component
        # (formerly inline in Settings.tsx).
        settings_path = (
            Path(__file__).resolve().parent.parent.parent
            / "voice_typer"
            / "client"
            / "src"
            / "renderer"
            / "src"
            / "components"
            / "settings"
            / "GeneralSettingsSection.tsx"
        )
        src = settings_path.read_text(encoding="utf-8")
        # Must use .call( not .ipc(
        assert "window.python?.call(" in src, (
            "TS error: GeneralSettingsSection.tsx must use window.python?.call() not .ipc()"
        )
        # Must NOT use .ipc( anywhere
        assert "window.python?.ipc(" not in src, (
            "TS error: GeneralSettingsSection.tsx must NOT use window.python?.ipc() — "
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
