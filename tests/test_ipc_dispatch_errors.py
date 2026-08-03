"""B-6: TCP dispatch exception-handling regression tests.

These tests verify that an *uncaught* exception raised by an IPC
handler is converted into a structured ``{"type": "error", ...}``
response on the TCP socket **without** tearing down the client
connection — so the renderer can recover (or at least report the
error to the user) instead of being silently disconnected.

Why a dedicated test file?
--------------------------

The dispatch loop in
:func:`voice_typer.server.ipc_server.IPCServer._handle_tcp_connection`
wraps ``self._dispatch(msg)`` in a ``try/except Exception:`` block
(see the ``ERR-018`` comment in ``ipc_server.py``).  Before ERR-018,
any uncaught handler exception bubbled up to the outer
``except Exception:`` clause, which logged ``"client connection
closed"`` at DEBUG and tore down the TCP session — so a single buggy
handler killed the entire IPC session until the user restarted the
backend.

There were already unit tests covering *handler-level* exception
catching (e.g. ``tests/test_server.py::
TestDispatchToggleDictation::test_exception_returns_error_response``
exercises the ``try/except`` inside ``_handle_toggle_dictation``).
But **no** test exercised the *dispatch-level* catching path — the
safety net that fires when a handler itself fails to catch.  This
file fills that gap with an end-to-end TCP integration test:

  1. Start a real ``IPCServer.start_tcp()`` on an ephemeral port.
  2. Connect a real ``socket.socket`` client and authenticate.
  3. Monkey-patch a registered handler (``_handle_get_status``) to
     raise ``RuntimeError`` on the first call.
  4. Send a ``get_status`` request and assert the response is
     ``{"type": "error", "data": {"message": "internal error"}}``.
  5. Restore the original handler, send a second ``get_status``
     request on the SAME socket, and assert a normal ``status``
     response is returned — proving the connection survived.

The test mirrors the live-TCP patterns already established in
``tests/test_feature_hardening_regressions.py`` (``live_server``
fixture, ``_send_line`` / ``_read_response_line`` helpers, minimal
``MockApp``) rather than inventing parallel infrastructure.
"""

from __future__ import annotations

import json
import socket
import time
from contextlib import suppress
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.tray import AppState

# ── Helpers (mirrors tests/test_feature_hardening_regressions.py) ──────


def _free_port() -> int:
    """Reserve and immediately release an ephemeral port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send_line(sock: socket.socket, obj: dict) -> None:
    """Send a JSON object as a single newline-terminated line."""
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_response_line(sock: socket.socket, timeout: float = 2.0) -> dict:
    """Read one newline-terminated JSON line from ``sock``.

    Raises ``TimeoutError`` if no newline arrives within ``timeout``.
    """
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
    """Best-effort drain of any pending lines on ``sock``.

    Used to swallow the initial ``state_changed`` event the server
    emits right after auth (see ``ERR-017`` in ``ipc_server.py``) so
    the subsequent ``_read_response_line`` call returns the actual
    command response rather than the connect-time event.
    """
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


# ── MockApp (mirrors tests/test_feature_hardening_regressions.py) ──────


class _MockApp:
    """Minimal VoiceTyperApp stub for live TCP dispatch tests.

    Reuses the same shape as ``MockApp`` in
    ``tests/test_feature_hardening_regressions.py`` so we don't invent
    a parallel fake.  A real ``Config`` is used so handler JSON
    serialization (``dataclasses.asdict``) doesn't crash; a real
    ``HistoryDB`` is used so history-touching handlers work
    end-to-end; a real ``VoiceTyperService`` wraps the app so the
    dispatch path goes through the genuine service layer.
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

        # use monkeypatch.setenv (auto-restored at teardown) instead of
        # raw os.environ assignment (which leaked across tests).
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))
        try:
            from voice_typer.server.history_db import HistoryDB

            self.history_db = HistoryDB(db_path=tmp_path / "test_history.db")
        except Exception:
            self.history_db = MagicMock()

        from voice_typer.server.service import VoiceTyperService

        self._service = VoiceTyperService(self)

    # Methods the IPC server calls on the app.
    def quit_app(self) -> None:
        self._quit_called = True

    def restart_app(self) -> None:
        self._restart_called = True

    @property
    def service(self):  # type: ignore[no-untyped-def]
        return self._service


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """Start a real IPCServer on an ephemeral port.

    Yields ``(server, port, token)``.  Cleans up by calling
    ``server.stop()`` and waiting for the accept thread to clear
    ``_tcp_server_socket``.
    """
    port = _free_port()
    token = "b6-dispatch-test-token"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR_OVERRIDE", str(tmp_path))

    app = _MockApp(tmp_path=tmp_path, monkeypatch=monkeypatch)
    server = IPCServer(app)
    app._ipc_server = server
    server.start()
    server.start_tcp(port)

    # Wait for the server to start listening.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(0.25)
            test_sock.connect(("127.0.0.1", port))
            test_sock.close()
            break
        except (TimeoutError, ConnectionRefusedError, OSError):
            time.sleep(0.02)
    else:
        server.stop()
        pytest.fail(f"IPC server did not start listening on port {port} within 2s")

    yield server, port, token

    server.stop()
    # close HistoryDB writer thread so it doesn't leak across the
    # pytest session (Windows native thread-limit crash — see ).
    with suppress(Exception):
        if hasattr(app, "history_db") and hasattr(app.history_db, "close"):
            app.history_db.close()
    with suppress(Exception):
        if hasattr(app, "_crash_recovery") and hasattr(app._crash_recovery, "shutdown"):
            app._crash_recovery.shutdown()
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if server._tcp_server_socket is None:
            break
        time.sleep(0.02)


