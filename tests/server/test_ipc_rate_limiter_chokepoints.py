"""rate limiter is enforced at each transport chokepoint, NOT in
``_dispatch``.

Background
----------

Pre-fix, the per-process ``_RateLimiter.allow()`` was called from BOTH:

  1. The transport chokepoint (TCP read loop in
     ``transport_tcp.py``; WS dispatch closure in
     ``sidecar_ws._make_dispatch``) — BEFORE ``server._dispatch(msg)``.
  2. ``DispatcherMixin._dispatch`` itself — at the top of the method
     body, before handler resolution.

So every TCP/WS dispatch charged the command cost against the
burst/sustained budget TWICE — once at the transport gate and again
inside ``_dispatch``. With ``burst=200``, a client sending 101
``download_model`` (cost 50 each) commands in 1 second would consume
``101 * 50 * 2 = 10100`` burst units against the 200 budget and be
throttled after ~2 commands instead of the intended ~4. The sustained
cap (600/10s) was likewise halved.

Fix
----------

The limiter call was REMOVED from ``DispatcherMixin._dispatch``. The
three transports now each own their own gate:

  * TCP — ``transport_tcp.py``'s ``_handle_tcp_connection`` read loop
    (the ``rate_limiter.allow(command=msg_type)`` gate, fires before
    ``self._tcp_dispatch_pool.submit(...)``).
  * WS — ``sidecar_ws._make_dispatch``'s closure body (the
    ``rate_limiter.allow(command=msg_type)`` gate, fires before
    ``loop.run_in_executor(ws_dispatch_pool, server._dispatch, msg)``).
  * Stdin — ``stdin_runner._run`` (the new
    ``_get_rate_limiter(self).allow(command=msg_type)`` gate, fires
    before ``self._dispatch(msg)``).

Pre-fix the stdin path had NO rate limiter at all ; the fix
ADDS the gate to stdin while REMOVING the double-call from TCP/WS.

These tests pin the three properties:

  (a) The stdin path now gates with the rate limiter — a rejected
      command emits the ``client.rate_limited`` envelope and does
      NOT reach ``_dispatch``.
  (b) ``DispatcherMixin._dispatch`` source no longer references the
      rate limiter (the double-call is gone).
  (c) The TCP and WS transport paths call the rate limiter exactly
      ONCE per dispatched command (not twice).
"""

from __future__ import annotations

import inspect
import io
import json
import socket
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.tray import AppState

from tests.fixtures.sidecar_ws_test_helpers import _make_fake_server
from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    IPCServer,
    server,
)

# ─────────────────────────────────────────────────────────────────────────
# (a) Stdin path now has the rate limiter gate
# ─────────────────────────────────────────────────────────────────────────


