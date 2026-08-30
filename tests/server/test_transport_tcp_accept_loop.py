"""TCP accept-loop coverage tests.

The accept loop in ``ipc/transport_tcp.py:TCPTransportMixin._accept_tcp``
runs in a daemon thread and:

1. **Spawns a worker per connection** — each accepted connection is
   handed off to ``self._tcp_worker_pool.submit(...)`` so a slow/malicious
   client cannot block the accept loop.
2. **Respects the connection cap** — when the worker pool has been shut
   down (``pool.submit`` raises ``RuntimeError``), the new connection is
   closed and the accept loop breaks.
3. **Escalates write timeouts** — when ``select.select`` reports the
   socket as not writable for ``_TCP_WRITE_TIMEOUT_SECONDS``, the
   ``_await_socket_writable`` helper raises ``TimeoutError`` and ``_send``
   marks the client dead (closes the socket, sets ``_tcp_client = None``).

Platform: runs on Linux. The accept loop and write-timeout paths are
platform-agnostic (they use stdlib ``socket`` / ``select``).
"""

from __future__ import annotations

import contextlib
import inspect
import socket
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server.ipc import sender as sender_module  # noqa: E402
from voice_typer.server.ipc.rate_limiter import _TCP_WRITE_TIMEOUT_SECONDS  # noqa: E402
from voice_typer.server.ipc_server import IPCServer  # noqa: E402

from tests.fixtures.ipc_test_helpers import (  # noqa: E402
    make_bare_ipc_server,
    make_ipc_server_with_fakes,
)

# ─── Helpers ───────────────────────────────────────────────────────────


def _make_server(*, token: str = "test-token-AAAABBBB") -> IPCServer:
    """Build an IPCServer with the canonical fake app + service, ready
    for TCP tests."""
    server, _app, _service = make_ipc_server_with_fakes()
    server._running = True
    return server


# ─── 1. accept loop spawns a worker per connection ────────────────────


class TestAcceptLoopSpawnsWorkerPerConnection:
    """#1: ``_accept_tcp`` must hand each accepted connection to
    ``self._tcp_worker_pool.submit(...)`` so a slow client cannot block
    the accept loop."""

    def test_accept_loop_submits_each_connection_to_worker_pool(self, monkeypatch, caplog):
        """When a client connects, ``_accept_tcp`` must call
        ``pool.submit(self._run_tcp_handler_safely, conn, addr, token)``
        exactly once per accepted connection.

        Uses a real ``socket.socketpair`` as the listening socket (mocked
        ``server.accept`` to return the pre-connected pair) so we exercise
        the accept → submit path without a real TCP bind.
        """
        server = _make_server()

        # Create the worker pool manually (start_tcp would create it in a
        # background thread).
        from concurrent.futures import ThreadPoolExecutor

        server._tcp_worker_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tcp-worker-test")
        server._tcp_dispatch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tcp-dispatch-test")

        submit_calls: list[tuple] = []

        original_submit = server._tcp_worker_pool.submit

        def tracking_submit(*args, **kwargs):
            submit_calls.append((args, kwargs))
            # Return a completed future so the worker doesn't actually run
            # the handler (we only care about the submit call).
            fut = original_submit(*args, **kwargs)
            return fut

        server._tcp_worker_pool.submit = tracking_submit  # type: ignore[method-assign]

        # Create a fake accepted connection.
        client_sock, server_sock = socket.socketpair()

        # Mock the server socket so ``accept()`` returns our pre-connected
        # pair, then raises OSError on the next call (to break the loop).
        mock_server_sock = MagicMock()
        accept_call_count = [0]

        def mock_accept():
            accept_call_count[0] += 1
            if accept_call_count[0] == 1:
                return server_sock, ("127.0.0.1", 12345)
            raise OSError("accept loop stopped by test")

        mock_server_sock.accept.side_effect = mock_accept
        mock_server_sock.close = lambda: None
        server._tcp_server_socket = mock_server_sock

        # Set the token so the auth check doesn't refuse the connection.
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token-AAAABBBB")

        # Run _accept_tcp inline (not in a thread) so we can assert
        # synchronously. The OSError from mock_accept breaks the loop.
        with contextlib.suppress(Exception):
            server._accept_tcp(("9876", mock_server_sock))

        try:
            assert len(submit_calls) == 1, (
                f"_accept_tcp must call pool.submit exactly once per accepted "
                f"connection; got {len(submit_calls)} submit calls"
            )
            args, _kwargs = submit_calls[0]
            # The first positional arg is the handler method.
            assert args[0].__name__ == "_run_tcp_handler_safely", (
                f"pool.submit must be called with _run_tcp_handler_safely; got {args[0]}"
            )
            # The second positional arg is the connection socket.
            assert args[1] is server_sock, "pool.submit must be called with the accepted connection socket"
        finally:
            with contextlib.suppress(Exception):
                client_sock.close()
            with contextlib.suppress(Exception):
                server_sock.close()
            with contextlib.suppress(Exception):
                server._tcp_worker_pool.shutdown(wait=False, cancel_futures=True)
            with contextlib.suppress(Exception):
                server._tcp_dispatch_pool.shutdown(wait=False, cancel_futures=True)


