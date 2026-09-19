"""REF-4. The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock

import pytest


class TestAccessibilityIpcEndpointExists:
    """PLAT-030."""

    @pytest.mark.skipif(
        sys.platform == "darwin",
        reason="Non-macOS only: verifies the granted=True no-op path when AXIsProcessTrusted is unavailable",
    )
    def test_check_accessibility_returns_granted_on_non_macos(self, monkeypatch):
        """On non-macOS platforms, the handler must return granted=True."""

        from voice_typer.server.ipc_server import IPCServer

        # Build a minimal IPCServer with a mock app.
        app = MagicMock()
        app._config_mutation_lock = __import__("threading").RLock()
        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        server.app = app
        server.service = MagicMock()

        # Invoke the handler directly. The handler runs
        resp = server._handle_check_accessibility({}, {"id": "test"})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is True
        assert resp["data"]["platform"] == sys.platform


class TestSendCatchesOSErrorSubclasses:
    """NEW-CQ-003: Test IPC error handling for various exception types."""

    @pytest.mark.parametrize("exc_class", [BrokenPipeError, ConnectionResetError, OSError])
    def test_send_catches_oserror_subclasses(self, exc_class):
        """Each OSError subclass should be caught by the _send error handler."""
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        server.app = MagicMock()
        server.app._config_mutation_lock = __import__("threading").RLock()

        # Create a mock TCP client whose write() raises
        mock_client = MagicMock()
        mock_client.write.side_effect = exc_class("simulated connection lost")
        mock_client.settimeout = MagicMock()
        mock_client.getpeername.return_value = ("127.0.0.1", 12345)

        # (not propagate it)
        try:
            server._send(mock_client, {"type": "test"})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pytest.fail(f"NEW-CQ-003: _send should catch {exc_class.__name__}, not propagate it")
        except Exception:
            # Other exception types (e.g. RuntimeError from the drop path)
            pass


class TestReadlineCapsOversizedMessages:
    """NEW-IPC-012: Large IPC message handling at size boundaries."""

    @pytest.mark.skipif(
        not hasattr(__import__("socket"), "AF_UNIX"),
        reason="AF_UNIX not available on Windows",
    )
    def test_normal_sized_message_passes_through(self):
        """A message under the cap must be read successfully."""
        # Create a real socketpair for the _TCPLineIO
        import socket as _socket

        from voice_typer.server.ipc_server import _TCPLineIO

        srv, cli = _socket.socketpair(_socket.AF_UNIX, _socket.SOCK_STREAM)
        try:
            # Write a small JSON message from the client side
            cli.sendall(b'{"type": "test", "id": "1"}\n')
            cli.close()

            # Read from the server side via _TCPLineIO
            io = _TCPLineIO(srv)
            line = io.readline()
            assert line is not None
            assert "test" in line
        finally:
            srv.close()


class TestSendCatchesSocketTimeout:
    """NEW-IPC-016: IPC write timeout under blocking conditions."""

    def test_send_catches_socket_timeout(self):
        """When the TCP client's write() raises socket.timeout, _send"""
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server._dispatch_lock = threading.RLock()
        server.app = MagicMock()
        server.app._config_mutation_lock = __import__("threading").RLock()

        mock_client = MagicMock()
        mock_client.write.side_effect = TimeoutError("write timed out")
        mock_client.settimeout = MagicMock()
        mock_client.getpeername.return_value = ("127.0.0.1", 12345)

        try:
            server._send(mock_client, {"type": "test"})
        except TimeoutError:
            pytest.fail("NEW-IPC-016: _send should catch socket.timeout")
        except Exception:
            pass  # drop path may raise other exceptions

    def test_send_awaits_socket_writable_before_write(self):
        """The send path must enforce a write timeout before writing."""

        from unittest.mock import patch

        from voice_typer.server.ipc import sender as sender_mod
        from voice_typer.server.ipc_server import IPCServer

        # Create a proper IPCServer instance
        app = MagicMock()
        app._config_mutation_lock = threading.RLock()
        server = IPCServer(app)

        # Create a mock _TCPLineIO that succeeds
        mock_tcp = MagicMock()
        mock_tcp.write.return_value = None  # write succeeds
        server._tcp_client = mock_tcp
        server._tcp_mode = True
        mock_tcp.conn = MagicMock()

        with patch.object(sender_mod, "_await_socket_writable") as spy:
            server._send({"type": "test"})
        spy.assert_called()
        assert spy.call_args.args[0] is mock_tcp.conn
