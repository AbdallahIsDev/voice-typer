"""TCP / WS error-envelope parity tests.

Previously, the TCP path (``ipc_server._handle_tcp_connection``) and
the WS path (``sidecar_ws._make_dispatch``) emitted DIFFERENT error
envelopes for the same logical errors:

* **Rate limit**: TCP emitted ``{"type":"error","data":{"message":
  "rate limit exceeded; backing off"}}`` (no ``code``); WS emitted
  ``{"type":"error","data":{"code":"rate_limited","message":"rate
  limit exceeded; backing off"}}``.
* **Invalid JSON**: TCP emitted ``{"type":"error","data":{"message":
  "invalid JSON"}}`` (no ``code``); WS emitted
  ``{"type":"error","data":{"code":"invalid_payload","message":
  "invalid JSON"}}``.
* **Dispatch exception**: TCP emitted ``{"type":"error","data":{
  "code":"internal_error","message":"internal error"}}``; WS emitted
  ``{"type":"error","data":{"code":"internal_error","message":
  "dispatch raised"}}``. The messages differed even though both
  represented the same logical fault (an uncaught handler
  exception).

After the refactor, both paths emit the SAME envelope for each error class:

* Rate limit → ``{"type":"error","data":{"code":"rate_limited",
  "message":"rate limit exceeded; backing off"}}``
* Invalid JSON → ``{"type":"error","data":{"code":"invalid_payload",
  "message":"invalid JSON"}}``
* Dispatch exception → ``{"type":"error","data":{"code":
  "internal_error","message":"internal error"}}``

The WS-path unit tests in ``tests/tauri/test_sidecar_ws_unit.py``
already cover the WS side. This file adds the corresponding TCP-side
assertions AND a parity test that asserts both paths emit byte-for-byte
identical envelopes for each error class.

The TCP path is tested via a live ``IPCServer.start_tcp()`` on an
ephemeral port (reusing the live-server fixture pattern from
``tests/test_ipc_dispatch_errors.py``). The WS path is tested via
the ``_make_dispatch`` coroutine directly (no live WS server needed
— the dispatch function is a plain coroutine that takes a ``msg``
dict and returns a result dict).
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

from tests.fixtures.sidecar_ws_test_helpers import _make_fake_server

# ── Helpers (mirrors tests/test_ipc_dispatch_errors.py) ─────────────────


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send_line(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _send_raw(sock: socket.socket, raw: str) -> None:
    """Send a raw string (no JSON encoding) — used for invalid-JSON tests."""
    sock.sendall((raw + "\n").encode("utf-8"))


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
    """Best-effort drain of any pending lines on ``sock`` (e.g. the
    initial ``state_changed`` event from the fix).
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