# ─── 2. accept loop respects connection cap ───────────────────────────


class TestAcceptLoopRespectsConnectionCap:
    """#2: when the worker pool has been shut down (or is saturated
    and rejects new submissions), ``_accept_tcp`` must close the new
    connection and break the accept loop.

    In production, ``pool.submit`` raises ``RuntimeError`` when the pool
    has been shut down (via ``stop()``). The accept loop catches this and
    closes the just-accepted connection to avoid a socket leak.
    """

    def test_accept_loop_closes_connection_when_pool_rejects_submission(self, monkeypatch):
        """When ``pool.submit`` raises ``RuntimeError`` (pool shut down),
        the accepted connection must be closed and the accept loop must
        break."""
        server = _make_server()

        # Create a worker pool and immediately shut it down so submit
        # raises RuntimeError.
        from concurrent.futures import ThreadPoolExecutor

        server._tcp_worker_pool = ThreadPoolExecutor(max_workers=1)
        server._tcp_dispatch_pool = ThreadPoolExecutor(max_workers=1)
        server._tcp_worker_pool.shutdown(wait=False, cancel_futures=True)

        # Use a MagicMock for the accepted connection (real socket.socket
        # objects don't allow attribute assignment for close/settimeout
        # tracking). The accept loop only needs ``close`` and
        # ``settimeout`` on the accepted socket.
        mock_conn = MagicMock(spec=socket.socket)
        mock_conn.close = MagicMock()
        mock_conn.settimeout = MagicMock()

        # Mock the server socket.
        mock_server_sock = MagicMock()
        accept_call_count = [0]

        def mock_accept():
            accept_call_count[0] += 1
            if accept_call_count[0] == 1:
                return mock_conn, ("127.0.0.1", 54321)
            raise OSError("accept loop should have broken after RuntimeError")

        mock_server_sock.accept.side_effect = mock_accept
        mock_server_sock.close = MagicMock()
        server._tcp_server_socket = mock_server_sock

        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-token-AAAABBBB")

        # Run _accept_tcp. It should:
        # 1. Accept the connection.
        # 2. Try to submit to the pool → RuntimeError.
        # 3. Close the accepted connection.
        # 4. Break the loop.
        server._accept_tcp(("9876", mock_server_sock))

        try:
            assert accept_call_count[0] == 1, (
                "accept should be called exactly once (loop breaks after "
                f"RuntimeError); got {accept_call_count[0]} calls"
            )
            (
                mock_conn.close.assert_called(),
                (
                    "the accepted connection must be closed when pool.submit "
                    "raises RuntimeError (socket-leak prevention)"
                ),
            )
        finally:
            with contextlib.suppress(Exception):
                server._tcp_dispatch_pool.shutdown(wait=False, cancel_futures=True)


# ─── 3. write timeout escalation ──────────────────────────────────────


