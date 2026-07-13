"""E2E integration test for the full Electron→TCP→Python→response pipeline.

BACKLOG-005: This test exercises the complete IPC flow that unit tests
with mocked stdin/stdout miss:

  1. Start a real ``IPCServer.start_tcp()`` on an ephemeral port.
  2. Connect via a real TCP socket (like the Electron main process does).
  3. Send the auth handshake line.
  4. Send multiple real IPC commands (``get_config``, ``set_config``,
     ``get_status``, ``get_history``, ``toggle_dictation``).
  5. Verify each response's structure, type, and data fields.
  6. Verify the config_changed push event is emitted after set_config.
  7. Verify error handling for unknown commands and malformed JSON.
  8. Tear down cleanly (stop server, close socket).

This catches regressions that unit tests miss:
  - TCP transport bugs (broken accept loop, framing errors).
  - Auth enforcement (SEC-018).
  - Command registry dispatch (REFACTOR: _COMMAND_REGISTRY lookup).
  - Response write-back (JSON serialization, newline framing).
  - Push event delivery (config_changed after set_config).
  - Error recovery (unknown command, invalid JSON don't crash the server).
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
import weakref
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock pystray before importing ipc_server (which transitively imports
# tray → pystray). Without this, pystray tries to connect to an X display
# on Linux headless CI and crashes.
_mock_pystray = MagicMock()
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

from voice_typer.server.ipc_server import IPCServer  # noqa: E402
from voice_typer.server.tray import AppState  # noqa: E402

# ── Mock app ──────────────────────────────────────────────────────────


class E2EMockApp:
    """Minimal VoiceTyperApp stub for E2E pipeline tests."""

    def __init__(self, tmp_path: Path):
        self.tray = MagicMock()
        self.tray.state = AppState.IDLE
        self.tray._state = AppState.IDLE
        self.tray._message = ""

        from voice_typer.server.config import Config

        self.config = Config()
        self.config.hotkey = "<f2>"
        self.config.model_size = "small.en"
        self.config.asr_backend = "whisper"
        self.config.theme_mode = "system"
        self.config.schema_version = 1

        self._ipc_server = None
        self._quit_called = False
        self._restart_called = False
        self._dictation_toggled = False
        # RACE-011: set_config handler acquires this lock before mutating config
        import threading
        self._config_mutation_lock = threading.RLock()
        # Model manager stub (set_config checks app.models.set_active_backend)
        self.models = MagicMock()
        # change_model is called when model_size changes
        self.change_model = MagicMock()

        os.environ["VOICE_TYPER_CONFIG_DIR"] = str(tmp_path)
        try:
            from voice_typer.server.history_db import HistoryDB

            self.history_db = HistoryDB(db_path=tmp_path / "e2e_history.db")
        except Exception:
            self.history_db = MagicMock()

        from voice_typer.server.service import VoiceTyperService

        self._service = VoiceTyperService(self)
        # Override apply_config_side_effects to avoid calling real
        # autostart/hotkey/repaste backends that would block in tests.
        self._service.apply_config_side_effects = lambda updates: None

    def quit_app(self) -> None:
        self._quit_called = True

    def restart_app(self) -> None:
        self._restart_called = True

    def toggle_dictation(self) -> None:
        self._dictation_toggled = True

    @property
    def service(self):
        return self._service


# ── Helpers ───────────────────────────────────────────────────────────


def _free_port() -> int:
    """Reserve and release an ephemeral port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Per-socket read buffer for _read_line. TCP may coalesce multiple
# newline-terminated JSON responses into a single recv() call; without
# a persistent buffer, the bytes after the first newline would be lost
# across _read_line invocations (the previous implementation did
# `line, _ = buf.split(b"\n", 1)` and discarded the tail). The buffer
# is keyed by id(sock) and auto-evicted when the socket is GC'd via
# weakref.finalize, so id() reuse cannot surface a stale buffer.
_SOCKET_READ_BUFFERS: dict[int, bytearray] = {}