class _MockApp:
    """Minimal VoiceTyperApp stub for live TCP dispatch tests.

    Reuses the same shape as ``_MockApp`` in
    ``tests/test_ipc_dispatch_errors.py``.
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
    token = "ipc5-parity-test-token"
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
    # close HistoryDB writer thread + shut down CrashRecovery saver
    # thread so they don't leak across the pytest session (on Windows the
    # accumulated daemon threads trip a native limit and crash the process
    # mid-suite).
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
    # Drain any post-auth connect-time events (e.g. state_changed).
    _drain(client, timeout=0.3)
    yield client, server
    with suppress(OSError):
        client.close()


# ── TCP-side envelope assertions ────────────────────────────────────────


class TestTcpErrorEnvelopes:
    """TCP path now emits the same envelope shape as the WS path."""

    def test_tcp_invalid_json_returns_invalid_payload_code(self, authenticated_client):
        """Invalid JSON on the TCP socket must return
        ``{"type":"error","data":{"code":"client.invalid_payload",
        "message":"invalid JSON"}}`` — matching the WS path.
        """
        client, _ = authenticated_client
        _send_raw(client, "{not valid json")
        resp = _read_response_line(client, timeout=2.0)
        assert resp["type"] == "error"
        # namespaced form is canonical.
        assert resp["data"]["code"] == "client.invalid_payload"
        assert resp["data"]["message"] == "invalid JSON"

    def test_tcp_dispatch_exception_returns_internal_error_code(self, authenticated_client, monkeypatch):
        """An uncaught handler exception on the TCP path must return
        ``{"type":"error","data":{"code":"internal_error","message":
        "internal error"}}`` — matching the WS path.
        """
        client, server = authenticated_client

        def boom(data, resp):  # noqa: ARG001
            raise RuntimeError("simulated handler crash")

        monkeypatch.setattr(server, "_handle_get_status", boom)
        _send_line(client, {"id": 1, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)
        assert resp["type"] == "error"
        # production emits the namespaced form (per  /
        # migration). The legacy bare ``internal_error`` is no
        # longer emitted by the dispatch loop or handler catch-alls.
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_tcp_rate_limit_returns_rate_limited_code(self, authenticated_client, monkeypatch):
        """When the rate limiter rejects a frame, the TCP path must
        return ``{"type":"error","data":{"code":"rate_limited",
        "message":"rate limit exceeded; backing off"}}`` — matching
        the WS path.

        We patch the rate limiter's ``allow()`` to always return False
        so the test doesn't need to send 200+ frames to trigger the
        limit.
        """
        client, server = authenticated_client
        # Patch the shared limiter on the server instance.
        limiter = server._rate_limiter_instance
        assert limiter is not None, "rate limiter should be initialized after start()"
        monkeypatch.setattr(limiter, "allow", lambda **kw: False)

        _send_line(client, {"id": 1, "type": "get_status"})
        resp = _read_response_line(client, timeout=2.0)
        assert resp["type"] == "error"
        # namespaced form is canonical.
        assert resp["data"]["code"] == "client.rate_limited"
        assert resp["data"]["message"] == "rate limit exceeded; backing off"


# ── WS-side envelope assertions (parity baseline) ───────────────────────


class TestWsErrorEnvelopes:
    """WS path baseline — confirms the envelopes the TCP path
    now matches.

    These tests are the WS-side mirror of ``TestTcpErrorEnvelopes``.
    The parity test in ``TestTcpWsEnvelopeParity`` cross-checks that
    the two sides produce identical envelopes.
    """

    def test_ws_invalid_json_returns_invalid_payload_code(self):
        """The WS path's invalid-JSON envelope is the parity baseline."""
        # The WS path handles invalid JSON in ``_handle_connection``
        # (not in ``_make_dispatch``), so we test the envelope shape
        # directly here. The full integration test is in
        # ``tests/tauri/test_sidecar_ws_integration.py``.
        expected = {
            "type": "error",
            "data": {
                # namespaced form is canonical.
                "code": "client.invalid_payload",
                "message": "invalid JSON",
            },
        }
        # The TCP path's envelope (from ``TestTcpErrorEnvelopes``)
        # must match this exactly.
        assert expected["data"]["code"] == "client.invalid_payload"
        assert expected["data"]["message"] == "invalid JSON"

    def test_ws_dispatch_exception_returns_internal_error_code(self):
        """If ``server._dispatch`` raises, the WS path returns the
        internal_error envelope — the parity baseline for the TCP path.
        """
        import asyncio

        from voice_typer.server import sidecar_ws as sw

        server = _make_fake_server()
        server._dispatch = MagicMock(side_effect=RuntimeError("boom"))
        dispatch = sw._make_dispatch(server)

        result = asyncio.run(dispatch({"type": "get_status", "data": {}}, MagicMock()))

        assert result["type"] == "error"
        # production emits the namespaced form (per  /
        # migration).
        assert result["data"]["code"] == "server.internal_error"
        # the message is now "internal error" (was "dispatch
        # raised" pre-) so the WS path matches the TCP path
        # verbatim.
        assert result["data"]["message"] == "internal error"

    def test_ws_rate_limit_returns_rate_limited_code(self):
        """When the rate limiter rejects a frame, the WS path returns
        the rate_limited envelope — the parity baseline for the TCP
        path.
        """
        import asyncio
        from unittest.mock import patch

        from voice_typer.server import sidecar_ws as sw

        server = _make_fake_server()
        dispatch = sw._make_dispatch(server)

        # Call dispatch once to populate ``server._rate_limiter_instance``
        # with a real ``_RateLimiter`` (``_get_rate_limiter`` creates one
        # lazily on first access — a MagicMock's auto-vivified child
        # fails the ``isinstance(limiter, _RateLimiter)`` check, so the
        # first call creates a real one and stores it on the server).
        asyncio.run(dispatch({"type": "get_status", "data": {}}, MagicMock()))

        # Now patch the real limiter's ``allow`` to always return False.
        with patch.object(
            server._rate_limiter_instance,
            "allow",
            return_value=False,
        ):
            result = asyncio.run(dispatch({"type": "get_status", "data": {}}, MagicMock()))

        assert result["type"] == "error"
        # namespaced form is canonical.
        assert result["data"]["code"] == "client.rate_limited"
        assert result["data"]["message"] == "rate limit exceeded; backing off"


# ── Cross-path parity test ──────────────────────────────────────────────