class TestWriteTimeoutEscalation:
    """#3: when the socket is not writable within
    ``_TCP_WRITE_TIMEOUT_SECONDS``, ``_send`` must escalate by:

    - Catching the ``TimeoutError`` raised by ``_await_socket_writable``.
    - Marking the client dead (``_tcp_client = None``).
    - Re-merging the pending snapshot into ``_pending_tcp``.

    This prevents a stalled renderer from blocking the worker thread
    indefinitely (authenticated-idle DoS mitigation).
    """

    def test_write_timeout_marks_client_dead_and_remerges_pending(self):
        """When ``select.select`` reports the socket as not writable,
        ``_send`` must:

        1. NOT call ``sendall`` (the write path is never reached).
        2. Mark ``_tcp_client = None`` (dead-client path).
        3. Re-merge the pending snapshot into ``_pending_tcp``.
        """
        # Canonical bare send-path fixture (same pattern as
        # test_sender_select_timeout.py).
        server = make_bare_ipc_server(send_path=True)

        # Mock tcp_client whose conn will be reported as not-writable.
        tcp_client = MagicMock()
        tcp_client.conn = MagicMock()
        server._tcp_client = tcp_client

        # Pre-populate pending to verify the re-merge.
        server._pending_tcp.append('{"pending": "old1"}')
        server._pending_tcp.append('{"pending": "old2"}')

        # Patch select so BOTH select.select and select.poll report the
        # socket as NOT writable (timeout path).
        mock_select_mod = MagicMock()
        mock_select_mod.select.return_value = ([], [], [])  # not writable
        mock_poller = MagicMock()
        mock_poller.poll.return_value = []  # not writable (cross-check)
        mock_select_mod.poll.return_value = mock_poller
        mock_select_mod.POLLOUT = 4

        with patch.object(sender_module, "select", mock_select_mod):
            # _send catches the TimeoutError internally — it does NOT
            # re-raise to the caller.
            server._send({"type": "test_event", "id": 1})

        # sendall must NOT have been called — select said not writable,
        # so the write path was never reached.
        tcp_client.conn.sendall.assert_not_called()

        # Client must be marked dead.
        assert server._tcp_client is None, (
            "_send must mark _tcp_client = None when the write timeout fires (dead-client escalation path)"
        )

        # The pending snapshot must be re-merged.
        assert len(server._pending_tcp) >= 2, (
            f"pending entries must be re-merged after the write timeout; got {len(server._pending_tcp)} entries"
        )
        assert '{"pending": "old1"}' in server._pending_tcp
        assert '{"pending": "old2"}' in server._pending_tcp

    def test_write_timeout_constant_is_bounded(self):
        """Sanity check: the write timeout must be in a sensible range
        (0.5–10s) so a stalled client is detected promptly without
        false-positive timeouts on a temporarily-congested loopback."""
        assert 0.5 <= _TCP_WRITE_TIMEOUT_SECONDS <= 10.0, (
            f"TCP write timeout must be in 0.5–10s; got {_TCP_WRITE_TIMEOUT_SECONDS}"
        )


# ─── 4. source-level pin: _accept_tcp uses pool.submit ───────────────


class TestAcceptLoopSourceContract:
    """Static source-level pins: ``_accept_tcp`` must use
    ``pool.submit(self._run_tcp_handler_safely, ...)`` for each accepted
    connection (not inline ``_handle_tcp_connection``)."""

    def test_accept_tcp_source_contains_pool_submit(self):
        """The source of ``_accept_tcp`` must call ``pool.submit`` with
        ``_run_tcp_handler_safely`` — this is the architectural pin that
        prevents a regression to inline handler execution (which would
        let a slow client block the accept loop)."""
        source = inspect.getsource(IPCServer._accept_tcp)
        assert "pool.submit(self._run_tcp_handler_safely" in source, (
            "_accept_tcp must hand each accepted connection to the worker "
            "pool via pool.submit(self._run_tcp_handler_safely, ...) — "
            "inline _handle_tcp_connection would block the accept loop."
        )

    def test_accept_tcp_source_handles_runtime_error_from_submit(self):
        """The source must catch ``RuntimeError`` from ``pool.submit`` and
        close the connection — this is the connection-rejection path when
        the pool is shut down."""
        source = inspect.getsource(IPCServer._accept_tcp)
        assert "except RuntimeError:" in source, (
            "_accept_tcp must catch RuntimeError from pool.submit (pool "
            "shut down) and close the connection to avoid a socket leak."
        )
        assert "conn.close()" in source, (
            "_accept_tcp must close the accepted connection when pool.submit raises RuntimeError."
        )