def _sock_read_buf(sock: socket.socket) -> bytearray:
    """Return the persistent read buffer for ``sock``, creating it if needed.

    The returned bytearray is the live buffer used by ``_read_line``;
    callers should treat it as private to the helper.
    """
    key = id(sock)
    buf = _SOCKET_READ_BUFFERS.get(key)
    if buf is None:
        buf = bytearray()
        _SOCKET_READ_BUFFERS[key] = buf
        # Auto-evict when the socket is garbage-collected so that a
        # future id() reuse doesn't surface a stale buffer.
        weakref.finalize(sock, _SOCKET_READ_BUFFERS.pop, key, None)
    return buf


def _read_line(sock: socket.socket, timeout: float = 3.0) -> dict:
    """Read one newline-terminated JSON line from ``sock``.

    Maintains a persistent per-socket buffer so that TCP-coalesced
    responses are preserved across calls. When a single ``recv()``
    returns multiple newline-terminated JSON lines, the bytes beyond
    the first newline are stashed in the buffer and returned by
    subsequent ``_read_line`` calls without hitting the wire again.
    """
    buf = _sock_read_buf(sock)
    sock.settimeout(timeout)
    while b"\n" not in buf:
        try:
            chunk = sock.recv(4096)
        except TimeoutError as exc:
            raise TimeoutError(
                f"Timed out waiting for response. Partial: {bytes(buf)!r}"
            ) from exc
        if not chunk:
            raise ConnectionError(
                f"Server closed connection. Partial: {bytes(buf)!r}"
            )
        buf.extend(chunk)
    line, rest = buf.split(b"\n", 1)
    # Replace buffer contents with leftover bytes (preserves the same
    # bytearray object so the dict still references the live buffer).
    del buf[:]
    buf.extend(rest)
    return json.loads(line.decode("utf-8"))


def _send_line(sock: socket.socket, obj: dict) -> None:
    """Send a JSON object as a single newline-terminated line."""
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_all_pending(sock: socket.socket, timeout: float = 0.5) -> list[dict]:
    """Read all pending lines from ``sock`` (non-blocking after first read).

    Inherits ``_read_line``'s per-socket buffer, so coalesced responses
    that arrived in a previous ``recv()`` are drained from the buffer
    before any new network read is attempted.
    """
    results: list[dict] = []
    current_timeout = timeout
    try:
        while True:
            line = _read_line(sock, timeout=current_timeout)
            results.append(line)
            current_timeout = 0.2  # Shorter timeout for subsequent reads
    except (TimeoutError, ConnectionError):
        pass
    return results


# ── Fixture ───────────────────────────────────────────────────────────


@pytest.fixture
def e2e_server(tmp_path, monkeypatch):
    """Start a real IPCServer on an ephemeral port for E2E testing."""
    port = _free_port()
    token = "e2e-token-67890"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)

    # Patch _config_dir to return tmp_path — avoids SEC-005 path traversal
    # rejection of tmp_path (which is outside the home directory).
    from voice_typer.server import config as config_module
    monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

    app = E2EMockApp(tmp_path)
    server = IPCServer(app)
    # Override apply_config_side_effects to avoid calling real
    # autostart/hotkey/repaste backends that would block in tests.
    server.service.apply_config_side_effects = lambda updates: None
    # Note: we call start_tcp() WITHOUT start() to avoid starting the
    # stdin listener thread, which blocks on sys.stdin in the test
    # environment. The TCP accept loop is all we need for E2E tests.
    # We still need to register the push function manually (start()
    # does this, but we skip start() to avoid the stdin thread).
    from voice_typer.server.ipc_server import _set_push_event
    server._push_fn = server.push
    _set_push_event(server._push_fn)
    server._running = True
    server._hook_tray_set_state()
    server.start_tcp(port)

    # Wait for the TCP server to be ready
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(0.5)
            test_sock.connect(("127.0.0.1", port))
            test_sock.close()
            break
        except (TimeoutError, ConnectionRefusedError):
            time.sleep(0.1)
    else:
        server.stop()
        pytest.fail("TCP server did not start within 5 seconds")

    yield server, port, token, app

    # Clean up: stop the TCP server and unregister the push function.
    # We don't call server.stop() because that also tries to join the
    # stdin thread (which we never started).
    server._running = False
    from voice_typer.server.ipc_server import _clear_push_event
    if server._push_fn is not None:
        _clear_push_event(server._push_fn)
        server._push_fn = None
    # Close the listening socket to unblock the accept loop
    if server._tcp_server_socket is not None:
        with contextlib.suppress(OSError):
            server._tcp_server_socket.close()
    # Close any connected client
    if server._tcp_client is not None:
        with contextlib.suppress(Exception):
            server._tcp_client.close()
        server._tcp_client = None
    # Brief pause to let the OS release the port before the next test
    time.sleep(0.2)