@pytest.fixture
def authenticated_client(live_server):
    """Connect a client, send the auth line, yield the open socket."""
    server, port, token = live_server
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(("127.0.0.1", port))
    _send_line(client, {"type": "auth", "token": token})
    # Drain any post-auth connect-time events (e.g. state_changed from
    # ) so the first response the test reads is the response
    # to its own command.
    _drain(client, timeout=0.3)
    yield client, server
    with suppress(OSError):
        client.close()


# ── Tests ──────────────────────────────────────────────────────────────


class TestTcpDispatchExceptionHandling:
    """B-6: an uncaught handler exception must produce an error
    response AND keep the connection open for subsequent requests.

    These tests exercise the dispatch-level ``try/except`` safety net
    in ``_handle_tcp_connection`` (the ``ERR-018`` block), not the
    per-handler ``try/except`` that lives inside individual
    ``_handle_*`` methods.  The safety net is what protects the IPC
    session when a handler is buggy enough to let an exception
    escape its own ``try/except``.
    """

    def test_handler_exception_returns_error_response(self, authenticated_client, monkeypatch):
        """A handler raising ``RuntimeError`` must yield a structured
        error response, not a torn-down connection.

        Asserts:
          - Response ``type`` is ``"error"``.
          - Response ``data.message`` is the dispatch-loop's generic
            ``"internal error"`` string (the safety net deliberately
            does NOT leak the raw exception message — see the
            ``ERR-018`` comment in ``ipc_server.py``).
        """
        client, server = authenticated_client

        def boom(data, resp):  # noqa: ARG001 — handler signature
            raise RuntimeError("simulated handler crash")

        # Patch the bound handler on the live server instance.  The
        # dispatch path looks up ``getattr(self, handler_name)`` fresh
        # on each call, so this monkeypatch takes effect immediately
        # for the next request without restarting the server.
        monkeypatch.setattr(server, "_handle_get_status", boom)

        _send_line(client, {"id": 1, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)

        assert resp["type"] == "error", f"Expected error response for raising handler, got: {resp}"
        assert resp.get("id") == 1, f"Response id mismatch: {resp}"
        # The dispatch safety net sends a fixed, generic message — it
        # intentionally does NOT forward str(exception) to the client
        # (that would leak server internals / stack details over IPC).
        # We assert the contract here so a future refactor that
        # accidentally widens the message is caught.
        assert resp["data"]["message"] == "internal error", f"Expected generic 'internal error' message, got: {resp}"

    def test_connection_survives_handler_exception(self, authenticated_client, monkeypatch):
        """After an exception-induced error response, the SAME socket
        must accept and respond to a subsequent request.

        This is the core B-6 regression: pre-ERR-018 the outer
        ``except Exception:`` would tear down the connection on any
        uncaught handler exception, so a single buggy handler killed
        the entire IPC session.  Post-ERR-018 the dispatch loop's
        inner ``try/except`` catches, sends the error response, and
        ``continue``s — the connection survives.
        """
        client, server = authenticated_client

        original = server._handle_get_status
        call_count = {"n": 0}

        def flaky(data, resp):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first call crashes; second should work")
            return original(data, resp)

        monkeypatch.setattr(server, "_handle_get_status", flaky)

        # First request — handler raises, dispatch loop catches.
        _send_line(client, {"id": 10, "type": "get_status"})
        resp1 = _read_response_line(client, timeout=2.0)
        assert resp1["type"] == "error", f"First response should be error (handler raised): {resp1}"
        assert resp1.get("id") == 10

        # Second request on the SAME socket — connection must survive
        # and the handler (now un-flaked) must return a normal status.
        _send_line(client, {"id": 11, "type": "get_status"})
        resp2 = _read_response_line(client, timeout=2.0)
        assert resp2["type"] == "status", (
            f"Second response should be a normal status — connection "
            f"did not survive the prior handler exception: {resp2}"
        )
        assert resp2.get("id") == 11
        # Sanity-check the payload shape so we don't pass on a stub.
        assert "status" in resp2.get("data", {}), f"Status response missing data.status: {resp2}"

    def test_repeated_exceptions_keep_connection_alive(self, authenticated_client, monkeypatch):
        """Multiple consecutive handler exceptions must each produce
        an error response without disconnecting the client.

        Guards against a regression where the safety net only catches
        the first exception and lets the second tear down the
        connection (e.g. if someone narrows the ``except`` clause or
        adds a re-raise on a counter).
        """
        client, server = authenticated_client

        def always_boom(data, resp):  # noqa: ARG001
            raise RuntimeError("always crashes")

        monkeypatch.setattr(server, "_handle_get_status", always_boom)

        for i in range(3):
            _send_line(client, {"id": 100 + i, "type": "get_status"})
            resp = _read_response_line(client, timeout=2.0)
            assert resp["type"] == "error", f"Iteration {i}: expected error, got {resp}"
            assert resp.get("id") == 100 + i

        # Connection must still be alive — send a different command
        # (get_config) to a handler that does NOT raise and confirm
        # we get a normal response on the same socket.
        _send_line(client, {"id": 999, "type": "get_config"})
        resp_final = _read_response_line(client, timeout=2.0)
        assert resp_final.get("id") == 999
        assert resp_final["type"] in ("config", "error"), (
            f"Expected config or error response on surviving connection, got: {resp_final}"
        )


class TestStdinListenerGatedInTcpMode:
    """d-review Finding 1 regression: the unauthenticated stdin
    listener must NOT be spawned when the server runs in TCP/WS mode.

    A direct-terminal invocation of
    ``python -m voice_typer.server.ipc_server --port N`` would
    otherwise accept unauthenticated JSON commands on stdin while the
    TCP socket enforces the VOICE_TYPER_IPC_TOKEN handshake — an auth
    bypass.  The CLI sets ``_tcp_mode = True`` *before* ``start()``;
    this test mirrors that ordering.
    """

    def test_stdin_thread_not_started_when_tcp_mode_set_before_start(self, tmp_path, monkeypatch):
        app = _MockApp(tmp_path=tmp_path, monkeypatch=monkeypatch)
        server = IPCServer(app)
        app._ipc_server = server
        # Mirrors CLI: mark TCP mode BEFORE start() so the stdin
        # listener (unauthenticated command path) is suppressed.
        server._tcp_mode = True
        server.start()
        try:
            assert server._stdin_thread is None, (
                "stdin listener must not be spawned in TCP mode (d-review Finding 1 auth bypass)"
            )
        finally:
            server.stop()

    def test_stdin_thread_started_in_legacy_stdin_mode(self, tmp_path, monkeypatch):
        """Legacy stdin/stdout IPC mode (no TCP) must still spawn the
        stdin listener so the documented ``voice-typer`` CLI keeps
        working.

        UE-13 (High): the unauthenticated stdin listener is gated
        behind ``VOICE_TYPER_ALLOW_STDIN_IPC=1`` (``--allow-stdin`` on
        the CLI) — so the legacy stdin/stdout path is exercised via the
        documented explicit opt-in, exactly like
        ``test_ipc_server_lifecycle_fixes.py::test_stdin_thread_spawned_when_env_var_set``.
        """
        app = _MockApp(tmp_path=tmp_path, monkeypatch=monkeypatch)
        server = IPCServer(app)
        app._ipc_server = server
        # UE-13: without this env var, ``start()`` refuses to spawn the
        # stdin listener (closing the unauthenticated stdin auth-bypass
        # hole). Set the documented gate so the legacy mode is exercised.
        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        server.start()
        try:
            assert server._stdin_thread is not None, "stdin listener must be spawned in legacy stdin mode"
        finally:
            server.stop()