# ─── 5. Windows exclusive-bind guard (P1-1.4) ─────────────────────────


class TestAcceptTcpWindowsExclusiveBind:
    """P1-1.4 (Windows parity): ``_accept_tcp`` must NOT set
    ``SO_REUSEADDR`` on Windows (it has INVERSE semantics there — it lets
    a second socket FORCIBLY bind a port already in use, so a stale
    second backend could hijack port 9876 from the live backend and split
    Electron's TCP connections across two servers), and MUST set it on
    POSIX (where it skips TIME_WAIT rebinds). Mirrors the same guard in
    ``transport._pick_available_port``.
    """

    def test_accept_tcp_skips_so_reuseaddr_on_windows(self, monkeypatch):
        """On Windows, ``_accept_tcp`` must bind WITHOUT
        ``SO_REUSEADDR`` — the default exclusive-bind semantics make a
        second backend fail loudly with EADDRINUSE instead of silently
        stealing the live backend's port."""
        server = _make_server()

        from voice_typer.server.ipc import transport_tcp as tcp_mod

        created: list = []

        class FakeSocket:
            def __init__(self, *a, **k):
                self.options: list = []
                created.append(self)

            def setsockopt(self, level, opt, val):
                self.options.append((level, opt, val))

            def bind(self, addr):
                raise OSError(10048, "WSAEADDRINUSE simulated")

            def close(self):
                pass

        # Patch the socket class + os.name at the transport_tcp module
        # level so the bind path in _accept_tcp uses our fake.
        monkeypatch.setattr(tcp_mod.socket, "socket", FakeSocket)
        monkeypatch.setattr(tcp_mod.os, "name", "nt")

        with contextlib.suppress(Exception):
            server._accept_tcp(9876)

        assert created, "_accept_tcp must create a socket before binding"
        assert created[0].options == [], (
            "on Windows, _accept_tcp must NOT call setsockopt(SO_REUSEADDR) "
            "— exclusive bind is what prevents a second backend from "
            "hijacking port 9876 (P1-1.4)"
        )

    def test_accept_tcp_sets_so_reuseaddr_on_posix(self, monkeypatch):
        """On POSIX, ``_accept_tcp`` must keep setting ``SO_REUSEADDR``
        so a socket in TIME_WAIT can be rebound on the next launch."""
        server = _make_server()

        from voice_typer.server.ipc import transport_tcp as tcp_mod

        created: list = []

        class FakeSocket:
            def __init__(self, *a, **k):
                self.options: list = []
                created.append(self)

            def setsockopt(self, level, opt, val):
                self.options.append((level, opt, val))

            def bind(self, addr):
                raise OSError("bind failed (test)")

            def close(self):
                pass

        monkeypatch.setattr(tcp_mod.socket, "socket", FakeSocket)
        monkeypatch.setattr(tcp_mod.os, "name", "posix")

        with contextlib.suppress(Exception):
            server._accept_tcp(9876)

        assert created, "_accept_tcp must create a socket before binding"
        assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in created[0].options, (
            "on POSIX, _accept_tcp must set SO_REUSEADDR (skips TIME_WAIT rebinds) — P1-1.4 only removes it on Windows"
        )

    def test_accept_tcp_source_guards_so_reuseaddr_by_platform(self):
        """Source-level pin: the SO_REUSEADDR setsockopt must be gated
        on ``os.name != "nt"`` so a refactor can't silently reintroduce
        the unconditional Windows hijack vector."""
        source = inspect.getsource(IPCServer._accept_tcp)
        assert 'if os.name != "nt":' in source, (
            "_accept_tcp must gate SO_REUSEADDR on os.name != 'nt' (P1-1.4 "
            "Windows parity — unconditional SO_REUSEADDR lets a second "
            "backend hijack the port)"
        )
        assert "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)" in source, (
            "_accept_tcp must still set SO_REUSEADDR on POSIX"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