class TestStdinRateLimiterGate:
    """the stdin path (``stdin_runner._run``) now applies the
    per-process ``_RateLimiter`` BEFORE calling ``self._dispatch(msg)``.

    Pre-fix, the stdin path had NO rate limiter — a buggy/loopy stdin
    client could dispatch unbounded ``download_model`` /
    ``set_config`` / ``shutdown`` commands without ever being
    throttled. The TCP and WS paths already gated; stdin did not.
    """

    def test_stdin_rejects_when_rate_limiter_returns_false(self, server) -> None:
        """When the rate limiter rejects (``allow() -> False``), the
        stdin path must emit a ``client.rate_limited`` error envelope
        and MUST NOT call ``self._dispatch``."""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        # Force the limiter to be created on the server instance so we
        # can patch its ``allow`` method.
        limiter = _get_rate_limiter(server)
        # Replace ``_dispatch`` with a sentinel that asserts it was
        # NOT called — the rate-limiter gate must short-circuit before
        # dispatch.
        dispatch_called = []
        original_dispatch = server._dispatch

        def _should_not_be_called(msg):  # noqa: ARG001
            dispatch_called.append(msg)
            return original_dispatch(msg)

        server._dispatch = _should_not_be_called

        # Patch the limiter to always reject.
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        # The dispatch handler was NOT called (the gate short-circuited).
        assert dispatch_called == [], (
            "stdin path must NOT call _dispatch when the rate "
            "limiter rejects — the transport chokepoint must short-"
            "circuit before dispatch."
        )
        # The limiter was consulted exactly once (the stdin path's gate).
        limiter.allow.assert_called_once_with(command="get_status")
        # The error envelope was emitted on stdout.
        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "client.rate_limited"
        assert msg["data"]["message"] == "rate limit exceeded; backing off"

    def test_stdin_dispatches_when_rate_limiter_returns_true(self, server) -> None:
        """When the rate limiter accepts (``allow() -> True``), the
        stdin path must proceed to ``self._dispatch(msg)`` normally."""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        limiter = _get_rate_limiter(server)
        limiter.allow = MagicMock(return_value=True)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"get_status","id":7}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        # The limiter was consulted exactly once.
        limiter.allow.assert_called_once_with(command="get_status")
        # The dispatch response was emitted (NOT the rate_limited envelope).
        msg = json.loads(stdout.getvalue().strip())
        assert msg["id"] == 7
        assert msg["type"] == "status"

    def test_stdin_rate_limited_envelope_matches_tcp_ws_shape(self, server) -> None:
        """The stdin path's rate-limit envelope must match the TCP and
        WS paths' envelope shape (``client.rate_limited`` + the same
        message) so a client branching on ``code`` sees the same value
        across all three transports."""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        limiter = _get_rate_limiter(server)
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"download_model","id":99}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        msg = json.loads(stdout.getvalue().strip())
        # The envelope shape matches the TCP path's
        # (``transport_tcp.py``'s ``_send_error_envelope``) and the WS
        # path's (``sidecar_ws._make_dispatch``'s ``return {...}``).
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "client.rate_limited"
        assert msg["data"]["message"] == "rate limit exceeded; backing off"
        # The command cost was applied (download_model=50) — verify the
        # limiter saw the actual command name.
        limiter.allow.assert_called_once_with(command="download_model")

    def test_stdin_heartbeat_bypasses_rate_limiter(self, server) -> None:
        """The heartbeat command bypasses the rate limiter
        (``_RateLimiter.allow`` short-circuits to ``True`` for
        ``command == "heartbeat"``) so the heartbeat keep-alive is
        unaffected by the gate. This is the limiter's own behavior
        (not a stdin-path special-case) — pin it so a future change
        to the bypass doesn't silently break the stdin heartbeat path."""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        limiter = _get_rate_limiter(server)
        # Spy on ``allow`` without overriding its behavior.
        limiter.allow = MagicMock(wraps=limiter.allow)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"heartbeat","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        # The limiter was consulted (the stdin gate ran).
        limiter.allow.assert_called_once_with(command="heartbeat")
        # The heartbeat reached dispatch (the bypass returned True, so
        # the gate did not short-circuit). The heartbeat handler
        # returns a heartbeat_ack envelope.
        out = stdout.getvalue().strip()
        assert out, "expected the heartbeat to be dispatched (bypass returns True)"


# ─────────────────────────────────────────────────────────────────────────
# (b) ``_dispatch`` source no longer references the rate limiter
# ─────────────────────────────────────────────────────────────────────────


