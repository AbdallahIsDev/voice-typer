"""Tests for the select-based write gate in ``sender._send``.

Verifies the three core contracts of the ``_await_socket_writable`` refactor
(which replaced the per-write ``gettimeout`` / ``settimeout`` / restore
dance with a single ``select.select`` call):

(a) ``select.select`` is called BEFORE ``sendall`` — the write-readiness
    gate runs first so a stalled renderer can't block the worker thread.
(b) When ``select.select`` returns an empty writable list (timeout), the
    error is logged and the frame is dropped (client marked dead, pending
    entries re-merged — not silently lost).
(c) When ``select.select`` returns the socket as writable, ``sendall`` is
    called with the correct encoded JSON line.

These tests are complementary to ``tests/test_sender_select_timeout.py``
(which tests ``_await_socket_writable`` in isolation and the call-order
contract). This file focuses on the end-to-end ``_send`` behavior: the
exact data passed to ``sendall`` and the log/re-merge side effects on
timeout.
"""

from __future__ import annotations

import json
import logging
import threading
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.ipc import sender as sender_module
from voice_typer.server.ipc.rate_limiter import _TCP_WRITE_TIMEOUT_SECONDS
from voice_typer.server.ipc_server import IPCServer


def _make_server() -> IPCServer:
    """Build a minimal IPCServer fixture for testing ``_send`` in isolation.

    Uses ``__new__`` to skip the full ``__init__`` (which would spawn
    threads / bind sockets). Sets just the attributes ``_send`` touches.
    """
    server = IPCServer.__new__(IPCServer)
    server.app = MagicMock()
    server.app._shutting_down = False
    server._lock = threading.RLock()
    server._tcp_write_lock = threading.RLock()
    server._pending_tcp = []
    server._tcp_mode = True
    server._cached_shutting_down = False
    server._tcp_client = None
    return server


def _make_buffered_mock_client() -> tuple[MagicMock, list[bytes]]:
    """Mock tcp_client simulating ``_TCPLineIO`` buffer-then-flush.

    ``write()`` appends to an in-memory buffer; ``flush()`` issues a
    single ``sendall`` for the whole buffer (mirrors the real
    ``_TCPLineIO`` behavior so we can inspect the exact bytes passed to
    ``sendall`` without a real socketpair).

    Returns ``(tcp_client, write_buffer)`` so the test can inspect the
    buffered bytes.
    """
    tcp_client = MagicMock()
    tcp_client.conn = MagicMock()
    write_buffer: list[bytes] = []

    def mock_write(text: str | bytes) -> None:
        # The real ``_TCPLineIO.write`` accepts BOTH ``str`` and
        # pre-encoded ``bytes`` (the sender's ``line_bytes`` fast path
        # passes bytes and skips the re-encode). Mirror that contract.
        write_buffer.append(text.encode("utf-8") if isinstance(text, str) else text)

    def mock_flush() -> None:
        if write_buffer:
            tcp_client.conn.sendall(b"".join(write_buffer))
            write_buffer.clear()

    def mock_reset() -> None:
        write_buffer.clear()

    tcp_client.write.side_effect = mock_write
    tcp_client.flush.side_effect = mock_flush
    tcp_client._reset_write_buffer.side_effect = mock_reset
    return tcp_client, write_buffer


def _patch_select_writable(conn: object) -> patch:
    """Patch ``sender.select`` so ``select.select`` reports *conn* as
    writable (the happy path). ``select.poll`` is configured similarly
    but is not reached when ``select.select`` returns writable."""
    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4
    mock_mod.select.return_value = ([conn], [], [])
    return patch.object(sender_module, "select", mock_mod)


def _patch_select_not_writable() -> tuple[patch, MagicMock]:
    """Patch ``sender.select`` so BOTH ``select.select`` and
    ``select.poll`` report the socket as NOT writable (timeout path)."""
    mock_mod = MagicMock()
    mock_mod.POLLOUT = 4
    mock_mod.select.return_value = ([], [], [])
    mock_poller = MagicMock()
    mock_poller.poll.return_value = []
    mock_mod.poll.return_value = mock_poller
    return patch.object(sender_module, "select", mock_mod), mock_mod


# ── (a) select.select is called BEFORE sendall ────────────────────────


