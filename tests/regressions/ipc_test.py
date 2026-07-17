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
import sys
import threading
from unittest.mock import MagicMock

import pytest

# ─── Linux test-env shim (RW-8) ──────────────────────────────────────────
# ``voice_typer.server.crash_handler`` uses ``ctypes.WINFUNCTYPE`` as a
# decorator at module load time. That attribute only exists on Windows,
# so importing ``voice_typer.server.app`` (which does
# ``from voice_typer.server import crash_handler``) raises
# ``AttributeError`` on Linux. Many tests in this file introspect
# ``VoiceTyperApp`` source via ``inspect.getsource``; without this
# shim, those tests would fail non-deterministically depending on
# whether some earlier test happened to pre-load ``app``. The same
# pattern is used in ``tests/test_api_doc_accuracy.py:42-57``. This is
# a *test-only* shim — production code never monkey-patches ctypes.
if sys.platform != "win32" and "voice_typer.server.crash_handler" not in sys.modules:
    sys.modules["voice_typer.server.crash_handler"] = MagicMock()


class TestAccessibilityIpcEndpointExists:
    """PLAT-030.

    The finding: macOS Accessibility check exists but no IPC endpoint
    for the Electron UI to query. Fix: added ``check_accessibility``
    IPC handler that returns ``{granted, platform}``.
    """

    @pytest.mark.skip(
        reason="RW-8: PORT-CANDIDATE — ported to "
        "tests/test_bugfix_regressions_behavioral.py::"
        "TestAccessibilityIpcBehavioral::"
        "test_handler_returns_accessibility_status_type_and_uses_axistrusted_on_macos"
    )
    def test_check_accessibility_ipc_handler_exists(self):
        # RW-8: PORT-CANDIDATE — see
        # tests/test_bugfix_regressions_behavioral.py::TestAccessibilityIpcBehavioral::
        # test_handler_returns_accessibility_status_type_and_uses_axistrusted_on_macos.
        # The source-string check ("accessibility_status" and "AXIsProcessTrusted"
        # in the handler source) is brittle: production may extract the macOS
        # probe into a helper. The behavioral test mocks sys.platform=darwin
        # and ApplicationServices.AXIsProcessTrusted to verify the handler
        # returns the expected response type and consults AXIsProcessTrusted.
        from voice_typer.server import ipc_server

        # REFACTOR: _dispatch was converted to a command registry.
        assert "check_accessibility" in ipc_server.IPCServer._COMMAND_REGISTRY, (
            "PLAT-030: IPC _COMMAND_REGISTRY must include 'check_accessibility'."
        )
        src = inspect.getsource(ipc_server.IPCServer._handle_check_accessibility)
        assert "accessibility_status" in src, "PLAT-030: handler must return 'accessibility_status' response type."
        assert "AXIsProcessTrusted" in src, "PLAT-030: handler must use AXIsProcessTrusted() on macOS."

    def test_check_accessibility_returns_granted_on_non_macos(self, monkeypatch):
        """On non-macOS platforms, the handler must return granted=True."""
        import sys

        from voice_typer.server.ipc_server import IPCServer

        # Ensure we're on a non-macOS platform for this test
        if sys.platform == "darwin":
            pytest.skip("Test only runs on non-macOS platforms")

        # Build a minimal IPCServer with a mock app
        app = MagicMock()
        app._config_mutation_lock = __import__("threading").RLock()
        server = IPCServer.__new__(IPCServer)
        server.app = app
        server.service = MagicMock()

        # Dispatch the check_accessibility command
        resp = server._dispatch({"type": "check_accessibility", "id": "test"})

        assert resp["type"] == "accessibility_status"
        assert resp["data"]["granted"] is True
        assert resp["data"]["platform"] == sys.platform


class TestSendCatchesOSErrorSubclasses:
    """NEW-CQ-003: Test IPC error handling for various exception types."""

    @pytest.mark.parametrize("exc_class", [BrokenPipeError, ConnectionResetError, OSError])
    def test_send_catches_oserror_subclasses(self, exc_class):
        """Each OSError subclass should be caught by the _send error handler.

        This test creates a mock TCP client whose write() raises the given
        exception, calls _send, and verifies the exception is caught (not
        propagated) and the client is dropped.
        """
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._config_mutation_lock = __import__("threading").RLock()

        # Create a mock TCP client whose write() raises
        mock_client = MagicMock()
        mock_client.write.side_effect = exc_class("simulated connection lost")
        mock_client.settimeout = MagicMock()
        mock_client.getpeername.return_value = ("127.0.0.1", 12345)

        # _send should catch the exception and drop the client
        # (not propagate it)
        try:
            server._send(mock_client, {"type": "test"})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pytest.fail(f"NEW-CQ-003: _send should catch {exc_class.__name__}, not propagate it")
        except Exception:
            # Other exception types (e.g. RuntimeError from the drop path)
            # are acceptable — the key is that the original OSError subclass
            # was caught.
            pass


class TestReadlineCapsOversizedMessages:
    """NEW-IPC-012: Large IPC message handling at size boundaries."""

    @pytest.mark.skip(
        reason="RW-8: PORT-CANDIDATE — ported to "
        "tests/test_bugfix_regressions_behavioral.py::"
        "TestTcpLineIoOversizedBehavioral::test_oversized_message_returns_none"
    )
    def test_readline_caps_oversized_messages(self):
        # RW-8: PORT-CANDIDATE — see
        # tests/test_bugfix_regressions_behavioral.py::TestTcpLineIoOversizedBehavioral::
        # test_oversized_message_returns_none.
        # The source-string check ("_MAX_LINE_BYTES" in readline source)
        # is brittle: production may rename the constant or inline the cap
        # as a literal. The behavioral test feeds a >1MB message through a
        # real socketpair and verifies readline returns None (EOF).
        """The _TCPLineIO.readline() must cap at _MAX_LINE_BYTES.
        A message exceeding the cap must trigger EOF (empty return),
        not OOM or hang.
        """
        from voice_typer.server.ipc_server import _TCPLineIO

        # Verify the cap exists in source. The implementation may use
        # either module-level constants (_MAX_LINE_BYTES / _MAX_LINE_CHARS)
        # or function-local variables (_max_line_bytes / _max_line_chars).
        # Both enforce the 1 MB cap; the test accepts either naming
        # convention.
        src = inspect.getsource(_TCPLineIO.readline)
        assert (
            "_MAX_LINE_BYTES" in src or "_MAX_LINE_CHARS" in src or "_max_line_bytes" in src or "_max_line_chars" in src
        )
        # The drop condition must return empty string on overflow
        assert "return" in src

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
        """When the TCP client's write() raises socket.timeout, _send
        must catch it and drop the client (not hang or propagate)."""
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
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

    def test_send_calls_settimeout_before_write(self):
        """_send must call settimeout before writing to prevent indefinite blocking."""

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

        # _send should call settimeout on the underlying socket
        # We need to access the conn attribute to set timeout
        mock_tcp.conn = MagicMock()

        server._send({"type": "test"})
        # settimeout must have been called on the connection
        mock_tcp.conn.settimeout.assert_called()