class TestDispatchDoesNotCallRateLimiter:
    """``DispatcherMixin._dispatch`` source must NOT contain
    a rate-limiter call. The limiter is enforced at the transport
    chokepoints (TCP / WS / stdin), NOT inside ``_dispatch``.

    Pre-fix, ``_dispatch`` called ``_get_rate_limiter(self).allow(...)``
    at the top of the method body — causing every TCP/WS dispatch to
    charge the command cost against the burst/sustained budget TWICE
    (once at the transport gate, once inside ``_dispatch``).
    """

    def test_dispatch_source_does_not_reference_rate_limiter(self) -> None:
        """The source of ``IPCServer._dispatch`` must not contain any
        CODE reference to ``_get_rate_limiter`` or ``rate_limiter.allow``
        (comments documenting the removal are allowed — only executable
        Python statements must not call the limiter)."""
        src = inspect.getsource(IPCServer._dispatch)
        # Strip comment-only lines (a line whose first non-whitespace
        # token is ``#``). The explanatory comment in the source
        # documents WHY the limiter was removed; the code-grep must
        # look only at actual Python statements.
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "_get_rate_limiter" not in code_only, (
            "_dispatch must NOT call _get_rate_limiter — the "
            "rate limiter is enforced at the transport chokepoints "
            "(TCP / WS / stdin), not inside _dispatch. The double-call "
            "halves the effective burst budget and trips the sustained "
            "cap in half the expected time."
        )
        assert "rate_limiter.allow" not in code_only, (
            "_dispatch must NOT call rate_limiter.allow — the limiter is enforced at the transport chokepoints."
        )
        assert "RATE_LIMITED" not in code_only, (
            "_dispatch must NOT reference the RATE_LIMITED error "
            "code — the rate-limit error envelope is constructed at the "
            "transport chokepoints, not inside _dispatch."
        )

    def test_dispatcher_module_does_not_import_get_rate_limiter(self) -> None:
        """The ``dispatcher`` module must not import ``_get_rate_limiter``
        — the import was needed only for the now-removed limiter call
        inside ``_dispatch``. A dangling import would be dead code and
        a footgun for a future contributor re-introducing the double-call."""
        from voice_typer.server.ipc import dispatcher

        # The module-level ``_get_rate_limiter`` symbol must not be
        # present (it was an explicit import removed by ).
        assert not hasattr(dispatcher, "_get_rate_limiter"), (
            "dispatcher module must NOT import _get_rate_limiter — "
            "the import is dead code after removing the limiter call "
            "from _dispatch."
        )

    def test_stdin_runner_module_imports_get_rate_limiter(self) -> None:
        """The ``stdin_runner`` module must import ``_get_rate_limiter``
        — the stdin path now owns the rate-limiter gate (previously it
        had no gate at all)."""
        from voice_typer.server.ipc import stdin_runner

        assert hasattr(stdin_runner, "_get_rate_limiter"), (
            "stdin_runner module must import _get_rate_limiter — "
            "the stdin path now owns the rate-limiter gate (previously "
            "it had no gate)."
        )

    def test_stdin_run_source_contains_rate_limiter_gate(self) -> None:
        """The source of ``IPCServer._run`` (the stdin runner) must
        contain the rate-limiter gate — the gate was ADDED by  to
        close the "stdin path has no limiter" gap."""
        src = inspect.getsource(IPCServer._run)
        assert "_get_rate_limiter(self).allow" in src, (
            "stdin runner (_run) must call "
            "_get_rate_limiter(self).allow(command=...) before "
            "self._dispatch(msg) — the stdin path now owns the "
            "rate-limiter gate."
        )
        assert "client.rate_limited" in src, (
            "stdin runner must emit the client.rate_limited "
            "error envelope when the limiter rejects — matching the "
            "TCP / WS path envelope shape."
        )


# ─────────────────────────────────────────────────────────────────────────
# (c) TCP / WS transport paths call the rate limiter exactly ONCE
# ─────────────────────────────────────────────────────────────────────────


