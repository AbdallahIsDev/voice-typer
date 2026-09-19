"""behavioral tests ported from source-string meta-tests."""

from __future__ import annotations

import contextlib
import socket as _socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestTrayIconBaseIcoBehavioral:
    """PORT of ``test_get_icon_path_looks_for_base_ico``."""

    def test_get_icon_path_returns_ico_when_available(self, monkeypatch):
        from voice_typer.server import tray_icon
        from voice_typer.server.tray_types import AppState

        # Force is_windows() to return True so the .ico lookup path runs.
        monkeypatch.setattr(tray_icon, "is_windows", lambda: True)

        # Mock Path.exists so that ONLY tray-mic.ico exists (neither
        def fake_exists(self: Path) -> bool:
            return self.name == "tray-mic.ico"

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = tray_icon._get_icon_path(AppState.IDLE, size=32)

        assert result is not None, (
            "PLAT-024: _get_icon_path must return the base tray-mic.ico path on Windows when the base .ico exists."
        )
        assert result.name == "tray-mic.ico", f"PLAT-024: _get_icon_path must return tray-mic.ico, got {result.name!r}"


class TestAccessibilityIpcBehavioral:
    """PORT of ``test_check_accessibility_ipc_handler_exists``."""

    _SKIP_REASON = (
        "check_accessibility behavior now covered by "
        "tests/handlers/test_system_handlers.py::TestCheckAccessibility "
        "(re-added to the registry 2026-08-10, finding #919 part b). "
        "Kept skipped for historical context."
    )

    def _make_server(self):
        """Build a minimal IPCServer with a mock app + service."""
        from tests.fixtures.ipc_test_helpers import make_bare_ipc_server

        return make_bare_ipc_server()

    @pytest.mark.skip(reason=_SKIP_REASON)
    def test_handler_returns_accessibility_status_type_and_uses_axistrusted_on_macos(self, monkeypatch):
        # Import IPCServer FIRST so ipc_server.py fully loads (including
        from voice_typer.server.handlers import system_handlers
        from voice_typer.server.ipc_server import IPCServer  # noqa: F401

        monkeypatch.setattr(system_handlers, "is_macos", lambda: True)

        # Mock ctypes.cdll.LoadLibrary to return a fake library whose
        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 1  # truthy
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        import ctypes

        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        # Dispatch the check_accessibility command.
        server = self._make_server()
        resp = server._dispatch({"type": "check_accessibility", "id": "t1"})

        # The response must be accessibility_status (not error, not granted).
        assert resp["type"] == "accessibility_status", (
            f"PLAT-030: handler must return type=accessibility_status, got {resp['type']!r}"
        )
        assert resp["data"]["granted"] is True, (
            "PLAT-030: handler must consult AXIsProcessTrusted(), granted "
            f"should be True when AXIsProcessTrusted returns 1, got {resp['data']['granted']}"
        )
        # AXIsProcessTrusted must have been called (this is the behavioral
        assert fake_lib.AXIsProcessTrusted.called, "PLAT-030: handler must call AXIsProcessTrusted() on macOS."

    @pytest.mark.skip(reason=_SKIP_REASON)
    def test_handler_returns_false_when_axistrusted_returns_zero(self, monkeypatch):
        """Sanity: when AXIsProcessTrusted returns 0, granted must be False."""
        from voice_typer.server.handlers import system_handlers
        from voice_typer.server.ipc_server import IPCServer  # noqa: F401

        monkeypatch.setattr(system_handlers, "is_macos", lambda: True)

        fake_lib = MagicMock()
        fake_lib.AXIsProcessTrusted.return_value = 0  # falsy
        fake_cdll = MagicMock()
        fake_cdll.LoadLibrary.return_value = fake_lib
        import ctypes

        monkeypatch.setattr(ctypes, "cdll", fake_cdll)

        server = self._make_server()
        resp = server._dispatch({"type": "check_accessibility", "id": "t2"})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is False, "PLAT-030: granted must be False when AXIsProcessTrusted returns 0."


class TestTcpLineIoOversizedBehavioral:
    """PORT of ``test_readline_caps_oversized_messages``."""

    @pytest.mark.skipif(
        not hasattr(_socket, "AF_UNIX"),
        reason="AF_UNIX not available on Windows",
    )
    def test_oversized_message_returns_none(self):
        from voice_typer.server.ipc_server import _TCPLineIO

        srv, cli = _socket.socketpair(_socket.AF_UNIX, _socket.SOCK_STREAM)
        try:
            # Spawn a thread to write a >1MB message from the client side.
            oversized = b"x" * (2 * 1024 * 1024) + b"\n"

            def write_oversized():
                with contextlib.suppress(OSError):
                    cli.sendall(oversized)

            t = threading.Thread(target=write_oversized)
            t.start()

            # Read from the server side via _TCPLineIO. The cap must
            io = _TCPLineIO(srv)
            line = io.readline()

            t.join(timeout=2.0)

            assert line is None or len(line) < 1024 * 1024, (
                "NEW-IPC-012: _TCPLineIO.readline must cap oversized messages "
                f"at 1MB and return None (EOF). Got a line of length "
                f"{len(line) if line else 0}, the cap is missing or too large."
            )
        finally:
            srv.close()
            cli.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