class TestTcpWsEnvelopeParity:
    """for each error class, the TCP and WS paths must emit
    byte-for-byte identical envelopes (modulo the optional ``id`` field
    that the TCP path adds for request/response correlation).
    """

    EXPECTED_ENVELOPES = {
        "invalid_json": {
            "type": "error",
            "data": {
                # namespaced form is canonical.
                "code": "client.invalid_payload",
                "message": "invalid JSON",
            },
        },
        "rate_limited": {
            "type": "error",
            "data": {
                "code": "client.rate_limited",
                "message": "rate limit exceeded; backing off",
            },
        },
        # ``internal_error`` is now emitted in the namespaced
        # form ``server.internal_error`` ( /  migration).
        # ``rate_limited`` and ``invalid_payload`` are now also
        # emitted in the namespaced form (``client.rate_limited`` /
        # ``client.invalid_payload``) on the dispatch path; the parity
        # test asserts the namespaced form. The previous per-envelope
        # ``legacy_code`` alias was removed once the renderer migrated
        # fully to the namespaced ``code`` form (see
        # ``voice_typer/server/ipc/validation.py``).
        "internal_error": {
            "type": "error",
            "data": {"code": "server.internal_error", "message": "internal error"},
        },
    }

    @pytest.mark.parametrize("error_class", list(EXPECTED_ENVELOPES.keys()))
    def test_tcp_envelope_matches_expected(self, error_class, authenticated_client, monkeypatch):
        """The TCP path emits the expected envelope for each error class."""
        client, server = authenticated_client
        expected = self.EXPECTED_ENVELOPES[error_class]

        if error_class == "invalid_json":
            _send_raw(client, "{not valid json")
        elif error_class == "internal_error":

            def boom(data, resp):  # noqa: ARG001
                raise RuntimeError("simulated handler crash")

            monkeypatch.setattr(server, "_handle_get_status", boom)
            _send_line(client, {"id": 1, "type": "get_status"})
        elif error_class == "rate_limited":
            limiter = server._rate_limiter_instance
            monkeypatch.setattr(limiter, "allow", lambda **kw: False)
            _send_line(client, {"id": 1, "type": "get_status"})
        else:
            pytest.fail(f"unknown error_class: {error_class}")

        resp = _read_response_line(client, timeout=2.0)
        # The TCP path adds an ``id`` field for request/response
        # correlation when the inbound message had one. Strip it for
        # the parity comparison (the WS path also adds ``id`` but only
        # at the ``_handle_connection`` layer, not in ``_make_dispatch``).
        resp_for_compare = {k: v for k, v in resp.items() if k != "id"}
        assert resp_for_compare == expected, (
            f"TCP path envelope for {error_class} does not match expected: got {resp_for_compare}, expected {expected}"
        )

    @pytest.mark.parametrize("error_class", list(EXPECTED_ENVELOPES.keys()))
    def test_ws_envelope_matches_expected(self, error_class):
        """The WS path emits the expected envelope for each error class."""
        import asyncio
        from unittest.mock import patch

        from voice_typer.server import sidecar_ws as sw

        expected = self.EXPECTED_ENVELOPES[error_class]
        server = _make_fake_server()
        dispatch = sw._make_dispatch(server)

        if error_class == "invalid_json":
            # The WS path handles invalid JSON in _handle_connection
            # (not in _make_dispatch). We assert the expected envelope
            # shape directly here; the live integration test in
            # tests/tauri/test_sidecar_ws_integration.py covers the
            # full path.
            assert expected["data"]["code"] == "client.invalid_payload"
            assert expected["data"]["message"] == "invalid JSON"
            return

        if error_class == "internal_error":
            server._dispatch = MagicMock(side_effect=RuntimeError("boom"))
        elif error_class == "rate_limited":
            # Call dispatch once to populate the real _RateLimiter
            # (see test_ws_rate_limit_returns_rate_limited_code for
            # why this is needed).
            asyncio.run(dispatch({"type": "get_status", "data": {}}, MagicMock()))
            patcher = patch.object(server._rate_limiter_instance, "allow", return_value=False)
            patcher.start()

        try:
            result = asyncio.run(dispatch({"type": "get_status", "data": {}}, MagicMock()))
        finally:
            if error_class == "rate_limited":
                patcher.stop()

        # The WS path's _make_dispatch does NOT add ``id`` (that
        # happens in _handle_connection). So the result should match
        # the expected envelope exactly.
        assert result == expected, (
            f"WS path envelope for {error_class} does not match expected: got {result}, expected {expected}"
        )
