"""TCP accept-loop worker-pool tests split out of ``tests/test_security_fixes.py``.

Domain: SEC-8 — the TCP accept loop must dispatch connections to a
worker pool IMMEDIATELY after ``accept()`` so a slow-auth client
cannot stall the accept loop. Pre-fix ``_handle_tcp_connection`` ran
INLINE on the accept-loop thread; post-fix it's submitted to
``self._tcp_worker_pool``.

Class/method names + assertions are preserved verbatim from the
original monolith — only file location has changed. The ``sec8_server``
fixture (real IPCServer on an ephemeral port) is co-located here
because it's only used by TestAcceptLoopWorkerPool.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer  # noqa: E402
from voice_typer.server.tray import AppState  # noqa: E402


class _MockApp:
    """Minimal app stub for the SEC-8 E2E test.

    Mirrors the structure of ``E2EMockApp`` in
    ``tests/test_e2e_pipeline.py`` but trimmed to just what the TCP
    accept / auth / get_status path needs.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self.tray = MagicMock()
        self.tray.state = AppState.IDLE
        self.tray._state = AppState.IDLE
        self.tray._message = ""

        self.config = MagicMock()
        self.config.hotkey = "<f2>"
        self.config.model_size = "small.en"
        self.config.asr_backend = "whisper"
        self.config.theme_mode = "system"
        self.config.schema_version = 1

        self._ipc_server = None
        self._quit_called = False
        self._restart_called = False
        self._dictation_toggled = False
        self.models = MagicMock()
        self.change_model = MagicMock()

        # use monkeypatch.setenv (auto-restored at teardown) instead of
        # raw os.environ assignment (which leaked across tests).
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))
        try:
            from voice_typer.server.history_db import HistoryDB

            self.history_db = HistoryDB(db_path=tmp_path / "sec8_history.db")
        except Exception:
            self.history_db = MagicMock()

        from voice_typer.server.service import VoiceTyperService

        self._service = VoiceTyperService(self)
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


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _send_line(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _read_line(sock: socket.socket, timeout: float = 3.0) -> dict:
    """Read one newline-terminated JSON line from ``sock``.

    Uses a one-shot buffer (sufficient for the SEC-8 test which reads
    exactly one response). The shared ``_read_line`` in
    ``test_e2e_pipeline.py`` is more sophisticated (persists across
    calls) but we don't need that here.
    """
    sock.settimeout(timeout)
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError(f"server closed; partial={bytes(buf)!r}")
        buf.extend(chunk)
    line, _ = buf.split(b"\n", 1)
    return json.loads(line.decode("utf-8"))


def _drain(sock: socket.socket, timeout: float = 0.3) -> list[dict]:
    """Read all immediately-pending lines from ``sock``."""
    results: list[dict] = []
    try:
        while True:
            results.append(_read_line(sock, timeout=timeout))
            timeout = 0.1
    except (TimeoutError, ConnectionError, OSError):
        pass
    return results


@pytest.fixture
def sec8_server(tmp_path, monkeypatch):
    """Start a real IPCServer on an ephemeral port for SEC-8 testing.

    Mirrors the ``e2e_server`` fixture in ``test_e2e_pipeline.py`` but
    trimmed to the minimum needed for the slow-auth-vs-fast-auth test.
    """
    port = _free_port()
    token = "sec8-token-AAAABBBBCCCCDDDD"
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", token)
    monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", str(tmp_path))

    from voice_typer.server import config as config_module

    monkeypatch.setattr(config_module, "_config_dir", lambda: tmp_path)

    app = _MockApp(tmp_path, monkeypatch)
    server = IPCServer(app)
    server.service.apply_config_side_effects = lambda updates: None
    from voice_typer.server.event_bus import subscribe as _set_push_event

    server._push_fn = server.push
    _set_push_event(server._push_fn)
    server._running = True
    server._hook_tray_set_state()
    server.start_tcp(port)

    # Wait for the TCP listener to be ready.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(0.5)
            test_sock.connect(("127.0.0.1", port))
            test_sock.close()
            break
        except (TimeoutError, ConnectionRefusedError):
            time.sleep(0.05)
    else:
        server._running = False
        if server._tcp_server_socket is not None:
            with contextlib.suppress(OSError):
                server._tcp_server_socket.close()
        pytest.fail("TCP server did not start within 5 seconds")

    yield server, port, token, app

    # Teardown — close everything to unblock the accept loop and worker
    # pool. We don't call server.stop() because it also tries to join
    # the stdin thread (which we never started).
    server._running = False
    from voice_typer.server.event_bus import unsubscribe as _clear_push_event

    if server._push_fn is not None:
        _clear_push_event(server._push_fn)
        server._push_fn = None
    if server._tcp_server_socket is not None:
        with contextlib.suppress(OSError):
            server._tcp_server_socket.close()
    if server._tcp_client is not None:
        with contextlib.suppress(Exception):
            server._tcp_client.close()
        server._tcp_client = None
    # SEC-8: shut down the worker pool too.
    pool = getattr(server, "_tcp_worker_pool", None)
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)
        server._tcp_worker_pool = None
    time.sleep(0.2)