class TestWsPathSingleRateLimiterCall:
    """the WS path (``sidecar_ws._make_dispatch``) calls the
    rate limiter exactly ONCE per dispatched command — at the transport
    chokepoint. Pre-fix, the WS path called the limiter at the
    chokepoint AND ``_dispatch`` called it again (double-call).
    """

    def test_ws_dispatch_calls_limiter_once_per_command(self) -> None:
        """A single WS dispatch must consult ``rate_limiter.allow``
        exactly once. The WS chokepoint calls it; ``_dispatch`` no
        longer does."""
        import asyncio

        from voice_typer.server import sidecar_ws as sw

        ws_server = _make_fake_server()
        # Force the limiter to be created on the server instance.
        from voice_typer.server.ipc_server import _get_rate_limiter

        limiter = _get_rate_limiter(ws_server)
        limiter.allow = MagicMock(return_value=True)  # type: ignore[method-assign]

        dispatch = sw._make_dispatch(ws_server)
        asyncio.run(dispatch({"type": "get_status", "data": {}, "id": 1}, MagicMock()))

        # The limiter was called exactly ONCE — the WS chokepoint gate.
        # Pre-fix it would have been called twice (chokepoint + _dispatch).
        assert limiter.allow.call_count == 1, (
            "WS path must call rate_limiter.allow exactly once "
            f"per dispatch (got {limiter.allow.call_count}). The "
            "double-call from _dispatch has been removed; the single "
            "call is the WS chokepoint gate."
        )
        limiter.allow.assert_called_once_with(command="get_status")

    def test_ws_dispatch_does_not_double_charge_on_reject(self) -> None:
        """When the WS chokepoint rejects, ``_dispatch`` is NOT called
        at all — so the limiter's ``allow`` is consulted exactly once
        (the chokepoint gate) and the command cost is charged exactly
        once (not twice)."""
        import asyncio

        from voice_typer.server import sidecar_ws as sw

        ws_server = _make_fake_server()
        ws_server._dispatch = MagicMock(return_value={"type": "result", "data": {}})

        from voice_typer.server.ipc_server import _get_rate_limiter

        limiter = _get_rate_limiter(ws_server)
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        dispatch = sw._make_dispatch(ws_server)
        result = asyncio.run(dispatch({"type": "get_status", "data": {}, "id": 1}, MagicMock()))

        # The limiter was consulted exactly once (the chokepoint gate).
        assert limiter.allow.call_count == 1, (
            f"WS path must call rate_limiter.allow exactly once even on rejection (got {limiter.allow.call_count})."
        )
        # ``_dispatch`` was NOT called (the chokepoint short-circuited).
        ws_server._dispatch.assert_not_called()
        # The error envelope was returned.
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.rate_limited"


