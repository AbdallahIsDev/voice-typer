"""TCP idle-read timeout regression tests.

When a client authenticates but then never sends a command or heartbeat,
the server's dispatch loop must not block forever on ``readline()`` —
that would leak a worker-pool thread + file descriptor indefinitely
(authenticated-idle DoS). The dispatch-loop socket is configured with
an idle-read timeout sized to ``_HEARTBEAT_TIMEOUT_SECONDS +
_HEARTBEAT_INTERVAL_SECONDS``; when the timeout fires the connection is
closed cleanly.

These tests also pin the defensive ``conn.settimeout(10.0)`` applied in
``_accept_tcp`` before the worker-pool submit (caps FD-exhaustion DoS
exposure for queued connections).
"""

from __future__ import annotations

import contextlib
import inspect
import json
import socket
import threading
import time

import pytest
from voice_typer.server.ipc_server import IPCServer  # noqa: E402

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service  # noqa: E402


def test_accept_tcp_sets_defensive_timeout_before_submit() -> None:
    """Static check: ``_accept_tcp`` sets ``conn.settimeout(10.0)`` before
    submitting the connection to the worker pool.

    This pre-configures a bounded blocking budget on the accepted socket
    so a backlog of queued connections (worker pool saturated by a
    flood-connect attack) cannot hold worker threads indefinitely once
    they do start processing. Pin the architecture so a future refactor
    doesn't accidentally drop the defensive timeout.
    """
    source = inspect.getsource(IPCServer._accept_tcp)
    # Strip comment-only lines so explanatory text doesn't trip the check.
    code_lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0]
        code_lines.append(line)
    code_only = "\n".join(code_lines)

    assert "settimeout(10.0)" in code_only, (
        "_accept_tcp must call conn.settimeout(10.0) BEFORE pool.submit "
        "to cap FD-exhaustion DoS exposure for queued connections."
    )
    # The settimeout call must come BEFORE the pool.submit call.
    timeout_idx = code_only.find("settimeout(10.0)")
    submit_idx = code_only.find("pool.submit(")
    assert timeout_idx != -1 and submit_idx != -1, (
        "both settimeout(10.0) and pool.submit must be present in _accept_tcp"
    )
    assert timeout_idx < submit_idx, (
        "conn.settimeout(10.0) must come BEFORE pool.submit in _accept_tcp "
        "so the socket has a bounded blocking budget while queued."
    )


def test_handle_tcp_connection_sets_idle_read_timeout_after_auth() -> None:
    """Static check: ``_handle_tcp_connection`` configures an idle-read
    timeout (sized from the heartbeat constants) on the dispatch-loop
    socket after auth succeeds — instead of clearing the timeout
    entirely (``settimeout(None)``), which left the server vulnerable to
    an authenticated-idle DoS.
    """
    source = inspect.getsource(IPCServer._handle_tcp_connection)
    # The idle-read timeout must be derived from the heartbeat constants
    # so it tracks the watchdog contract (sized to tolerate healthy
    # heartbeat intervals while still bounding idle connections).
    assert "_HEARTBEAT_TIMEOUT_SECONDS" in source, (
        "_handle_tcp_connection must derive the idle-read timeout from "
        "_HEARTBEAT_TIMEOUT_SECONDS so it tracks the watchdog contract."
    )
    assert "_HEARTBEAT_INTERVAL_SECONDS" in source, (
        "_handle_tcp_connection must derive the idle-read timeout from "
        "_HEARTBEAT_INTERVAL_SECONDS so it tracks the watchdog contract."
    )
    # The dispatch loop must catch socket.timeout separately from the
    # generic OSError handler so idle-read timeouts are logged as a
    # warning (not swallowed at DEBUG like a routine disconnect).
    assert "except socket.timeout" in source, (
        "_handle_tcp_connection dispatch loop must catch socket.timeout "
        "separately from OSError so idle-read timeouts surface as a "
        "warning (authenticated-idle DoS mitigation)."
    )
    # The pre-fix ``conn.settimeout(None)`` that cleared the timeout
    # entirely must NOT be present in the CODE (it allowed authenticated-
    # idle DoS). We strip comments first because the explanatory comment
    # references the pre-fix behavior by name.
    code_only_lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0]
        code_only_lines.append(line)
    code_only = "\n".join(code_only_lines)
    assert "settimeout(None)" not in code_only, (
        "_handle_tcp_connection must NOT clear the socket timeout with "
        "settimeout(None) — that re-introduces the authenticated-idle DoS. "
        "Use the idle-read timeout instead."
    )


