"""TCP IPC auth handshake tests (SEC-018).

Drives the real ``_handle_tcp_connection`` method with mock sockets so
the auth handshake, error envelopes, and dispatch loop are exercised
behaviorally — not via structural source inspection or stubbed
``_dispatch`` calls. Previously the "correct token" and "wrong token"
tests were ghosts that never invoked the real auth code path; this
rewrite sends real auth frames through the real handler and asserts on
the wire-format responses the server writes back to the conn.

Classes:
- TestTcpIpcAuthHandshake — per-launch session token auth for the TCP IPC server

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

from __future__ import annotations

import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from voice_typer.server.ipc.transport_tcp import IPC_PROTOCOL_VERSION
from voice_typer.server.ipc.validation import ErrorCodes

from tests.server.conftest import (  # noqa: F401
    IPCServer,
    server,
)

# ── Test helpers ────────────────────────────────────────────────────────


class _FakeSocket:
    """Minimal socket double for driving ``_handle_tcp_connection``.

    Returns a text-mode reader (``io.StringIO``) over the canned input
    via ``makefile()`` so the real ``_TCPLineIO`` reads the auth line +
    dispatch lines exactly as it would from a real socket. Captures
    every ``sendall()`` call so the test can inspect the error
    envelopes / dispatch responses the server writes back. No-ops
    ``settimeout`` / ``setsockopt`` / ``shutdown`` — the production
    handler wraps each in ``contextlib.suppress(OSError,
    AttributeError)`` so a real socket isn't required for the auth
    code paths.
    """

    def __init__(self, input_text: str = "") -> None:
        # Fresh StringIO so makefile() can return this same reader.
        self._reader = io.StringIO(input_text)
        self.sent_chunks: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False
        self.shutdown_count = 0

    def settimeout(self, t):
        self.timeouts.append(t)

    def setsockopt(self, *args, **kwargs):
        # No-op for socket.IPPROTO_TCP / TCP_NODELAY calls. The
        # production handler wraps this in suppress(OSError,
        # AttributeError) so any raise would be swallowed anyway.
        pass

    def makefile(self, mode="r", encoding=None, buffering=None):
        # ``_TCPLineIO.__init__`` calls
        # ``conn.makefile("r", encoding="utf-8", buffering=DEFAULT_BUFFER_SIZE)``
        # and stores the result on ``self._reader``. Subsequent
        # ``readline(size)`` calls hit StringIO.readline which
        # returns the next line (up to ``\n``) or ``""`` at EOF.
        return self._reader

    def sendall(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.sent_chunks.append(data)

    def shutdown(self, how):
        self.shutdown_count += 1

    def close(self):
        self.closed = True

    # ── Test-only helpers ──────────────────────────────────────────

    def sent_text(self) -> str:
        """Return everything the server wrote as a single string."""
        return b"".join(self.sent_chunks).decode("utf-8", errors="replace")


class _TimeoutFakeSocket(_FakeSocket):
    """Variant whose ``makefile()`` reader raises ``socket.timeout``.

    Simulates the auth-read timeout firing without actually sleeping
    5 seconds — the handler's ``except Exception:`` clause catches
    the ``socket.timeout`` (subclass of ``OSError`` → ``Exception``)
    and tears down the connection.
    """

    def __init__(self) -> None:
        super().__init__(input_text="")
        self._timeout_reader = MagicMock()
        self._timeout_reader.readline.side_effect = TimeoutError("auth read timed out (simulated)")

    def makefile(self, mode="r", encoding=None, buffering=None):
        return self._timeout_reader


def _make_dispatch_pool() -> ThreadPoolExecutor:
    """Construct a real dispatch pool for tests that exercise dispatch.

    Normally created lazily by ``start_tcp``; tests that call
    ``_handle_tcp_connection`` directly without ``start_tcp`` must
    populate ``server._tcp_dispatch_pool`` so the dispatch loop can
    submit ``_tcp_dispatch_and_respond`` tasks. Without it, the read
    loop's ``self._tcp_dispatch_pool.submit(...)`` raises
    ``AttributeError`` on the ``None`` attribute, the outer
    ``except Exception:`` catches it, and the dispatch response is
    never written back.
    """
    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-tcp-dispatch")


# ── SEC-018: TCP IPC session token auth ──────────────────────────────────


class TestTcpIpcAuthHandshake:
    """SEC-018: the TCP IPC server must authenticate the first message
    from the client against a per-launch session token.  Without this,
    any local process could connect to 127.0.0.1:9876 and send
    ``quit_app`` / ``set_config`` / etc.

    The token is passed via the ``VOICE_TYPER_IPC_TOKEN`` env var.
    When set, the first line from the client must be a JSON auth
    message with the matching token.  These tests drive the real
    ``_handle_tcp_connection`` method with mock sockets (``_FakeSocket``)
    so the auth handshake, error envelopes, and post-auth dispatch loop
    are exercised behaviorally.
    """

    def test_no_token_env_refuses_all_connections(self, server, monkeypatch):
        """SEC-018: When VOICE_TYPER_IPC_TOKEN is not set, the server
        must REFUSE all incoming TCP connections (refuse-all-by-default).

        Pre-SEC-2-fix, the server used to accept unauthenticated
        connections in standalone mode when the env var was absent.
        That behavior was closed because it allowed any same-user
        process on ``127.0.0.1:9876`` to dispatch arbitrary IPC
        commands (``quit_app`` / ``set_config`` / etc.) without
        authentication. The current behavior is: every accepted
        connection is closed before any auth or dispatch runs, and
        the bind-time logger emits an ERROR.
        """
        import os

        monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)

        # Confirm the env var is genuinely absent in the test process.
        assert os.environ.get("VOICE_TYPER_IPC_TOKEN", "") == ""

        # Drive the auth path with a mock socket.  The handler
        # MUST close the conn before any auth read or dispatch.
        conn = MagicMock()
        addr = ("127.0.0.1", 9999)
        # ``expected_token`` is the empty string (the value the TCP
        # server reads via ``os.environ.get(...)`` when the env var
        # is unset).  Per SEC-2 the handler must refuse and close.
        server._handle_tcp_connection(conn, addr, expected_token="")
        # Connection was closed without any auth or dispatch.
        conn.close.assert_called_once()
        # No read/write activity — the conn was closed before any
        # auth readline could happen.
        conn.recv.assert_not_called()

    def test_auth_with_correct_token_succeeds(self, server):
        """SEC-018: when the client sends the correct auth token, the
        connection is accepted, the auth-timeout is set on the conn
        (proving the early-refuse path was NOT taken), the conn is NOT
        closed before dispatch runs, subsequent dispatch requests are
        processed, and the response is written back to the conn.

        Also verifies ``hmac.compare_digest`` is used by sending the
        token with extra whitespace — a byte-exact comparison (which
        ``compare_digest`` is) MUST reject the whitespace-padded value,
        proving the comparison is not a substring / ``in`` match.
        """
        token = "correct-secret-token-abc-12345"
        auth_line = json.dumps({"type": "auth", "token": token}) + "\n"
        dispatch_line = json.dumps({"type": "get_status", "id": 42}) + "\n"
        fake = _FakeSocket(auth_line + dispatch_line)

        # ``_tcp_dispatch_pool`` is normally created lazily by
        # ``start_tcp``; populate it so the dispatch loop can submit
        # ``_tcp_dispatch_and_respond``.
        server._tcp_dispatch_pool = _make_dispatch_pool()
        try:
            server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token=token)
            # Drain pending dispatch tasks before asserting on the
            # written responses (the pool runs dispatches asynchronously
            # off the read loop; without draining, the response may not
            # be written yet).
            server._tcp_dispatch_pool.shutdown(wait=True)
        finally:
            server._tcp_dispatch_pool = None

        # (a) Connection was NOT closed before the auth handshake
        # completed: the auth-timeout ``settimeout`` fired (proving we
        # got past the early-refuse path), and the 5s default is set.
        assert fake.timeouts, "expected the handler to set an auth-timeout on the conn before reading"
        assert 5.0 in fake.timeouts, f"expected the default 5s auth timeout to be set; got {fake.timeouts}"
        # The conn IS closed at the end (graceful EOF after dispatch
        # drains and the read loop hits EOF).
        assert fake.closed, "expected the handler to close the conn after the dispatch loop hits EOF"

        # (b) Subsequent dispatch ran: a get_status response with id=42
        # was written back. It may be preceded by the state_changed
        # push event (always emitted on connect); collect all lines
        # and find the dispatch response.
        sent_text = fake.sent_text()
        assert sent_text, "expected the server to write at least one response"
        responses = [json.loads(line) for line in sent_text.splitlines() if line.strip()]
        status_responses = [r for r in responses if r.get("id") == 42]
        assert status_responses, f"get_status response (id=42) not found in: {sent_text!r}"
        assert status_responses[0]["type"] == "status", (
            f"expected dispatch response type='status', got {status_responses[0].get('type')!r}"
        )

        # (c) ``hmac.compare_digest`` is byte-exact and
        # order-independent — verify behaviorally by sending the token
        # with extra whitespace, which MUST NOT match. A naive
        # ``token in expected_token`` or substring match would have
        # spuriously accepted the padded value; ``compare_digest``
        # rejects it.
        wrong_attempt = " " + token + " "
        auth_ws = json.dumps({"type": "auth", "token": wrong_attempt}) + "\n"
        fake_ws = _FakeSocket(auth_ws)
        server._handle_tcp_connection(fake_ws, ("127.0.0.1", 9999), expected_token=token)
        ws_sent = fake_ws.sent_text()
        assert ws_sent, "expected an auth_failed error envelope for the whitespace-padded token"
        ws_envelope = json.loads(ws_sent.strip().splitlines()[-1])
        assert ws_envelope["type"] == "error", f"whitespace-padded token must yield type='error'; got {ws_envelope!r}"
        assert ws_envelope["data"]["code"] == "auth_failed", (
            f"whitespace-padded token must be rejected via hmac.compare_digest; got: {ws_envelope!r}"
        )
        assert fake_ws.closed, "wrong-token conn must be closed"

    def test_auth_with_wrong_token_drops_connection(self, server):
        """SEC-018: when the client sends the wrong auth token, the
        connection is dropped BEFORE any dispatch runs, and a
        structured ``auth_failed`` error envelope is written back to
        the conn so the client can distinguish auth failure from other
        transport-level errors.
        """
        expected = "server-side-secret-token"
        wrong = "client-side-wrong-token"
        auth_line = json.dumps({"type": "auth", "token": wrong}) + "\n"
        # Include a subsequent dispatch line that MUST NOT be processed.
        post_auth = json.dumps({"type": "get_status", "id": 99}) + "\n"
        fake = _FakeSocket(auth_line + post_auth)

        server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token=expected)

        # (a) Connection was closed.
        assert fake.closed, "wrong-token conn should be closed after auth failure"

        # (b) A structured auth_failed error envelope was written back.
        sent = fake.sent_text()
        assert sent, "expected an auth_failed error envelope to be written back"
        envelope = json.loads(sent.strip().splitlines()[-1])
        assert envelope["type"] == "error", f"expected type='error', got {envelope.get('type')!r}"
        data = envelope["data"]
        assert data["code"] == "auth_failed", f"expected code='auth_failed', got {data.get('code')!r}"
        assert "message" in data and data["message"], "auth_failed envelope must include a human-readable message"
        # The wrong token value must NOT be echoed back in the envelope
        # (defense in depth — the envelope uses a static message).
        assert wrong not in sent, f"wrong token value leaked into the auth_failed envelope: {sent!r}"
        assert expected not in sent, f"expected token value leaked into the auth_failed envelope: {sent!r}"

        # (c) No dispatch ran: no get_status response with id=99 was
        # written. The post_auth line was never read because the
        # handler returned after the auth-failure teardown.
        assert '"id":99' not in sent.replace(" ", ""), (
            f"dispatch must not run after auth failure — found id=99 in: {sent!r}"
        )

    def test_protocol_version_mismatch_returns_error(self, server):
        """DR-21: an auth frame with a mismatched ``protocol_version``
        is rejected BEFORE the token check with a structured
        ``server.protocol_version_mismatch`` error envelope carrying
        both the client and server version numbers.

        Note: the token in this frame is CORRECT — the test verifies
        the version check runs FIRST and rejects before the token
        check would have accepted.
        """
        bogus = IPC_PROTOCOL_VERSION + 1
        auth_line = (
            json.dumps(
                {
                    "type": "auth",
                    "token": "correct-token",
                    "protocol_version": bogus,
                }
            )
            + "\n"
        )
        fake = _FakeSocket(auth_line)

        server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token="correct-token")

        # Connection was closed after the mismatch envelope was sent.
        assert fake.closed, "mismatched protocol_version should close the conn after emitting the error envelope"
        sent = fake.sent_text()
        assert sent, "expected a protocol_version_mismatch error envelope to be written"
        envelope = json.loads(sent.strip().splitlines()[-1])
        assert envelope["type"] == "error", f"expected type='error', got {envelope.get('type')!r}"
        data = envelope["data"]
        assert data["code"] == ErrorCodes.PROTOCOL_VERSION_MISMATCH, (
            f"expected code={ErrorCodes.PROTOCOL_VERSION_MISMATCH!r}, got {data.get('code')!r}"
        )
        # The envelope must carry both version numbers so the client
        # can surface a "client vN, server vM" diagnostic.
        assert data["client_protocol_version"] == bogus, (
            f"expected client_protocol_version={bogus!r}, got {data.get('client_protocol_version')!r}"
        )
        assert data["server_protocol_version"] == IPC_PROTOCOL_VERSION, (
            f"expected server_protocol_version={IPC_PROTOCOL_VERSION!r}, got {data.get('server_protocol_version')!r}"
        )

    def test_auth_timeout_drops_connection(self, server):
        """SEC-018: when the client connects but sends nothing, the
        auth ``readline`` hits the 5-second timeout. The server must
        drop the connection without writing an ``auth_failed`` envelope
        (timeout is a transport-level event, not an auth rejection —
        the client never sent a token to reject).

        The 5s default is hardcoded as a local in
        ``_handle_tcp_connection``; we verify it is set on the conn via
        ``settimeout(5.0)`` and that the ``socket.timeout`` raised by
        the timed-out ``readline`` is handled cleanly (no crash, no
        auth_failed envelope, conn closed).
        """
        fake = _TimeoutFakeSocket()
        server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token="any-non-empty-token")

        # The handler set the 5-second auth timeout on the conn
        # (proving the timeout mechanism is wired up before the
        # auth read).
        assert 5.0 in fake.timeouts, f"expected the default 5s auth timeout to be set on the conn; got {fake.timeouts}"
        # Connection was closed (the timeout exception was caught and
        # the handler ran its teardown).
        assert fake.closed, "timeout should drop the connection (handler must close the conn)"
        # No auth_failed envelope was written — timeout is NOT an auth
        # rejection, so the server should not emit one (a client that
        # sees auth_failed might conclude the token was wrong and
        # retry with a different one, which is the wrong behavior for
        # a stalled connection).
        sent = fake.sent_text()
        assert "auth_failed" not in sent, f"timeout path should not write an auth_failed envelope; got: {sent!r}"
        assert sent == "", f"timeout path should not write any envelope; got: {sent!r}"

    def test_non_dict_msg_does_not_crash_dispatcher(self, server):
        """A non-dict JSON value (e.g. ``[1, 2, 3]``) dispatched AFTER
        auth must NOT crash the dispatcher with an unhandled
        ``AttributeError`` — ``_tcp_dispatch_and_respond`` catches it
        and surfaces a structured error envelope so the client can
        distinguish a malformed payload from a transport-level drop.
        """
        token = "non-dict-test-token"
        auth_line = json.dumps({"type": "auth", "token": token}) + "\n"
        # A JSON array is not a dict — ``_dispatch``'s ``msg.get("type")``
        # raises ``AttributeError``, which ``_tcp_dispatch_and_respond``
        # catches and surfaces as a structured ``server.internal_error``
        # envelope.
        bad_line = "[1, 2, 3]\n"
        fake = _FakeSocket(auth_line + bad_line)

        server._tcp_dispatch_pool = _make_dispatch_pool()
        try:
            server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token=token)
            # Wait for the async dispatch to complete so the error
            # envelope is in ``sent_chunks`` before we assert.
            server._tcp_dispatch_pool.shutdown(wait=True)
        finally:
            server._tcp_dispatch_pool = None

        # The handler completed without raising (no crash propagated
        # out of ``_handle_tcp_connection``).
        # The dispatcher should have written at least one structured
        # error envelope for the non-dict payload.
        sent = fake.sent_text()
        assert sent, "expected at least one response from the server"
        responses = [json.loads(line) for line in sent.splitlines() if line.strip()]
        error_envelopes = [r for r in responses if r.get("type") == "error"]
        assert error_envelopes, f"expected a structured error envelope for the non-dict payload; got: {sent!r}"
        # The error envelope must carry a namespaced code (not be an
        # AttributeError traceback echo or a bare ``{"type": "error"}``
        # with no diagnostic info).
        err = error_envelopes[0]
        assert "data" in err and "code" in err["data"], f"error envelope must include data.code; got: {err!r}"
        assert err["data"]["code"], "error code must be a non-empty string (not a bare exception echo)"

    def test_auth_failure_log_does_not_contain_token(self, server, caplog):
        """SEC-018: when auth fails, the rejected token value MUST NOT
        appear in any log record — the warning log line uses a static
        message ("invalid token") rather than echoing the supplied
        value, so a misconfigured client's logs (or a malicious
        observer of stderr) cannot harvest the secret by reading the
        server's auth-failure logs.
        """
        expected = "expected-secret-do-not-log-abcdef"
        wrong_attempt = expected + "-WRONG-SUFFIX"
        auth_line = json.dumps({"type": "auth", "token": wrong_attempt}) + "\n"
        fake = _FakeSocket(auth_line)

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"):
            server._handle_tcp_connection(fake, ("127.0.0.1", 9999), expected_token=expected)

        # Auth failure path ran and closed the conn.
        assert fake.closed, "auth failure should have closed the conn"
        # The auth_failed envelope was written back (sanity check).
        sent = fake.sent_text()
        assert sent, "expected an auth_failed envelope to be written"
        assert json.loads(sent.strip().splitlines()[-1])["data"]["code"] == "auth_failed"

        # The rejected token value MUST NOT appear in any log record.
        # Also check the expected (server-side) token — neither side
        # of the comparison should leak into logs.
        for record in caplog.records:
            msg = record.getMessage()
            assert wrong_attempt not in msg, f"rejected token value leaked into log: {msg!r}"
            assert expected not in msg, f"expected token value leaked into log: {msg!r}"