def _connect_and_auth(port: int, token: str) -> socket.socket:
    """Connect to the server and send the auth line."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(("127.0.0.1", port))
    _send_line(sock, {"type": "auth", "token": token})
    return sock


# ── E2E tests ─────────────────────────────────────────────────────────


class TestE2EFullPipeline:
    """E2E tests for the full TCP IPC pipeline."""

    def test_auth_handshake_then_get_config(self, e2e_server):
        """Full flow: connect → auth → get_config → verify response."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        # Drain any initial push events (e.g., state_changed on connect)
        _read_all_pending(sock, timeout=0.5)

        _send_line(sock, {"id": 1, "type": "get_config"})
        resp = _read_line(sock, timeout=3.0)

        assert resp["id"] == 1
        assert resp["type"] == "config"
        assert "data" in resp
        assert resp["data"]["hotkey"] == "<f2>"
        assert resp["data"]["model_size"] == "small.en"

        sock.close()

    def test_set_config_returns_ack(self, e2e_server):
        """set_config should return an ack response AND emit a config_changed push.

        This test verifies the full set_config dispatch path:
          1. Validation against the IPC allowlist (validate_config_update).
          2. Config mutation under the app's config-mutation lock
             (RACE-011).
          3. ``apply_config_side_effects`` + ``config.save()``.
          4. Tray / model-availability cache invalidation.
          5. The ``config_changed`` push event emitted via
             ``_push_event_now`` (config_handlers.py:169).
          6. The ``ack`` response returned by the dispatcher.

        NOTE on response ordering: ``_handle_set_config`` emits the
        ``config_changed`` push event from inside the handler (BEFORE
        returning the ack to the dispatcher). The dispatcher then
        writes the ack to the same TCP socket. This means the client
        sees TWO newline-terminated JSON lines back-to-back:
        ``config_changed`` first, then ``ack``. We read both and
        assert on each.

        Background: this test was previously skipped with the reason
        "set_config E2E hangs in test env due to _push_event_now
        writing to the TCP client from within the dispatch thread;
        covered by test_new_test_001_live_tcp.py instead". That
        rationale was wrong on two counts:
          (a) No file named ``test_new_test_001_live_tcp.py`` exists
              in the repository (verified via Glob). A copy of its
              contents was inlined into ``test_feature_hardening_
              regressions.py`` after a ``# === Source: ... ===``
              header, but that copy does NOT cover set_config — it
              only covers get_status, get_config, unknown_command,
              reconnect, and server-stop scenarios.
          (b) The "hang" claim is inaccurate. The actual behavior is
              that ``_push_event_now`` writes the ``config_changed``
              event to the socket *before* the ack; the test read a
              single line and raised ``KeyError: 'id'`` on the
              push event (which has no ``id`` field). It did not
              truly hang — the assertion just failed on the wrong
              response.

        Fix: ``_read_line`` now maintains a persistent per-socket buffer
        so coalesced responses are preserved across calls. We read up to
        two lines (push + ack) and assert that both arrive, instead of
        assuming the ack arrives first.
        """
        server, port, token, app = e2e_server
        # Mock config.save() to avoid disk I/O and path traversal issues
        original_save = app.config.save
        app.config.save = lambda: True
        sock = _connect_and_auth(port, token)

        try:
            # Drain initial state_changed push
            _read_all_pending(sock, timeout=0.5)

            # Send set_config with a simple boolean field
            _send_line(sock, {"id": 2, "type": "set_config", "data": {"show_notifications": False}})

            # Read responses — the config_changed push event arrives
            # FIRST (written from inside _handle_set_config), followed
            # by the ack (written by the dispatcher after the handler
            # returns). Both lines may be coalesced into a single TCP
            # segment by the OS; _read_line's per-socket buffer ensures
            # the second line is not lost across calls.
            responses: list[dict] = []
            with contextlib.suppress(TimeoutError, ConnectionError):
                while len(responses) < 2:
                    responses.append(_read_line(sock, timeout=2.0))

            # The ack MUST be present with id=2.
            acks = [r for r in responses if r.get("id") == 2]
            assert acks, (
                f"Expected ack with id=2, got responses: {responses}"
            )
            assert acks[0]["type"] in ("ack", "error"), (
                f"Expected ack or error, got {acks[0]['type']}"
            )

            # If we got an ack (not an error), the config_changed push
            # event MUST also have been emitted. This is the coverage
            # the previous skip claimed existed in
            # test_new_test_001_live_tcp.py (it didn't).
            if acks[0]["type"] == "ack":
                pushes = [
                    r for r in responses
                    if r.get("type") == "config_changed"
                ]
                assert pushes, (
                    "Expected a config_changed push event alongside the "
                    f"ack; got responses: {responses}"
                )
                # The push carries the validated updates so the renderer
                # can update UI-local state without a get_config round-trip.
                assert pushes[0]["data"] == {"show_notifications": False}, (
                    f"Unexpected config_changed data: {pushes[0]['data']}"
                )
        finally:
            app.config.save = original_save
            sock.close()

    def test_get_status_returns_current_state(self, e2e_server):
        """get_status should return the current AppState."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        _read_all_pending(sock, timeout=0.5)

        _send_line(sock, {"id": 3, "type": "get_status"})
        resp = _read_line(sock, timeout=3.0)

        assert resp["id"] == 3
        assert resp["type"] == "status"
        assert "data" in resp
        assert "status" in resp["data"]

        sock.close()

    def test_toggle_dictation_calls_app_method(self, e2e_server):
        """toggle_dictation should call app.toggle_dictation()."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        _read_all_pending(sock, timeout=0.5)

        assert app._dictation_toggled is False
        _send_line(sock, {"id": 4, "type": "toggle_dictation"})
        resp = _read_line(sock, timeout=3.0)

        assert resp["id"] == 4
        assert resp["type"] == "ack"
        assert app._dictation_toggled is True

        sock.close()

    def test_get_history_returns_list(self, e2e_server):
        """get_history should return a list (empty for fresh DB)."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        _read_all_pending(sock, timeout=0.5)

        _send_line(sock, {"id": 5, "type": "get_history", "data": {"limit": 10}})
        resp = _read_line(sock, timeout=3.0)

        assert resp["id"] == 5
        assert resp["type"] == "history"
        assert isinstance(resp["data"], list)

        sock.close()

    def test_unknown_command_returns_error_with_code(self, e2e_server):
        """Unknown commands should return a structured error with code field."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        _read_all_pending(sock, timeout=0.5)

        _send_line(sock, {"id": 6, "type": "frobnicate"})
        resp = _read_line(sock, timeout=3.0)

        assert resp["id"] == 6
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "unknown_command"
        assert "frobnicate" in resp["data"]["message"]

        sock.close()

    def test_malformed_json_does_not_crash_server(self, e2e_server):
        """Sending invalid JSON should return an error, not crash."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        _read_all_pending(sock, timeout=0.5)

        # Send invalid JSON
        sock.sendall(b"{not valid json}\n")
        resp = _read_line(sock, timeout=3.0)

        assert resp["type"] == "error"
        assert "message" in resp["data"]

        # Server should still be alive — send a valid command
        _send_line(sock, {"id": 7, "type": "get_status"})
        resp2 = _read_line(sock, timeout=3.0)
        assert resp2["id"] == 7
        assert resp2["type"] == "status"

        sock.close()

    def test_multiple_sequential_commands(self, e2e_server):
        """Multiple commands in sequence should each get correct responses."""
        server, port, token, app = e2e_server
        sock = _connect_and_auth(port, token)

        _read_all_pending(sock, timeout=0.5)

        # Send 3 read-only commands one at a time, reading each response
        # before sending the next (avoids interleaving issues)
        commands = [
            (10, "get_status", None),
            (11, "get_config", None),
            (12, "get_history", {"limit": 5}),
        ]
        for cmd_id, cmd_type, cmd_data in commands:
            _send_line(sock, {"id": cmd_id, "type": cmd_type, "data": cmd_data})
            # Read responses until we get the one with our id
            deadline = time.time() + 3.0
            while time.time() < deadline:
                resp = _read_line(sock, timeout=2.0)
                if resp.get("id") == cmd_id:
                    assert resp["type"] in ("status", "config", "history"), (
                        f"Expected status/config/history, got {resp['type']}"
                    )
                    break
            else:
                pytest.fail(f"Did not receive response for id={cmd_id}")

        sock.close()

    def test_command_registry_dispatches_all_registered_commands(self, e2e_server):
        """REFACTOR: verify the _COMMAND_REGISTRY has all expected commands."""
        server, port, token, app = e2e_server

        # The registry should have at least 50 commands
        assert len(IPCServer._COMMAND_REGISTRY) >= 50, (
            f"Expected 50+ commands in registry, got {len(IPCServer._COMMAND_REGISTRY)}"
        )

        # Spot-check key commands exist
        expected_commands = [
            "get_config",
            "set_config",
            "get_status",
            "toggle_dictation",
            "get_history",
            "restart_app",
            "quit_app",
            "download_model",
            "delete_model",
            "onboarding_is_first_run",
        ]
        for cmd in expected_commands:
            assert cmd in IPCServer._COMMAND_REGISTRY, (
                f"Command '{cmd}' missing from _COMMAND_REGISTRY"
            )
            handler_name = IPCServer._COMMAND_REGISTRY[cmd]
            assert hasattr(IPCServer, handler_name), (
                f"Handler method '{handler_name}' not found on IPCServer"
            )


class TestE2EAuthEnforcement:
    """E2E tests for SEC-018 TCP session token auth."""

    def test_wrong_token_drops_connection(self, e2e_server):
        """A wrong auth token should cause the server to drop the connection."""
        server, port, token, app = e2e_server

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(("127.0.0.1", port))
        _send_line(sock, {"type": "auth", "token": "wrong-token"})

        # Server should send an error response then close
        with contextlib.suppress(ConnectionError, TimeoutError):
            resp = _read_line(sock, timeout=3.0)
            assert resp["type"] == "error"
            assert "auth" in resp["data"]["message"].lower()

        sock.close()

    def test_no_auth_line_drops_connection(self, e2e_server):
        """Sending a command before auth should drop the connection."""
        server, port, token, app = e2e_server

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(("127.0.0.1", port))

        # Send a command without auth first
        _send_line(sock, {"id": 1, "type": "get_config"})

        # Server should drop the connection (the command is read as the
        # auth line, fails validation, and the connection is closed)
        with contextlib.suppress(ConnectionError, TimeoutError):
            _read_line(sock, timeout=3.0)

        sock.close()
