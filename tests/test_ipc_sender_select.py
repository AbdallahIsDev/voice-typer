"""Tests for the select-based write gate in ``sender._send``."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.ipc import sender as sender_module
from voice_typer.server.ipc.rate_limiter import _TCP_WRITE_TIMEOUT_SECONDS
from voice_typer.server.ipc_server import IPCServer

from tests.fixtures.ipc_test_helpers import make_bare_ipc_server, make_buffered_mock_tcp_client


def _make_server() -> IPCServer:
    """Canonical bare send-path IPCServer fixture for ``_send`` tests."""
    return make_bare_ipc_server(send_path=True)


def _patch_select_writable(conn: object) -> patch:
    """writable (the happy path). ``select.poll`` is configured similarly"""
    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4
    mock_mod.select.return_value = ([conn], [], [])
    return patch.object(sender_module, "select", mock_mod)


def _patch_select_not_writable() -> tuple[patch, MagicMock]:
    """Patch ``sender.select`` so BOTH ``select.select`` and"""
    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4
    mock_mod.select.return_value = ([], [], [])
    mock_poller = MagicMock()
    mock_poller.poll.return_value = []
    mock_mod.poll.return_value = mock_poller
    return patch.object(sender_module, "select", mock_mod), mock_mod


def test_select_called_before_sendall() -> None:
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

    assert len(select_calls) >= 1, "select.select must be called before sendall"
    rlist, wlist, xlist, timeout = select_calls[0]
    assert rlist == [], "select.select readable list must be empty"
    assert wlist == [tcp_client.conn], "select.select writable list must contain the tcp client's socket"
    assert xlist == [], "select.select exceptional list must be empty"
    assert timeout == _TCP_WRITE_TIMEOUT_SECONDS, (
        f"select.select timeout must be _TCP_WRITE_TIMEOUT_SECONDS ({_TCP_WRITE_TIMEOUT_SECONDS}), got {timeout}"
    )

    assert "select" in call_order, "select.select was not called"
    assert "sendall" in call_order, "sendall was not called"
    assert call_order.index("select") < call_order.index("sendall"), (
        f"select.select must be called BEFORE sendall; got order {call_order}"
    )


def test_select_timeout_logs_error_and_drops_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``select.select`` returns an empty writable list (the socket"""
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client
    # Pre-populate pending to verify the re-merge on write timeout.
    server._pending_tcp = ['{"old": 1}', '{"old": 2}']

    patch_obj, _mock_mod = _patch_select_not_writable()
    with patch_obj, caplog.at_level(logging.DEBUG, logger="voice_typer"):
        server._send({"type": "test_event", "id": 1})

    # sendall must NOT have been called, select said not writable.
    tcp_client.conn.sendall.assert_not_called()

    # Client must be marked dead.
    assert server._tcp_client is None, (
        "_send must mark the client dead when select returns empty (socket.timeout caught → dead-client path)"
    )

    # The pending snapshot must be re-merged (not silently lost).
    assert len(server._pending_tcp) >= 2, (
        f"Pending entries must be re-merged after write timeout; got {server._pending_tcp!r}"
    )
    assert '{"old": 1}' in server._pending_tcp
    assert '{"old": 2}' in server._pending_tcp

    # The write failure must be logged at DEBUG level.
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("client write failed" in m for m in debug_msgs), (
        f"Expected a DEBUG log mentioning 'client write failed'; got debug messages: {debug_msgs}"
    )


def test_select_writable_sendall_called_with_correct_data() -> None:
    """When ``select.select`` returns the socket as writable, ``_send``"""
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client

    sent_data: list[bytes] = []

    def capture_sendall(data: bytes) -> None:
        sent_data.append(data)

    tcp_client.conn.sendall.side_effect = capture_sendall

    msg = {"type": "test_event", "id": 42, "text": "hello"}
    # Production now uses compact JSON (no whitespace, ensure_ascii=False)
    expected_line = json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n"

    with _patch_select_writable(tcp_client.conn):
        server._send(msg)

    assert len(sent_data) == 1, (
        f"Expected exactly 1 sendall call (just the current line); got {len(sent_data)} calls with data {sent_data}"
    )

    # The sent data must be the JSON-encoded line + newline.
    assert sent_data[0] == expected_line.encode("utf-8"), (
        f"sendall must receive the JSON-encoded line + newline. "
        f"Expected {expected_line.encode('utf-8')!r}, got {sent_data[0]!r}"
    )

    # Client must stay alive (no error).
    assert server._tcp_client is tcp_client, (
        "Client must stay alive when select returns writable and the write succeeds"
    )


def test_select_writable_sendall_called_with_correct_data_and_drain() -> None:
    """When ``select.select`` returns writable AND there are pending"""
    server = _make_server()
    tcp_client = make_buffered_mock_tcp_client()
    server._tcp_client = tcp_client
    server._pending_tcp = ['{"pending": 1}', '{"pending": 2}']

    sent_data: list[bytes] = []
    select_calls: list[tuple] = []

    def capture_sendall(data: bytes) -> None:
        sent_data.append(data)

    tcp_client.conn.sendall.side_effect = capture_sendall

    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4

    def mock_select(rlist, wlist, xlist, timeout=None):
        select_calls.append((rlist, wlist, xlist, timeout))
        return (list(wlist), [], [])

    mock_mod.select.side_effect = mock_select

    msg = {"type": "test_event", "id": 99}
    expected_current = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    # The drain preserves the pending strings as-is (they were
    expected_drain = b'{"pending": 1}\n{"pending": 2}\n'

    with patch.object(sender_module, "select", mock_mod):
        server._send(msg)

    # Two sendall calls: current line + batched drain.
    assert len(sent_data) == 2, f"Expected 2 sendall calls (current + drain); got {len(sent_data)}: {sent_data}"
    assert sent_data[0] == expected_current, (
        f"First sendall must be the current line; expected {expected_current!r}, got {sent_data[0]!r}"
    )
    assert sent_data[1] == expected_drain, (
        f"Second sendall must be the batched drain (2 pending entries); "
        f"expected {expected_drain!r}, got {sent_data[1]!r}"
    )

    # Two select calls: one before each flush.
    assert len(select_calls) == 2, f"Expected 2 select.select calls (one per flush); got {len(select_calls)}"
    for _rlist, wlist, _xlist, timeout in select_calls:
        assert timeout == _TCP_WRITE_TIMEOUT_SECONDS
        assert wlist == [tcp_client.conn]

    # All pending drained, none re-merged.
    assert len(server._pending_tcp) == 0, f"Pending must be fully drained; got {server._pending_tcp!r}"

    # Client stays alive.
    assert server._tcp_client is tcp_client


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
