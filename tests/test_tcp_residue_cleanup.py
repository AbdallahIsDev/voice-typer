"""Pin the retired-TCP cleanup: helpers stay importable, --port rejects, docs describe WS."""

from __future__ import annotations

import sys

import pytest


class TestTransportHelpersStayImportable:
    """The socket helpers live in ``ipc.transport`` for injected test clients."""

    def test_reexport_identity(self) -> None:
        from voice_typer.server import ipc_server
        from voice_typer.server.ipc import transport as ipc_transport

        assert ipc_server._TCPLineIO is ipc_transport._TCPLineIO
        assert ipc_server._pick_available_port is ipc_transport._pick_available_port

    def test_pick_available_port_returns_bound_socket(self) -> None:
        from voice_typer.server.ipc.transport import _pick_available_port

        port, sock = _pick_available_port(0, max_tries=1)
        try:
            assert port == sock.getsockname()[1]
        finally:
            sock.close()

    def test_no_tcp_listener_surface(self) -> None:
        from voice_typer.server.ipc_server import IPCServer

        assert not hasattr(IPCServer, "start_tcp")
        assert not hasattr(IPCServer, "_accept_tcp")


class TestPortStubRejects:
    """The ``--port`` flag is a rejecting stub, not a transport selector."""

    def test_port_rejected_with_bad_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voice_typer.__main__ import EXIT_BAD_ARGS
        from voice_typer.server.ipc import entrypoint

        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        with pytest.raises(SystemExit) as exc_info:
            entrypoint.parse_ipc_args()
        assert exc_info.value.code == EXIT_BAD_ARGS


class TestDocsDescribeCurrentTransport:
    """Core-path docs must name the live transport, not the retired one."""

    def test_ipc_server_docstring(self) -> None:
        from voice_typer.server import ipc_server

        doc = ipc_server.__doc__ or ""
        assert "--ws" in doc
        assert "--port 9876" not in doc
        assert "predecessor" not in doc

    def test_transport_docstring_marks_test_support(self) -> None:
        from voice_typer.server.ipc import transport

        doc = transport.__doc__ or ""
        assert "sidecar" in doc.lower() or "--ws" in doc
        assert "test" in doc.lower()

    def test_event_bus_docstring(self) -> None:
        from voice_typer.server import event_bus

        doc = event_bus.__doc__ or ""
        assert "predecessor renderer over TCP" not in doc
        assert "TCP server start" not in doc
        assert "TCP/WS client connect" not in doc

    def test_lifecycle_docstrings(self) -> None:
        import inspect

        from voice_typer.server.app_lifecycle import LifecycleController

        quit_src = inspect.getsource(LifecycleController.quit_app)
        restart_src = inspect.getsource(LifecycleController.restart_app)
        assert "over TCP" not in quit_src
        assert "over the active" not in restart_src
        assert "TCP channel" not in restart_src