def test_idle_read_timeout_fires_for_authenticated_silent_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: an authenticated client that sends nothing is
    disconnected once the idle-read timeout fires.

    Uses a real ``socket.socketpair`` so we exercise the same dispatch
    path that production uses. The idle-read timeout is patched down to
    0.5s so the test runs in well under the 30s pytest timeout.
    """
    test_token = "idle-test-token-AAAABBBBCCCCDDDD"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", test_token)

    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True

    # Patch the heartbeat-constant module attributes so the idle-read
    # timeout computes to a short window. We patch the names imported
    # into transport_tcp's namespace.
    import voice_typer.server.ipc.transport_tcp as transport_tcp_mod

    original_timeout = transport_tcp_mod._HEARTBEAT_TIMEOUT_SECONDS
    original_interval = transport_tcp_mod._HEARTBEAT_INTERVAL_SECONDS
    transport_tcp_mod._HEARTBEAT_TIMEOUT_SECONDS = 0.3
    transport_tcp_mod._HEARTBEAT_INTERVAL_SECONDS = 0.2

    client_sock, server_sock = socket.socketpair()
    handler_done = threading.Event()

    def _run_handler() -> None:
        try:
            server._handle_tcp_connection(server_sock, ("127.0.0.1", 0), test_token)
        except Exception:
            pass
        finally:
            handler_done.set()

    handler_thread = threading.Thread(target=_run_handler, daemon=True)
    handler_thread.start()

    try:
        # Send the auth line so the handler enters the dispatch loop.
        client_sock.sendall((json.dumps({"type": "auth", "token": test_token}) + "\n").encode("utf-8"))

        # Drain the post-auth state_changed push event (best-effort).
        client_sock.settimeout(0.5)
        _drain = bytearray()
        try:
            while True:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                _drain.extend(chunk)
                if b"\n" in _drain:
                    break
        except (TimeoutError, OSError):
            pass

        # Send NOTHING further. Wait for the idle-read timeout to fire.
        # The handler should close the server-side socket, which the
        # client observes as EOF (recv returns b"").
        deadline = time.time() + 5.0
        closed = False
        client_sock.settimeout(0.5)
        while time.time() < deadline:
            try:
                chunk = client_sock.recv(4096)
                if chunk == b"":
                    closed = True
                    break
            except (TimeoutError, OSError):
                pass
            if handler_done.is_set():
                closed = True
                break
            time.sleep(0.05)

        assert closed, (
            "authenticated-but-silent client was not disconnected within "
            "5s — the idle-read timeout did not fire (authenticated-idle "
            "DoS regression)."
        )
    finally:
        transport_tcp_mod._HEARTBEAT_TIMEOUT_SECONDS = original_timeout
        transport_tcp_mod._HEARTBEAT_INTERVAL_SECONDS = original_interval
        with contextlib.suppress(OSError):
            client_sock.close()
        handler_thread.join(timeout=2.0)


def test_idle_read_timeout_does_not_fire_for_healthy_heartbeat_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a client that sends heartbeats within the idle-read
    window is NOT disconnected.

    This guards against a regression where the idle-read timeout is set
    too short (e.g. the 5s auth timeout leaking back in) and fires
    spuriously on healthy clients.
    """
    test_token = "healthy-heartbeat-token-AAAABBBB"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", test_token)

    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True

    # Patch the heartbeat constants so the idle-read timeout is ~1.0s
    # but the heartbeat interval is ~0.3s — a healthy client sending a
    # heartbeat every 0.3s must NOT trip the 1.0s idle timeout.
    import voice_typer.server.ipc.transport_tcp as transport_tcp_mod

    original_timeout = transport_tcp_mod._HEARTBEAT_TIMEOUT_SECONDS
    original_interval = transport_tcp_mod._HEARTBEAT_INTERVAL_SECONDS
    transport_tcp_mod._HEARTBEAT_TIMEOUT_SECONDS = 0.7
    transport_tcp_mod._HEARTBEAT_INTERVAL_SECONDS = 0.3

    client_sock, server_sock = socket.socketpair()

    def _run_handler() -> None:
        with contextlib.suppress(Exception):
            server._handle_tcp_connection(server_sock, ("127.0.0.1", 0), test_token)

    handler_thread = threading.Thread(target=_run_handler, daemon=True)
    handler_thread.start()

    try:
        # Auth.
        client_sock.sendall((json.dumps({"type": "auth", "token": test_token}) + "\n").encode("utf-8"))

        # Drain the post-auth state_changed push event.
        client_sock.settimeout(0.5)
        _drain = bytearray()
        try:
            while True:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                _drain.extend(chunk)
                if b"\n" in _drain:
                    break
        except (TimeoutError, OSError):
            pass

        # Send a heartbeat every 0.3s for ~2.0s (well past the 1.0s
        # idle-read timeout). The connection must stay open.
        end = time.time() + 2.0
        spurious_close = False
        while time.time() < end:
            client_sock.sendall(b'{"type":"heartbeat","id":1}\n')
            # Drain the heartbeat_ack so the kernel buffer doesn't fill.
            client_sock.settimeout(0.05)
            try:
                while True:
                    chunk = client_sock.recv(4096)
                    if chunk == b"":
                        spurious_close = True
                        break
            except (TimeoutError, OSError):
                pass
            if spurious_close:
                break
            time.sleep(0.3)

        assert not spurious_close, (
            "healthy heartbeat client was spuriously disconnected — the "
            "idle-read timeout fired despite regular heartbeats (regression)."
        )
    finally:
        transport_tcp_mod._HEARTBEAT_TIMEOUT_SECONDS = original_timeout
        transport_tcp_mod._HEARTBEAT_INTERVAL_SECONDS = original_interval
        with contextlib.suppress(OSError):
            client_sock.close()
        with contextlib.suppress(OSError):
            server_sock.close()
        handler_thread.join(timeout=2.0)


def test_handle_tcp_connection_no_dead_else_branch() -> None:
    """Static check: ``_handle_tcp_connection`` must NOT contain the
    dead ``else: auth_client = _TCPLineIO(conn)`` branch.

    Pre-fix the ``if expected_token:`` guard at the top of the auth
    block was always True (the ``not expected_token`` guard above
    returned early), making the matching ``else`` branch dead code.
    The dead branch has been removed and the auth body de-indented.
    """
    source = inspect.getsource(IPCServer._handle_tcp_connection)
    # The dead-branch pattern: a top-level ``else:`` immediately
    # followed by ``auth_client = _TCPLineIO(conn)``. After the fix
    # the auth body runs unconditionally (the early-return guard above
    # already handles the empty-token case).
    assert "else:\n            auth_client = _TCPLineIO(conn)" not in source, (
        "_handle_tcp_connection must not contain the dead else branch "
        "(the `if expected_token:` guard was always True because the "
        "`not expected_token` guard above returns early)."
    )
