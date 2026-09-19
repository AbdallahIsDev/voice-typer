"""Tests for the select-based write-timeout in ``sender._send``."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.ipc import sender as sender_module
from voice_typer.server.ipc.rate_limiter import _TCP_WRITE_TIMEOUT_SECONDS
from voice_typer.server.ipc_server import IPCServer

from tests.fixtures.ipc_test_helpers import make_bare_ipc_server, make_buffered_mock_tcp_client


def _make_server() -> IPCServer:
    """Canonical bare send-path IPCServer fixture for ``_send`` tests."""
    return make_bare_ipc_server(send_path=True)


def _patch_select_not_writable() -> tuple[patch, MagicMock]:
    """``select.poll`` report the socket as NOT writable (timeout path)."""
    mock_mod = MagicMock()
    mock_mod.select.return_value = ([], [], [])
    # Configure the poll cross-check to also return empty (not writable).
    mock_poller = MagicMock()
    mock_poller.poll.return_value = []  # empty → not writable
    mock_mod.poll.return_value = mock_poller
    mock_mod.POLLOUT = 4  # select.POLLOUT value (doesn't matter for mock)
    return patch.object(sender_module, "select", mock_mod), mock_mod


def test_send_calls_select_before_sendall() -> None:
    """``_send`` must call ``select.select([], [conn], [], timeout)``"""
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client

    call_order: list[str] = []
    select_calls: list[tuple] = []

    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4

    def mock_select(rlist, wlist, xlist, timeout=None):
        select_calls.append((rlist, wlist, xlist, timeout))
        call_order.append("select")
        return (list(wlist), [], [])

    mock_mod.select.side_effect = mock_select

    def tracking_sendall(_data) -> None:
        call_order.append("sendall")

    tcp_client.conn.sendall.side_effect = tracking_sendall

    with patch.object(sender_module, "select", mock_mod):
        server._send({"type": "test_event", "id": 1})

    assert len(select_calls) >= 1, "select.select must be called at least once before sendall"
    rlist, wlist, xlist, timeout = select_calls[0]
    assert rlist == [], "select.select readable list must be empty"
    assert wlist == [tcp_client.conn], "select.select writable list must contain the tcp client's socket"
    assert xlist == [], "select.select exceptional list must be empty"
    assert timeout == _TCP_WRITE_TIMEOUT_SECONDS, (
        f"select.select timeout must be _TCP_WRITE_TIMEOUT_SECONDS ({_TCP_WRITE_TIMEOUT_SECONDS}), got {timeout}"
    )

    assert "select" in call_order, "select.select was not called"
    assert "sendall" in call_order, "sendall was not called"
    sel_idx = call_order.index("select")
    send_idx = call_order.index("sendall")
    assert sel_idx < send_idx, f"select.select must be called BEFORE sendall; got order {call_order}"


def test_send_does_not_call_gettimeout_or_settimeout() -> None:
    """socket, the select-based approach never mutates the socket's timeout"""
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client

    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4
    mock_mod.select.return_value = ([tcp_client.conn], [], [])

    with patch.object(sender_module, "select", mock_mod):
        server._send({"type": "test_event", "id": 1})

    tcp_client.conn.gettimeout.assert_not_called()
    tcp_client.conn.settimeout.assert_not_called()


# ── 3. _await_socket_writable raises socket.timeout when select empty ─


def test_await_socket_writable_raises_timeout_when_select_empty() -> None:
    """When ``select.select`` returns an empty writable list (the socket"""
    fake_conn = MagicMock()

    patch_obj, mock_mod = _patch_select_not_writable()
    with patch_obj, pytest.raises(socket.timeout):
        sender_module._await_socket_writable(fake_conn)

    # Verify select.select was called with the right timeout.
    mock_mod.select.assert_called_once_with([], [fake_conn], [], _TCP_WRITE_TIMEOUT_SECONDS)


def test_send_handles_timeout_when_select_returns_empty() -> None:
    """
    When ``select.select`` returns empty (socket not writable),
    ``_send`` must NOT call ``sendall`` and must mark the client dead
    """
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client
    # Pre-populate pending to verify the  re-merge on write failure.
    server._pending_tcp = ['{"old": 1}']

    patch_obj, _mock_mod = _patch_select_not_writable()
    with patch_obj:
        server._send({"type": "test_event", "id": 1})

    # sendall must NOT have been called, select said not writable, so
    tcp_client.conn.sendall.assert_not_called()

    # Client must be marked dead (the timeout was caught and the dead-
    assert server._tcp_client is None, (
        "_send must mark the client dead when select returns empty (socket.timeout caught → dead-client path)"
    )

    # The pending snapshot must be re-merged ( contract).
    assert len(server._pending_tcp) >= 1, (
        f"Pending entries must be re-merged after the write timeout (got {server._pending_tcp!r})"
    )


def test_send_calls_sendall_when_select_returns_ready() -> None:
    """When ``select.select`` returns the socket as writable, ``_send``"""
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client

    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4
    mock_mod.select.return_value = ([tcp_client.conn], [], [])

    with patch.object(sender_module, "select", mock_mod):
        server._send({"type": "test_event", "id": 1})

    tcp_client.conn.sendall.assert_called()
    # Client should still be alive (no error occurred).
    assert server._tcp_client is tcp_client, "Client must stay alive when select returns ready and the write succeeds"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
