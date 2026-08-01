"""SU-19: TCP dispatch concurrency regression tests.

Verifies the fix for the head-of-line blocking bug where
``self._dispatch(msg)`` was called INLINE in the TCP read loop
(``voice_typer/server/ipc/transport_tcp.py::_handle_tcp_connection``).
A long-running handler (e.g. ``download_model``, up to 120s) blocked
the read loop from reading subsequent commands from the same Electron
client — every pending request waited in the kernel socket buffer
until the slow handler returned.

The fix offloads ``_dispatch`` to the existing ``_tcp_worker_pool``
(declared at ``transport_tcp.py`` startup, previously only used for
connection handling) via ``pool.submit(self._tcp_dispatch_and_respond,
msg, client)``. The read loop continues reading immediately. The
Electron client already correlates responses to requests out-of-order
via the ``id`` field (``sendToPython`` → ``pendingRequests.get(id)``),
so concurrent dispatch is safe even though responses may arrive in a
different order than requests.

This mirrors the WS path's ``run_in_executor`` pattern at
``voice_typer/server/sidecar_ws.py:572`` — adapted for the thread-based
TCP transport by wrapping the dispatch + response-send in a single
callable so the read loop can discard the Future.

Test strategy
-------------
1. Replace ``server._tcp_worker_pool`` with a real
   ``ThreadPoolExecutor`` (so dispatch actually runs concurrently) and
   ``server._dispatch`` with a test double that records start/end
   timestamps and sleeps for "slow" commands.
2. Drive ``_handle_tcp_connection`` with a real ``socket.socketpair``
   (same pattern as ``tests/test_heartbeat.py`` and
   ``tests/test_keyboard_ownership_watchdog.py``).
3. Send two commands back-to-back: a slow one (dispatch sleeps 1s)
   then a fast one (instant). Assert the fast dispatch STARTS before
   the slow dispatch FINISHES — proving the read loop did not block.
4. Verify the XE-2-1 heartbeat fast-path still bypasses ``_dispatch``
   (heartbeats are handled inline and are not delayed by an in-flight
   slow dispatch).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import pytest
from voice_typer.server.ipc_server import IPCServer

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service

_TEST_TOKEN = "su19-concurrency-test-token"


def _make_server_with_pool() -> IPCServer:
    """Build an ``IPCServer`` with a real ``ThreadPoolExecutor`` as the
    dispatch pool.

    The pool stands in for the production ``_tcp_worker_pool`` (which
    is normally created by ``start_tcp()``). Using a real pool — rather
    than a ``MagicMock`` — lets us verify that ``submit`` actually runs
    the dispatch concurrently, which is the whole point of the SU-19
    fix. A ``MagicMock`` whose ``submit`` is a no-op would not exercise
    the concurrency path at all.
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True
    server._tcp_worker_pool = ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="tcp-worker-test",
    )
    return server


def _drain_socket(sock: socket.socket, timeout: float = 0.4) -> bytes:
    """Best-effort drain of any pending data on ``sock``.

    Used to swallow the post-auth ``state_changed`` event (ERR-017)
    so subsequent reads return only the responses we care about.
    """
    sock.settimeout(timeout)
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            # Stop once we've consumed at least one newline-terminated
            # line (the state_changed event).
            if b"\n" in buf:
                break
    except (TimeoutError, OSError):
        pass
    return buf