class TestAcceptLoopWorkerPool:
    """SEC-8: the TCP accept loop must dispatch connections to a worker
    pool IMMEDIATELY after accept() so a slow-auth client cannot stall
    the accept loop.
    """

    def test_slow_auth_does_not_block_fast_auth(self, sec8_server):
        """A slow-auth client must not block a fast-auth client.

        Pre-fix behavior: ``_handle_tcp_connection`` ran INLINE on the
        accept-loop thread. A slow client (connects but sends nothing)
        would block the loop for the full 5-second auth timeout. Any
        other client that connected during that window was queued in
        the kernel backlog and not picked up until the slow client
        timed out.

        Post-fix: the slow client is handed off to a worker thread
        IMMEDIATELY after accept(), and the accept loop continues to
        accept the fast client's connection right away. The fast
        client's auth + dispatch completes in well under 5 seconds.

        We assert the fast client receives a get_status response
        within 3 seconds of connecting — well under the 5s auth
        timeout the slow client is holding. Pre-fix, this test would
        fail because the fast client's accept() would be delayed by
        ~5s.
        """
        server, port, token, app = sec8_server

        # 1) Open a "slow" client connection that sends NOTHING. The
        #    server's worker thread will block on readline() for up to
        #    5 seconds (the auth timeout). Pre-fix, this blocked the
        #    accept loop directly.
        slow_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        slow_sock.settimeout(10.0)
        slow_sock.connect(("127.0.0.1", port))
        # Send nothing. Give the server a moment to accept + hand off
        # to the worker thread (so the worker is genuinely blocked on
        # the slow read when the fast client connects).
        time.sleep(0.3)

        # 2) Open a "fast" client that authenticates immediately and
        #    sends a get_status request. Time how long it takes to
        #    get a response.
        fast_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        fast_sock.settimeout(5.0)
        start = time.monotonic()
        fast_sock.connect(("127.0.0.1", port))
        _send_line(fast_sock, {"type": "auth", "token": token})
        # Drain any initial push events (state_changed) before sending
        # the request so the _read_line below picks up the get_status
        # response (not the push).
        _drain(fast_sock, timeout=0.3)
        _send_line(fast_sock, {"id": 4242, "type": "get_status"})
        try:
            resp = _read_line(fast_sock, timeout=3.0)
        except (TimeoutError, ConnectionError, OSError) as exc:
            # A raw socket timeout here would surface as an unhandled
            # test ERROR instead of a diagnostic assertion failure. The
            # read timeout (3.0s) can fire before the threshold check
            # below on a loaded/coverage-instrumented machine, so
            # convert it into a clear, timetracked assertion instead.
            elapsed = time.monotonic() - start
            pytest.fail(
                f"fast client did not receive a response within 3.0s of "
                f"sending get_status ({elapsed:.2f}s elapsed) — "
                f"the slow-auth client likely blocked the accept loop "
                f"(SEC-8 regression): {exc!r}"
            )
        else:
            elapsed = time.monotonic() - start

        # 3) The fast client must have received a status response with
        #    the matching id. This proves the accept loop accepted the
        #    fast client's connection WHILE the slow client's auth
        #    handshake was still in flight on a worker thread.
        assert resp.get("id") == 4242, f"expected id=4242 in response, got {resp!r}"
        assert resp.get("type") == "status", f"expected type=status in response, got {resp!r}"

        # 4) The fast client must have received its response in well
        #    under the 5s auth timeout. Pre-fix, the response would
        #    have taken ~5s (slow client's auth timeout) plus the
        #    fast client's own dispatch time. We use 3.5s as the
        #    threshold — generous enough to absorb CI jitter, tight
        #    enough to fail clearly if the slow client blocks the
        #    accept loop.
        assert elapsed < 3.5, (
            f"fast client took {elapsed:.2f}s to get a response — "
            f"the slow-auth client likely blocked the accept loop "
            f"(SEC-8 regression). Expected < 3.5s."
        )

        # Cleanup: close the slow client. The worker thread's readline
        # will return EOF and the handler exits.
        with contextlib.suppress(OSError):
            slow_sock.close()
        with contextlib.suppress(OSError):
            fast_sock.close()

    def test_accept_loop_uses_worker_pool_static(self):
        """Static check: ``_accept_tcp`` must hand connections off to
        ``self._tcp_worker_pool.submit(...)`` instead of calling
        ``_handle_tcp_connection`` inline.

        This pins the architecture so a future refactor doesn't
        accidentally revert the SEC-8 fix. We strip comments before
        checking so explanatory text mentioning the old inline
        pattern doesn't trip the assertion.
        """
        source = inspect.getsource(IPCServer._accept_tcp)
        # Strip comment lines and inline comments.
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        # The accept loop must submit to the worker pool rather than
        # call _handle_tcp_connection directly.
        assert "_tcp_worker_pool" in code_only, (
            "_accept_tcp must reference self._tcp_worker_pool (SEC-8: "
            "hand connections off to a worker pool IMMEDIATELY after "
            "accept())."
        )
        assert "pool.submit" in code_only or "_tcp_worker_pool.submit" in code_only, (
            "_accept_tcp must call pool.submit(...) on the worker pool to hand off the connection (SEC-8)."
        )

        # The accept loop must NOT call _handle_tcp_connection inline
        # (the old pre-SEC-8 pattern). We allow it to appear inside
        # _run_tcp_handler_safely (which is the worker's entrypoint),
        # but the accept loop body itself must not call it directly.
        # Look for the call pattern `self._handle_tcp_connection(conn,`
        # — that's the inline form. The worker-pool form is
        # `pool.submit(self._run_tcp_handler_safely, conn, ...)`.
        assert "self._handle_tcp_connection(conn," not in code_only, (
            "_accept_tcp must NOT call self._handle_tcp_connection "
            "inline — that's the pre-SEC-8 pattern that allows a "
            "slow-auth client to stall the accept loop. Use "
            "pool.submit(self._run_tcp_handler_safely, ...) instead."
        )

    def test_stop_shuts_down_worker_pool(self):
        """Static check: ``stop()`` must shut down the worker pool so
        in-flight auth handshakes don't linger past shutdown.
        """
        source = inspect.getsource(IPCServer.stop)
        assert "_tcp_worker_pool" in source, "IPCServer.stop must shut down _tcp_worker_pool (SEC-8)."
        assert "shutdown" in source, "IPCServer.stop must call .shutdown() on the worker pool (SEC-8)."

    def test_dispatch_loop_uses_local_client_ref(self):
        """Static check: the dispatch loop in ``_handle_tcp_connection``
        must iterate over a LOCAL ``client`` reference (captured after
        auth succeeds), not ``self._tcp_client`` directly.

        With the SEC-8 worker-pool fix, multiple handlers can run
        concurrently. If a second client authenticates while the first
        is still in its dispatch loop, ``self._tcp_client`` is
        reassigned to the new client — iterating ``self._tcp_client``
        directly would read from the WRONG socket. The local
        ``client = auth_client`` capture (and the finally-block
        ``if self._tcp_client is client`` guard) prevents this.
        """
        source = inspect.getsource(IPCServer._handle_tcp_connection)
        # The dispatch loop must use a local `client` reference, not
        # `self._tcp_client` directly.
        assert "for line in client:" in source, (
            "_handle_tcp_connection dispatch loop must iterate over a "
            "local `client` reference (SEC-8: capture auth_client "
            "before the loop so a concurrent handler reassigning "
            "self._tcp_client doesn't cause this loop to read from "
            "the wrong socket)."
        )
        # The finally block must guard the self._tcp_client clear with
        # an identity check so we don't close another handler's client.
        assert "is client" in source, (
            "_handle_tcp_connection finally block must check "
            "`self._tcp_client is client` before clearing (SEC-8: "
            "another handler may have replaced self._tcp_client)."
        )


# ──────────────────────────────────────────────────────────────────────
# Smoke: ensure the new symbols are importable
# ──────────────────────────────────────────────────────────────────────


def test_sec8_worker_pool_attribute_exists():
    """SEC-8: IPCServer instances must have a ``_tcp_worker_pool``
    attribute (lazily created in ``start_tcp``).
    """
    from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes

    server, _, _ = make_ipc_server_with_fakes()
    # Before start_tcp(), the pool is None.
    assert hasattr(server, "_tcp_worker_pool")
    assert server._tcp_worker_pool is None