def test_select_called_before_sendall() -> None:
    """``_send`` must call ``select.select([], [conn], [], timeout)``
    BEFORE ``sendall``. The select establishes write-readiness so the
    subsequent ``sendall`` won't block indefinitely on a stalled
    renderer."""
    server = _make_server()
    tcp_client, _buf = _make_buffered_mock_client()
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

    # select.select was called with the expected args.
    assert len(select_calls) >= 1, "select.select must be called before sendall"
    rlist, wlist, xlist, timeout = select_calls[0]
    assert rlist == [], "select.select readable list must be empty"
    assert wlist == [tcp_client.conn], "select.select writable list must contain the tcp client's socket"
    assert xlist == [], "select.select exceptional list must be empty"
    assert timeout == _TCP_WRITE_TIMEOUT_SECONDS, (
        f"select.select timeout must be _TCP_WRITE_TIMEOUT_SECONDS ({_TCP_WRITE_TIMEOUT_SECONDS}), got {timeout}"
    )

    # select must be called BEFORE sendall.
    assert "select" in call_order, "select.select was not called"
    assert "sendall" in call_order, "sendall was not called"
    assert call_order.index("select") < call_order.index("sendall"), (
        f"select.select must be called BEFORE sendall; got order {call_order}"
    )


# ── (b) select returns empty → error logged + frame dropped ───────────


def test_select_timeout_logs_error_and_drops_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``select.select`` returns an empty writable list (the socket
    is not writable within the timeout), ``_send`` must:

    1. Log the write failure at DEBUG level (the existing convention for
       client-write failures — keeps the log clean under sustained
       disconnects).
    2. Drop the current frame (``sendall`` is NOT called).
    3. Mark the client as dead (``_tcp_client = None``) so the accept
       loop picks up the next reconnect.
    4. Re-merge any snapshotted pending entries into ``_pending_tcp`` so
       they survive for the next reconnect's drain (not silently lost).
    """
    server = _make_server()
    tcp_client, _buf = _make_buffered_mock_client()
    server._tcp_client = tcp_client
    # Pre-populate pending to verify the re-merge on write timeout.
    server._pending_tcp = ['{"old": 1}', '{"old": 2}']

    patch_obj, _mock_mod = _patch_select_not_writable()
    with patch_obj, caplog.at_level(logging.DEBUG, logger="voice_typer"):
        server._send({"type": "test_event", "id": 1})

    # sendall must NOT have been called — select said not writable.
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


# ── (c) select returns writable → sendall called with correct data ────


def test_select_writable_sendall_called_with_correct_data() -> None:
    """When ``select.select`` returns the socket as writable, ``_send``
    must call ``sendall`` with the correctly encoded JSON line (the
    message serialized via ``json.dumps`` + a trailing newline)."""
    server = _make_server()
    tcp_client, _buf = _make_buffered_mock_client()
    server._tcp_client = tcp_client

    sent_data: list[bytes] = []

    def capture_sendall(data: bytes) -> None:
        sent_data.append(data)

    tcp_client.conn.sendall.side_effect = capture_sendall

    msg = {"type": "test_event", "id": 42, "text": "hello"}
    # Production now uses compact JSON (no whitespace, ensure_ascii=False)
    # per XV-83 — see ``test_ipc_layer_fixes::TestCompactJsonSerialization``.
    expected_line = json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n"

    with _patch_select_writable(tcp_client.conn):
        server._send(msg)

    # sendall must have been called exactly once (no pending, no drain).
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
    """When ``select.select`` returns writable AND there are pending
    entries, ``_send`` must call ``sendall`` TWICE: once for the current
    line and once for the batched drain. The drain flush must also be
    preceded by a select call."""
    server = _make_server()
    tcp_client, _buf = _make_buffered_mock_client()
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
    # serialized at enqueue time, before the compact-JSON refactor
    # in the current-message path). The test pre-populates with
    # the legacy format (with default ``", "`` / ``": "``
    # whitespace) to match the production behavior: pending
    # entries are written verbatim, no re-encoding.
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

    # All pending drained — none re-merged.
    assert len(server._pending_tcp) == 0, f"Pending must be fully drained; got {server._pending_tcp!r}"

    # Client stays alive.
    assert server._tcp_client is tcp_client


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