# ─────────────────────────────────────────────────────────────────────────
# TCP path: live-server test for the single-call contract
# ─────────────────────────────────────────────────────────────────────────


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send_line(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_response_line(sock: socket.socket, timeout: float = 2.0) -> dict:
    sock.settimeout(timeout)
    buf = b""
    while b"\n" not in buf:
        try:
            chunk = sock.recv(4096)
        except TimeoutError as exc:
            raise TimeoutError(f"Timed out waiting for response. Got partial: {buf!r}") from exc
        if not chunk:
            raise ConnectionError(f"Server closed connection. Got partial: {buf!r}")
        buf += chunk
    line, _ = buf.split(b"\n", 1)
    return json.loads(line.decode("utf-8"))


def _drain(sock: socket.socket, timeout: float = 0.3) -> list[dict]:
    sock.settimeout(timeout)
    lines: list[dict] = []
    buf = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                if raw.strip():
                    with suppress(json.JSONDecodeError, UnicodeDecodeError):
                        lines.append(json.loads(raw.decode("utf-8")))
    except (TimeoutError, OSError):
        pass
    return lines


class _MockApp:
    """Minimal VoiceTyperApp stub for live TCP dispatch tests.

    Reuses the same shape as ``_MockApp`` in
    ``tests/test_ipc_error_envelope_parity.py``.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tray = MagicMock()
        self.tray.state = AppState.IDLE

        from voice_typer.server.config import Config

        self.config = Config()
        self.config.hotkey = "<f2>"
        self.config.repaste_hotkey = "<ctrl>+<alt>+v"
        self.config.recording_mode = "toggle"
        self.config.push_to_talk_hotkey = ""
        self.config.esc_cancel_enabled = True
        self.config.model_size = "small.en"
        self.config.asr_backend = "whisper"
        self.config.schema_version = 1
        self.config.theme_mode = "system"

        self._ipc_server: object | None = None
        self._quit_called = False
        self._restart_called = False

        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))
        try:
            from voice_typer.server.history_db import HistoryDB

            self.history_db = HistoryDB(db_path=tmp_path / "test_history.db")
        except Exception:
            self.history_db = MagicMock()

        from voice_typer.server.service import VoiceTyperService

        self._service = VoiceTyperService(self)

    def quit_app(self) -> None:
        self._quit_called = True

    def restart_app(self) -> None:
        self._restart_called = True

    @property
    def service(self):  # type: ignore[no-untyped-def]
        return self._service


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """Start a real IPCServer on an ephemeral port.

    Yields ``(server, port, token)``.
    """
    port = _free_port()
    token = "tcp-test-token"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))

    app = _MockApp(tmp_path=tmp_path, monkeypatch=monkeypatch)
    srv = IPCServer(app)
    app._ipc_server = srv
    srv.start()
    srv.start_tcp(port)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(0.25)
            test_sock.connect(("127.0.0.1", port))
            test_sock.close()
            break
        except (TimeoutError, ConnectionRefusedError, OSError):
            time.sleep(0.02)
    else:
        srv.stop()
        pytest.fail(f"IPC server did not start listening on port {port} within 2s")

    yield srv, port, token

    srv.stop()
    with suppress(Exception):
        if hasattr(app, "history_db") and hasattr(app.history_db, "close"):
            app.history_db.close()
    with suppress(Exception):
        if hasattr(app, "_crash_recovery") and hasattr(app._crash_recovery, "shutdown"):
            app._crash_recovery.shutdown()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if srv._tcp_server_socket is None:
            break
        time.sleep(0.02)


@pytest.fixture
def authenticated_client(live_server):
    """Connect a client, send the auth line, yield the open socket."""
    srv, port, token = live_server
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    _send_line(client, {"type": "auth", "token": token})
    _drain(client, timeout=0.3)
    yield client, srv
    with suppress(OSError):
        client.close()


class TestTcpPathSingleRateLimiterCall:
    """the TCP path (``transport_tcp._handle_tcp_connection``)
    calls the rate limiter exactly ONCE per dispatched command — at the
    transport chokepoint. Pre-fix, the TCP path called the limiter at
    the chokepoint AND ``_dispatch`` called it again (double-call).
    """

    def test_tcp_dispatch_calls_limiter_once_per_command(self, authenticated_client, monkeypatch) -> None:
        """A single TCP dispatch must consult ``rate_limiter.allow``
        exactly once. The TCP chokepoint calls it; ``_dispatch`` no
        longer does."""
        from voice_typer.server.ipc_server import _get_rate_limiter

        client, srv = authenticated_client
        limiter = _get_rate_limiter(srv)
        # Spy on ``allow`` without overriding its behavior (the real
        # limiter accepts the command).
        limiter.allow = MagicMock(wraps=limiter.allow)  # type: ignore[method-assign]

        _send_line(client, {"id": 42, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        # The dispatch succeeded (the limiter accepted).
        assert resp["id"] == 42
        assert resp["type"] == "status"
        # The limiter was called exactly ONCE — the TCP chokepoint gate.
        # Pre-fix it would have been called twice (chokepoint + _dispatch).
        assert limiter.allow.call_count == 1, (
            "TCP path must call rate_limiter.allow exactly once "
            f"per dispatch (got {limiter.allow.call_count}). The "
            "double-call from _dispatch has been removed; the single "
            "call is the TCP chokepoint gate."
        )
        limiter.allow.assert_called_once_with(command="get_status")

    def test_tcp_dispatch_reject_calls_limiter_once(self, authenticated_client, monkeypatch) -> None:
        """When the TCP chokepoint rejects, ``_dispatch`` is NOT called
        — the limiter is consulted exactly once (the chokepoint gate)
        and the command cost is charged exactly once."""
        from voice_typer.server.ipc_server import _get_rate_limiter

        client, srv = authenticated_client
        limiter = _get_rate_limiter(srv)
        # Force rejection.
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        _send_line(client, {"id": 7, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        # The error envelope was returned.
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.rate_limited"
        assert resp["data"]["message"] == "rate limit exceeded; backing off"
        # The limiter was consulted exactly once (the chokepoint gate).
        assert limiter.allow.call_count == 1, (
            f"TCP path must call rate_limiter.allow exactly once even on rejection (got {limiter.allow.call_count})."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