def _send_line(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_lines(sock: socket.socket, timeout: float = 2.0, max_lines: int = 10) -> list[dict]:
    """Read up to ``max_lines`` newline-terminated JSON lines."""
    sock.settimeout(timeout)
    lines: list[dict] = []
    buf = b""
    deadline = time.time() + timeout
    while len(lines) < max_lines and time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except (TimeoutError, OSError):
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            if raw.strip():
                with suppress(json.JSONDecodeError, UnicodeDecodeError):
                    lines.append(json.loads(raw.decode("utf-8")))
    return lines


class TestTCPDispatchConcurrency:
    """SU-19: ``_dispatch`` must be offloaded to ``_tcp_worker_pool`` so
    the read loop continues reading while a slow handler runs."""

    def test_slow_dispatch_does_not_block_read_loop(self) -> None:
        """A slow dispatch must not block the read loop from dispatching
        subsequent messages.

        Sends a slow command (dispatch sleeps 1s) then a fast command
        (instant) back-to-back. Asserts the fast dispatch STARTS before
        the slow dispatch FINISHES — proving the read loop did not wait
        for the slow dispatch to complete before reading the next
        command.
        """
        server = _make_server_with_pool()
        events: list[dict] = []
        events_lock = threading.Lock()

        def record(event: str, msg_type: str) -> None:
            with events_lock:
                events.append(
                    {
                        "event": event,
                        "type": msg_type,
                        "ts": time.monotonic(),
                        "thread": threading.current_thread().name,
                    }
                )

        def fake_dispatch(msg: dict) -> dict | None:
            msg_type = msg.get("type") if isinstance(msg, dict) else None
            record("dispatch_start", msg_type)
            if msg_type == "slow_command":
                time.sleep(1.0)
            record("dispatch_end", msg_type)
            return {"type": "response", "data": {"echo": msg_type}, "id": msg.get("id")}

        server._dispatch = fake_dispatch  # type: ignore[method-assign]

        client_sock, server_sock = socket.socketpair()
        handler_thread = threading.Thread(
            target=server._handle_tcp_connection,
            args=(server_sock, ("127.0.0.1", 0), _TEST_TOKEN),
            daemon=True,
        )
        handler_thread.start()

        try:
            # Auth handshake.
            _send_line(client_sock, {"type": "auth", "token": _TEST_TOKEN})
            # Drain post-auth state_changed event ().
            _drain_socket(client_sock, timeout=0.4)

            # Send two commands back-to-back: slow then fast.
            _send_line(client_sock, {"type": "slow_command", "id": 1})
            _send_line(client_sock, {"type": "fast_command", "id": 2})

            # Wait for both dispatches to complete (slow sleeps 1s).
            deadline = time.time() + 5.0
            while time.time() < deadline:
                with events_lock:
                    starts = [e for e in events if e["event"] == "dispatch_start"]
                    ends = [e for e in events if e["event"] == "dispatch_end"]
                if len(starts) >= 2 and len(ends) >= 2:
                    break
                time.sleep(0.02)

            with events_lock:
                starts = [e for e in events if e["event"] == "dispatch_start"]
                ends = [e for e in events if e["event"] == "dispatch_end"]

            # (4) Both messages are eventually dispatched.
            assert len(starts) >= 2, (
                f"SU-19: expected both messages to be dispatched, but only got "
                f"{len(starts)} dispatch_start events: {events!r}"
            )
            assert len(ends) >= 2, (
                f"SU-19: expected both dispatches to complete, but only got {len(ends)} dispatch_end events: {events!r}"
            )

            slow_start = next(e for e in starts if e["type"] == "slow_command")
            slow_end_ev = next(e for e in ends if e["type"] == "slow_command")
            fast_start = next(e for e in starts if e["type"] == "fast_command")

            # (3) The second (fast) message is dispatched BEFORE the first
            # (slow) completes — proving non-blocking. If the read loop
            # blocked on the slow dispatch, fast_start would be >= slow_end.
            assert fast_start["ts"] < slow_end_ev["ts"], (
                f"SU-19 REGRESSION: the fast dispatch started at "
                f"{fast_start['ts']:.4f} but the slow dispatch did not finish "
                f"until {slow_end_ev['ts']:.4f}. This means the read loop "
                f"BLOCKED on the slow dispatch (head-of-line blocking) instead "
                f"of offloading it to the worker pool."
            )

            # (5) The read loop continued reading while dispatch was
            # in-flight. The fast dispatch should start within 0.5s of the
            # slow dispatch starting (not 1s+ later, which would indicate
            # the read loop waited for the slow dispatch to finish).
            gap = fast_start["ts"] - slow_start["ts"]
            assert gap < 0.5, (
                f"SU-19: the fast dispatch started {gap:.3f}s after the slow "
                f"dispatch started — the read loop waited for the slow dispatch "
                f"to finish before reading the next message (expected <0.5s "
                f"for non-blocking read loop)."
            )

            # Bonus: dispatch ran on a worker pool thread, not the
            # read-loop handler thread. This confirms the dispatch was
            # actually offloaded via ``pool.submit``.
            handler_thread_name = handler_thread.name
            for e in starts:
                assert e["thread"] != handler_thread_name, (
                    f"SU-19: dispatch for {e['type']!r} ran on the handler "
                    f"thread {e['thread']!r} (same as the read loop) — it was "
                    f"NOT offloaded to the worker pool."
                )
                assert "tcp-worker" in e["thread"], (
                    f"SU-19: dispatch ran on thread {e['thread']!r}, expected a 'tcp-worker' pool thread."
                )

            # Drain any responses so the socket buffer doesn't fill.
            _read_lines(client_sock, timeout=0.3, max_lines=4)
        finally:
            with suppress(OSError):
                client_sock.close()
            handler_thread.join(timeout=5.0)
            assert not handler_thread.is_alive(), "SU-19: TCP handler thread did not exit after client close."
            server._tcp_worker_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[union-attr]

    def test_heartbeat_fast_path_bypasses_dispatch(self) -> None:
        """XE-2-1: the heartbeat fast-path must still bypass ``_dispatch``.

        Sends a slow command (dispatch sleeps 1s), then a heartbeat
        while the slow dispatch is in-flight. The heartbeat_ack must
        arrive at the client BEFORE the slow command's response —
        proving the heartbeat was handled inline in the read loop and
        was not delayed by the in-flight slow dispatch (and was NOT
        queued behind it in the worker pool).
        """
        server = _make_server_with_pool()
        dispatch_call_count = {"n": 0}
        dispatch_call_lock = threading.Lock()

        def fake_dispatch(msg: dict) -> dict | None:
            with dispatch_call_lock:
                dispatch_call_count["n"] += 1
            msg_type = msg.get("type") if isinstance(msg, dict) else None
            if msg_type == "slow_command":
                time.sleep(1.0)
            return {"type": "response", "data": {"echo": msg_type}, "id": msg.get("id")}

        server._dispatch = fake_dispatch  # type: ignore[method-assign]

        client_sock, server_sock = socket.socketpair()
        handler_thread = threading.Thread(
            target=server._handle_tcp_connection,
            args=(server_sock, ("127.0.0.1", 0), _TEST_TOKEN),
            daemon=True,
        )
        handler_thread.start()

        try:
            # Auth handshake.
            _send_line(client_sock, {"type": "auth", "token": _TEST_TOKEN})
            # Drain post-auth state_changed event ().
            _drain_socket(client_sock, timeout=0.4)

            # Send a slow command (dispatch sleeps 1s), then immediately
            # send a heartbeat. The heartbeat must be acked inline,
            # BEFORE the slow dispatch finishes.
            _send_line(client_sock, {"type": "slow_command", "id": 1})
            # Give the dispatch a moment to start (so it's genuinely
            # in-flight when the heartbeat arrives). 50ms is well
            # within the 1s sleep.
            time.sleep(0.05)
            _send_line(client_sock, {"type": "heartbeat", "id": 2})

            # Read responses. The heartbeat_ack should arrive first
            # (within ~0.5s), well before the slow dispatch's 1s sleep
            # finishes.
            responses = _read_lines(client_sock, timeout=2.5, max_lines=5)

            # The FIRST response must be the heartbeat_ack (not the
            # slow_command response). If the read loop had blocked on
            # the slow dispatch, the heartbeat would not have been read
            # until after the slow dispatch finished (1s+), and the
            # heartbeat_ack would either time out or arrive after the
            # slow response.
            assert len(responses) >= 1, f"XE-2-1: expected at least one response (heartbeat_ack), got {responses!r}"
            first = responses[0]
            assert first.get("type") == "heartbeat_ack", (
                f"XE-2-1 REGRESSION: the first response was {first!r}, expected "
                f"heartbeat_ack. The heartbeat was not handled inline (it was "
                f"either queued behind the slow dispatch or delayed by it)."
            )
            assert first.get("id") == 2, f"XE-2-1: heartbeat_ack id mismatch: {first!r}"

            # The heartbeat must NOT have gone through ``_dispatch``.
            # ``_dispatch`` should have been called exactly once (for
            # the slow_command), not twice.
            with dispatch_call_lock:
                n = dispatch_call_count["n"]
            assert n == 1, (
                f"XE-2-1: ``_dispatch`` was called {n} times, expected exactly "
                f"1 (for slow_command only). The heartbeat must bypass "
                f"``_dispatch`` via the inline fast-path."
            )
        finally:
            with suppress(OSError):
                client_sock.close()
            handler_thread.join(timeout=5.0)
            assert not handler_thread.is_alive(), "XE-2-1: TCP handler thread did not exit after client close."
            server._tcp_worker_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[union-attr]

    def test_dispatch_exception_still_sends_error_response(self) -> None:
        """SU-19: the error-handling path must still work when dispatch
        is offloaded to the worker pool.

        The ERR-018 / B-6 / EC-FIX-2 error envelope
        (``{"type":"error","data":{"code":"server.internal_error",
        "message":"internal error"}, "id": <id>}``) must be sent from
        within ``_tcp_dispatch_and_respond`` when ``_dispatch`` raises.
        """
        server = _make_server_with_pool()

        def boom(msg: dict) -> dict | None:
            raise RuntimeError("simulated handler crash")

        server._dispatch = boom  # type: ignore[method-assign]

        client_sock, server_sock = socket.socketpair()
        handler_thread = threading.Thread(
            target=server._handle_tcp_connection,
            args=(server_sock, ("127.0.0.1", 0), _TEST_TOKEN),
            daemon=True,
        )
        handler_thread.start()

        try:
            _send_line(client_sock, {"type": "auth", "token": _TEST_TOKEN})
            _drain_socket(client_sock, timeout=0.4)

            _send_line(client_sock, {"type": "get_status", "id": 42})

            responses = _read_lines(client_sock, timeout=2.0, max_lines=3)
            assert len(responses) >= 1, f"SU-19: expected an error response for the raising dispatch, got {responses!r}"
            err = responses[0]
            assert err.get("type") == "error", f"SU-19: expected type='error', got {err!r}"
            assert err.get("id") == 42, f"SU-19: error response must preserve the request id, got {err!r}"
            assert err["data"]["code"] == "server.internal_error", (
                f"SU-19: error code must be 'server.internal_error', got {err!r}"
            )
            assert err["data"]["message"] == "internal error", (
                f"SU-19: error message must be 'internal error', got {err!r}"
            )
        finally:
            with suppress(OSError):
                client_sock.close()
            handler_thread.join(timeout=5.0)
            assert not handler_thread.is_alive()
            server._tcp_worker_pool.shutdown(wait=False, cancel_futures=True)  # type: ignore[union-attr]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
