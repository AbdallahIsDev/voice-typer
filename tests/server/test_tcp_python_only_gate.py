"""HU-2.3: the TCP (Electron renderer-facing) transport must reject
``_PYTHON_ONLY_COMMANDS`` (``shutdown``, ``tray_click``).

The command registry marks these commands as Python/host-only
(``voice_typer/server/ipc/registry.py``): they are intentionally absent
from the TS ``ALLOWED_COMMANDS`` allowlist, but the dispatcher registry
still maps them — so a COMPROMISED renderer that bypasses its own
allowlist could send ``shutdown`` (backend DoS) or ``tray_click``
(spoofed tray actions) over TCP and they would be executed.

The fix enforces the boundary at the transport:
``_handle_tcp_connection`` rejects both commands with a structured
``server.unknown_command`` envelope and never reaches ``_dispatch``.
The WS (Tauri host) path is deliberately NOT gated — the Rust host
legitimately sends both (ADR-0020 §6.5 / §16 / §10).

These tests drive the real ``_handle_tcp_connection`` with mock sockets
(same harness as ``tests/server/test_ipc_auth.py``) and assert:

- the python-only command yields an error envelope (not a result),
- ``server._dispatch`` is never invoked for it,
- the connection survives and a subsequent ``get_status`` still works.
"""

from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from voice_typer.server.ipc.validation import ErrorCodes

from tests.server.conftest import (  # noqa: F401
    IPCServer,
    server,
)


class _FakeSocket:
    """Minimal socket double (mirrors ``tests/server/test_ipc_auth.py``).

    Provides a text-mode reader over the canned input via ``makefile()``
    so the real ``_TCPLineIO`` reads the auth line + dispatch lines
    exactly as it would from a real socket, and captures every
    ``sendall()`` so the test can inspect the envelopes the server
    writes back.
    """

    def __init__(self, input_text: str = "") -> None:
        self._reader = io.StringIO(input_text)
        self.sent_chunks: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, t):
        self.timeouts.append(t)

    def setsockopt(self, *args, **kwargs):
        # No-op for IPPROTO_TCP / TCP_NODELAY — the handler wraps the
        # call in suppress(OSError, AttributeError) anyway.
        pass

    def makefile(self, mode="r", encoding=None, buffering=None):
        return self._reader

    def sendall(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.sent_chunks.append(data)

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True

    def sent_text(self) -> str:
        return b"".join(self.sent_chunks).decode("utf-8", errors="replace")


def _make_dispatch_pool() -> ThreadPoolExecutor:
    """Real dispatch pool (normally created by ``start_tcp``); the read
    loop's ``self._tcp_dispatch_pool.submit(...)`` needs a real executor
    so a legitimate command can actually dispatch."""
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-tcp-pyonly-dispatch")


@pytest.fixture
def pyonly_server(server, monkeypatch):
    """Server with a real dispatch pool + a recording ``_dispatch``.

    The recording wrapper delegates to the real dispatcher (so
    ``get_status`` still works against the fixture's mock app) while
    recording every message that reaches it — proving a rejected
    python-only command never reaches ``_dispatch``.
    """
    server._tcp_dispatch_pool = _make_dispatch_pool()
    real_dispatch = server._dispatch
    recorded: list[dict] = []

    def recording_dispatch(msg):
        recorded.append(msg)
        return real_dispatch(msg)

    monkeypatch.setattr(server, "_dispatch", recording_dispatch)
    server._dispatch_calls = recorded  # type: ignore[attr-defined]
    yield server
    server._tcp_dispatch_pool = None


def _envelopes(fake: _FakeSocket) -> list[dict]:
    return [json.loads(line) for line in fake.sent_text().splitlines() if line.strip()]


class TestTcpPythonOnlyGate:
    """HU-2.3: python-only commands are rejected on the renderer TCP path."""

    @pytest.mark.parametrize("cmd", ["shutdown", "tray_click"])
    def test_python_only_command_rejected_before_dispatch(self, pyonly_server, cmd):
        token = "pyonly-gate-token"
        auth_line = json.dumps({"type": "auth", "token": token}) + "\n"
        cmd_line = json.dumps({"type": cmd, "id": 7}) + "\n"
        fake = _FakeSocket(auth_line + cmd_line)

        pyonly_server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token=token)
        pyonly_server._tcp_dispatch_pool.shutdown(wait=True)

        # The connection survived the rejection (only EOF closed it).
        assert fake.closed, "handler should close the conn on EOF"

        # A structured error envelope was written back — NOT a result.
        errors = [e for e in _envelopes(fake) if e.get("type") == "error"]
        assert errors, f"{cmd} must be rejected with an error envelope; got {fake.sent_text()!r}"
        assert errors[-1]["data"]["code"] == ErrorCodes.UNKNOWN_COMMAND, (
            f"python-only command must surface as server.unknown_command; got {errors[-1]!r}"
        )

        # The command must NEVER reach the dispatcher.
        assert pyonly_server._dispatch_calls == [], (
            f"{cmd} must NOT reach _dispatch; recorded: {pyonly_server._dispatch_calls!r}"
        )

    def test_connection_survives_rejection_and_still_dispatches(self, pyonly_server):
        """After a python-only command is rejected, the connection stays
        alive and a subsequent legitimate command dispatches normally
        (the gate ``continue``s, it does not tear down the session)."""
        token = "pyonly-survive-token"
        auth_line = json.dumps({"type": "auth", "token": token}) + "\n"
        lines = json.dumps({"type": "shutdown", "id": 1}) + "\n" + json.dumps({"type": "get_status", "id": 2}) + "\n"
        fake = _FakeSocket(auth_line + lines)

        pyonly_server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token=token)
        pyonly_server._tcp_dispatch_pool.shutdown(wait=True)

        responses = _envelopes(fake)
        status = [r for r in responses if r.get("id") == 2]
        assert status, f"get_status (id=2) must still be served; got {fake.sent_text()!r}"
        assert status[0]["type"] == "status"

        # Only the legitimate command reached the dispatcher.
        assert [c.get("type") for c in pyonly_server._dispatch_calls] == ["get_status"]
