"""PIR-SEC-1: regression tests for SEC-8 / SEC-9 / SEC-10 security fixes.

These three fixes close the security findings flagged in
``comprehensive-review.md`` §"Security":

  * **SEC-8** — TCP accept loop runs the auth handshake inline (soft
    DoS, 5s stall). Fix: hand off accepted connections to a worker
    thread pool IMMEDIATELY after ``accept()``, so a slow-auth client
    cannot block the accept loop from picking up the next legitimate
    client.

  * **SEC-9** — ``redact_secret`` regex gap for ``-``-delimited
    tokens. The previous patterns only matched Bearer/Token/sk-/
    32+ char generic alphanumerics, missing the common CLI flag /
    config-key forms ``--token=abc``, ``--token abc``,
    ``api_key=abc``. Fix: add specific flag / key=value patterns
    that fire on short inputs (bypassing the ``_MIN_REDACT_LEN``
    guard) because the keyword constraint makes them safe.

  * **SEC-10** — PowerShell script generation only escapes ``"`` as
    ``""`` (defense-in-depth gap). The previous generator embedded
    user-supplied values (path, target, arguments, description, icon,
    working directory) in double-quoted PowerShell strings, leaving
    ``$``, backtick, ``;``, ``|``, ``&``, ``()``, ``<>``, and
    newlines injectable. Fix: wrap each value in a single-quoted
    PowerShell string (``_ps_single_quote``) which disables ALL
    variable expansion, command substitution, and escape-sequence
    processing — the only escaping needed is doubling embedded
    single quotes (``'`` → ``''``).
"""

from __future__ import annotations

import contextlib
import inspect
import json
import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock pystray before importing ipc_server (transitively imports tray →
# pystray). Without this, pystray tries to connect to an X display on
# headless Linux CI and crashes.
_mock_pystray = MagicMock()
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

from voice_typer.server import (
    _secrets,  # noqa: E402
    server_platform,  # noqa: E402
)
from voice_typer.server._secrets import (
    assert_url_allowed,  # noqa: E402
    extend_url_allowlist,  # noqa: E402
    redact_secret,  # noqa: E402
)
from voice_typer.server.ipc_server import IPCServer  # noqa: E402
from voice_typer.server.tray import AppState  # noqa: E402

# ──────────────────────────────────────────────────────────────────────
# SEC-8: TCP accept loop must hand off connections to a worker pool
# ──────────────────────────────────────────────────────────────────────


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

        # XS-23: use monkeypatch.setenv (auto-restored at teardown) instead of
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
    deadline = time.time() + 5.0
    while time.time() < deadline:
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


class TestSEC8AcceptLoopWorkerPool:
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
        resp = _read_line(fast_sock, timeout=3.0)
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
# SEC-9: redact_secret must cover flag / key=value forms
# ──────────────────────────────────────────────────────────────────────


class TestSEC9RedactSecretFlagForms:
    """SEC-9: ``redact_secret`` must redact ``--token=abc``,
    ``--token abc``, and ``token=abc`` forms — not just Bearer/Token/
    sk-/32+ char generic alphanumerics.
    """

    def test_flag_equals_form(self):
        """``--token=abc123`` → ``--token=***``."""
        s = "starting sidecar with --token=abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        assert "abc123" not in redacted
        assert "--token=***" in redacted

    def test_flag_space_form(self):
        """``--token abc123`` → ``--token ***``."""
        s = "starting sidecar with --token abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        assert "abc123" not in redacted
        assert "--token ***" in redacted

    def test_bare_key_value_form(self):
        """``token=abc123`` → ``token=***`` (no flag prefix)."""
        s = "loaded config: token=abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        assert "abc123" not in redacted
        assert "token=***" in redacted

    def test_api_key_underscore_form(self):
        """``--api_key=secret`` → ``--api_key=***``."""
        s = "env: --api_key=sk-live-1234567890abcdef"
        redacted = redact_secret(s)
        assert "sk-live-1234567890abcdef" not in redacted
        assert "--api_key=***" in redacted

    def test_api_key_hyphen_form(self):
        """``--api-key=secret`` → ``--api-key=***``."""
        s = "env: --api-key=sk-live-1234567890abcdef"
        redacted = redact_secret(s)
        assert "sk-live-1234567890abcdef" not in redacted
        assert "--api-key=***" in redacted

    def test_password_form(self):
        """``password=hunter2`` → ``password=***``."""
        s = "db config: password=hunter2-supersecret"
        redacted = redact_secret(s)
        assert "hunter2-supersecret" not in redacted
        assert "password=***" in redacted

    def test_secret_form(self):
        """``--secret=xyz`` → ``--secret=***``."""
        s = "oauth --secret=oauth-client-secret-12345"
        redacted = redact_secret(s)
        assert "oauth-client-secret-12345" not in redacted
        assert "--secret=***" in redacted

    def test_access_token_form(self):
        """``--access_token=xyz`` → ``--access_token=***``."""
        s = "auth: --access_token=ya29-abcdef1234567890"
        redacted = redact_secret(s)
        assert "ya29-abcdef1234567890" not in redacted
        assert "--access_token=***" in redacted

    def test_case_insensitive_keyword(self):
        """Keywords are case-insensitive: ``--TOKEN=abc`` redacts too."""
        s = "starting sidecar with --TOKEN=abc123-secret-value"
        redacted = redact_secret(s)
        assert "abc123-secret-value" not in redacted
        # The prefix is preserved verbatim (case preserved).
        assert "--TOKEN=***" in redacted

    def test_short_input_with_flag_still_redacted(self):
        """SEC-9: flag patterns must fire even on short inputs.

        ``--token=abc`` is only 12 chars — below the 20-char
        ``_MIN_REDACT_LEN`` guard. Pre-SEC-9 the function returned
        short strings unchanged. Post-SEC-9 the flag patterns run
        BEFORE the length guard, so a short string with an explicit
        secret-bearing flag is still redacted.
        """
        s = "--token=abc"
        assert len(s) < 20  # sanity: under the generic threshold
        redacted = redact_secret(s)
        assert "abc" not in redacted
        assert "--token=***" in redacted

    def test_does_not_mangle_unrelated_key(self):
        """``\\bkey=`` must NOT match inside larger words like
        ``hotkey=`` or ``monkey=``.

        This is the false-positive guard: ``\\b`` ensures the keyword
        is a standalone word, not a suffix of a longer identifier.
        Without ``\\b``, ``hotkey=<f2>`` would be mangled to
        ``hot***`` — losing real config data.
        """
        s = "hotkey=<f2>"
        # The string is short (< 20 chars) AND has no flag-prefixed
        # secret keyword — it must pass through unchanged.
        assert redact_secret(s) == s

        # Same check on a longer string with the same hotkey= token.
        s_long = "the current hotkey=<f2> is set in the config file"
        redacted = redact_secret(s_long)
        assert "hotkey=<f2>" in redacted, (
            f"hotkey=<f2> must NOT be redacted (\\b boundary should "
            f"prevent `key=` matching inside `hotkey=`); got {redacted!r}"
        )

    def test_does_not_mangle_url_with_api_subdomain(self):
        """``api.example.com`` must NOT be redacted as ``api_key=``."""
        s = "https://user:pass@api.example.com/v1"
        redacted = redact_secret(s)
        # The hostname must be preserved.
        assert "api.example.com" in redacted
        # The password is a 32+ char run elsewhere — it'd be caught
        # by the generic pattern if present, but the keyword `key`
        # must not match `api.` (no `=` after `api`).

    def test_existing_bearer_pattern_still_works(self):
        """Regression: the existing ``Bearer <value>`` pattern must
        still fire after the SEC-9 changes.
        """
        s = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact_secret(s)
        assert "Bearer" in redacted
        assert "sk-abcdef" not in redacted
        assert "Bearer ***" in redacted

    def test_existing_token_pattern_still_works(self):
        """Regression: the existing ``Token <value>`` pattern must
        still fire.
        """
        s = "Token abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        redacted = redact_secret(s)
        assert "Token" in redacted
        assert "abcdefghijkl" not in redacted
        assert "Token ***" in redacted

    def test_existing_sk_pattern_still_works(self):
        """Regression: the existing ``sk-...`` pattern must still fire."""
        s = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        redacted = redact_secret(s)
        assert "sk-abc" not in redacted
        assert "***" in redacted

    def test_existing_generic_32char_pattern_still_works(self):
        """Regression: the generic 32+ char alphanumeric pattern must
        still fire on bare values without a keyword prefix.
        """
        s = "key=0123456789abcdef0123456789abcdef0123456789abcdef"
        redacted = redact_secret(s)
        # The 32+ char value must be redacted. With SEC-9 the
        # `key=...` form is matched by the bare-keyword pattern, so
        # the value is replaced with *** regardless of length. The
        # original 32+ char run must NOT appear.
        assert "0123456789abcdef0123" not in redacted

    def test_preserves_ordinary_long_text(self):
        """Regression: ordinary long text without secret patterns
        must pass through unchanged.
        """
        s = "This is a perfectly normal error message about a network timeout."
        assert redact_secret(s) == s

    def test_short_string_unchanged(self):
        """Regression: short strings without secret patterns must
        pass through unchanged.
        """
        assert redact_secret("short") == "short"
        assert redact_secret("1234567890123456789") == "1234567890123456789"

    def test_none_input(self):
        """Regression: ``None`` → ``"None"``."""
        assert redact_secret(None) == "None"

    def test_multiple_secrets_in_one_string(self):
        """Multiple secret-bearing tokens in the same string are all
        redacted.
        """
        s = "config: --token=abc123 --api_key=def456 password=hunter2"
        redacted = redact_secret(s)
        assert "abc123" not in redacted
        assert "def456" not in redacted
        assert "hunter2" not in redacted
        assert "--token=***" in redacted
        assert "--api_key=***" in redacted
        assert "password=***" in redacted


# ──────────────────────────────────────────────────────────────────────
# SEC-10: PowerShell script generation must escape dangerous characters
# ──────────────────────────────────────────────────────────────────────


class TestSEC10PsSingleQuote:
    """SEC-10: ``_ps_single_quote`` must produce a PowerShell
    single-quoted string that disables ALL variable expansion,
    command substitution, and escape-sequence processing.
    """

    @pytest.mark.parametrize(
        "dangerous_char,description",
        [
            ("$", "dollar — variable expansion"),
            ("`", "backtick — escape sequences (e.g. `n, `t)"),
            (";", "semicolon — statement chaining"),
            ("|", "pipe — pipeline operator"),
            ("&", "ampersand — call operator"),
            ("(", "open-paren — grouping"),
            (")", "close-paren — grouping"),
            ("<", "less-than — input redirection"),
            (">", "greater-than — output redirection"),
            ('"', "double-quote — was the only char escaped pre-SEC-10"),
            ("\n", "newline — statement separator"),
            ("\t", "tab — whitespace in commands"),
        ],
        ids=[
            "dollar",
            "backtick",
            "semicolon",
            "pipe",
            "ampersand",
            "open-paren",
            "close-paren",
            "less-than",
            "greater-than",
            "double-quote",
            "newline",
            "tab",
        ],
    )
    def test_dangerous_character_preserved_literal(self, dangerous_char, description):
        """Each dangerous character must appear LITERALLY inside the
        single-quoted string — no expansion, no escape-sequence
        processing, no statement break.
        """
        from voice_typer.server.server_platform import _ps_single_quote

        value = f"prefix{dangerous_char}suffix"
        quoted = _ps_single_quote(value)
        # The result must be wrapped in single quotes.
        assert quoted.startswith("'"), f"expected leading single quote, got {quoted!r}"
        assert quoted.endswith("'"), f"expected trailing single quote, got {quoted!r}"
        # The dangerous character must appear literally inside the
        # quotes. We strip the outer single quotes for the substring
        # check so we're looking at the inner content only.
        inner = quoted[1:-1]
        assert dangerous_char in inner, (
            f"dangerous char {dangerous_char!r} ({description}) must "
            f"appear literally inside the single-quoted string; "
            f"inner content was {inner!r}."
        )

    def test_single_quote_doubled(self):
        """Embedded single quotes must be doubled (``'`` → ``''``).

        This is the ONLY escaping required inside a PowerShell
        single-quoted string — it's what prevents a value containing
        ``'`` from prematurely terminating the string literal.
        """
        from voice_typer.server.server_platform import _ps_single_quote

        assert _ps_single_quote("a'b") == "'a''b'"
        assert _ps_single_quote("'") == "''''"
        assert _ps_single_quote("''") == "''''''"

    def test_empty_string(self):
        """An empty value produces an empty single-quoted string ``''``."""
        from voice_typer.server.server_platform import _ps_single_quote

        assert _ps_single_quote("") == "''"

    def test_non_string_input_stringified(self):
        """Non-string inputs are stringified via ``str()`` before quoting."""
        from voice_typer.server.server_platform import _ps_single_quote

        assert _ps_single_quote(42) == "'42'"
        assert _ps_single_quote(3.14) == "'3.14'"
        assert _ps_single_quote(None) == "'None'"

    def test_no_double_quote_escaping(self):
        """SEC-10 regression: double quotes must NOT be doubled.

        Pre-SEC-10 the generator used double-quoted strings and
        doubled embedded ``"`` as ``""``. Post-SEC-10 we use
        single-quoted strings, so embedded ``"`` is a literal
        character — no doubling.
        """
        from voice_typer.server.server_platform import _ps_single_quote

        # Double quote must appear ONCE, not doubled.
        assert _ps_single_quote('a"b') == "'a\"b'"
        assert '""' not in _ps_single_quote('a"b')


class TestSEC10BuildPowershellLnkScript:
    """SEC-10: the generated .lnk-creation PowerShell script must wrap
    every user-supplied value in a single-quoted string.
    """

    def _build(self, **overrides):
        """Helper: build a script with sensible defaults + overrides."""
        from voice_typer.server.server_platform import _build_powershell_lnk_script

        defaults = dict(
            lnk_path=Path("C:\\Users\\test\\Desktop\\Voice Typer.lnk"),
            target="C:\\Python311\\pythonw.exe",
            arguments='"C:\\app\\autostart_launcher.py"',
            icon_ico=None,
            description="Voice Typer — voice-to-text dictation",
        )
        defaults.update(overrides)
        return _build_powershell_lnk_script(**defaults)

    def test_uses_single_quoted_strings_not_double(self):
        """The generated script must wrap values in single-quoted
        strings, NOT double-quoted strings (the pre-SEC-10 pattern).
        """
        script = self._build()
        # The CreateShortcut call must use a single-quoted argument.
        assert "$s.CreateShortcut('" in script, (
            f"CreateShortcut must use a single-quoted argument; script was:\n{script}"
        )
        # The pre-SEC-10 form `CreateShortcut("` must NOT appear.
        assert '$s.CreateShortcut("' not in script, (
            f"CreateShortcut must NOT use a double-quoted argument (pre-SEC-10 pattern); script was:\n{script}"
        )
        # Same for the other property assignments.
        assert "$l.TargetPath = '" in script
        assert "$l.Arguments = '" in script
        assert "$l.Description = '" in script
        assert "$l.WorkingDirectory = '" in script
        # The pre-SEC-10 forms must NOT appear.
        assert '$l.TargetPath = "' not in script
        assert '$l.Arguments = "' not in script
        assert '$l.Description = "' not in script
        assert '$l.WorkingDirectory = "' not in script

    def test_icon_location_when_provided(self):
        """When ``icon_ico`` is provided, the IconLocation line uses
        a single-quoted string.
        """
        script = self._build(icon_ico=Path("C:\\app\\icon.ico"))
        assert "$l.IconLocation = '" in script, (
            f"IconLocation must use a single-quoted argument when icon_ico is provided; script was:\n{script}"
        )
        assert '$l.IconLocation = "' not in script

    def test_no_icon_location_when_absent(self):
        """When ``icon_ico`` is None, the IconLocation line is absent."""
        script = self._build(icon_ico=None)
        assert "IconLocation" not in script

    def test_injection_in_description_neutralized(self):
        """A malicious description like ``'; Remove-Item C:\\ -Recurse; '``
        must be neutralized — the embedded ``'`` chars are doubled so
        the description can't break out of the single-quoted string.
        """
        malicious = "'; Remove-Item C:\\ -Recurse; '"
        script = self._build(description=malicious)
        # The malicious description must appear with each `'` doubled.
        # The expected escaped form (after the outer single-quote
        # wrap is applied by ``_ps_single_quote``) is
        # ``'''; Remove-Item C:\\ -Recurse; '''`` — three single
        # quotes at each boundary (1 outer wrap + 2 from doubling
        # the embedded ``'``).
        expected_escaped = malicious.replace("'", "''")
        assert expected_escaped in script, (
            f"malicious description must be escaped via doubling of "
            f"single quotes; expected escaped form {expected_escaped!r} "
            f"in script:\n{script}"
        )
        # The raw un-escaped malicious payload must NOT appear as the
        # description value. Pre-SEC-10 the description was wrapped in
        # a single-quoted string WITHOUT doubling the embedded ``'``
        # chars, producing ``$l.Description = ''; Remove-Item...``
        # (exactly TWO single quotes after ``=``) — PowerShell parsed
        # this as an empty string ``''`` followed by ``; Remove-Item``
        # as a separate statement (the injection). Post-SEC-10 the
        # doubling produces ``$l.Description = '''; Remove-Item...``
        # (THREE single quotes after ``=``) which PowerShell parses
        # as a single-quoted string whose first character is a
        # literal ``'``.
        #
        # We can't use a plain ``"'; Remove-Item" not in script``
        # check because that substring is present in BOTH the
        # escaped form (``'''`` contains ``'`` followed by ``';``)
        # and the unescaped form. Instead we anchor on the
        # description-assignment prefix ``$l.Description =`` and
        # count quotes: the unescaped form has exactly two
        # single quotes after ``=`` (``= '';``) while the escaped
        # form has three (``= '''``). The 2-quote substring is NOT
        # a substring of the 3-quote form (the 4th char of ``= '''``
        # is ``'`` not ``;``), so this check reliably distinguishes
        # them.
        assert "$l.Description = '';" not in script, (
            f"raw un-escaped payload `= '';` must not appear in "
            f"script (would indicate the embedded `'` was NOT doubled "
            f"and could break out of the single-quoted context):\n{script}"
        )
        # Sanity: the escaped form (three single quotes after ``=``)
        # IS present, proving the doubling fired.
        assert "$l.Description = '''" in script, f"escaped form `= '''` must appear in script:\n{script}"

    @pytest.mark.parametrize(
        "dangerous_char",
        ["$", "`", ";", "|", "&", "(", ")", "<", ">", '"', "\n"],
    )
    def test_dangerous_char_in_target_path_is_literal(self, dangerous_char):
        """A target path containing a dangerous character must have
        that character appear literally inside the single-quoted
        string — no expansion or command execution.
        """
        target = f"C:\\path with {dangerous_char} char\\pythonw.exe"
        script = self._build(target=target)
        # The dangerous character must appear inside the single-quoted
        # TargetPath value. We don't assert the exact position — just
        # that the character is present in the script (it would be
        # stripped/escaped if the generator were mangling it).
        assert dangerous_char in script, (
            f"dangerous char {dangerous_char!r} must appear literally "
            f"in the generated script (inside a single-quoted string); "
            f"script was:\n{script}"
        )
        # The TargetPath line must be single-quoted.
        assert "$l.TargetPath = '" in script

    @pytest.mark.parametrize(
        "dangerous_char",
        ["$", "`", ";", "|", "&", "(", ")", "<", ">", '"', "\n"],
    )
    def test_dangerous_char_in_arguments_is_literal(self, dangerous_char):
        """Arguments containing a dangerous character must preserve
        it literally inside the single-quoted string.
        """
        arguments = f'"C:\\launcher.py" --opt {dangerous_char}value'
        script = self._build(arguments=arguments)
        assert dangerous_char in script, (
            f"dangerous char {dangerous_char!r} must appear literally "
            f"in the generated script (inside a single-quoted string); "
            f"script was:\n{script}"
        )
        assert "$l.Arguments = '" in script

    @pytest.mark.parametrize(
        "dangerous_char",
        ["$", "`", ";", "|", "&", "(", ")", "<", ">", '"', "\n"],
    )
    def test_dangerous_char_in_description_is_literal(self, dangerous_char):
        """Description containing a dangerous character must preserve
        it literally inside the single-quoted string.
        """
        description = f"Voice Typer {dangerous_char} dictation"
        script = self._build(description=description)
        assert dangerous_char in script, (
            f"dangerous char {dangerous_char!r} must appear literally "
            f"in the generated script (inside a single-quoted string); "
            f"script was:\n{script}"
        )
        assert "$l.Description = '" in script

    def test_script_ends_with_save(self):
        """The script must end with ``$l.Save()`` — the actual
        shortcut-write call. Sanity check that the structure is
        intact.
        """
        script = self._build()
        assert script.rstrip().endswith("$l.Save()"), f"script must end with $l.Save(); got tail: {script[-60:]!r}"

    def test_script_starts_with_com_object_creation(self):
        """The script must start by creating the WScript.Shell COM
        object. Sanity check that the structure is intact.
        """
        script = self._build()
        assert script.startswith("$s = New-Object -ComObject WScript.Shell"), (
            f"script must start with $s = New-Object -ComObject WScript.Shell; got head: {script[:80]!r}"
        )


class TestSEC10CreateLnkShortcutIntegration:
    """SEC-10: end-to-end check that ``_create_lnk_shortcut`` writes
    a single-quoted .ps1 file to disk when the win32com path is
    unavailable. We mock subprocess.run so no real powershell.exe is
    invoked (Linux CI doesn't have it).
    """

    def test_ps1_file_uses_single_quoted_strings(self, tmp_path, monkeypatch):
        """The .ps1 file written to disk must contain single-quoted
        strings (not the pre-SEC-10 double-quoted form).
        """
        # Force the win32com ImportError path so the PowerShell
        # fallback runs.
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "win32com.client" or name.startswith("win32com.client."):
                raise ImportError("mocked: win32com not available")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        # Capture the temp .ps1 file path so we can read it back.
        captured: dict = {}

        class _FakeCompletedProcess:
            returncode = 0
            stdout = b""
            stderr = b""

        def _fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            # cmd is ["powershell", "-NoProfile", ..., "-File", tmp]
            captured["ps1_path"] = cmd[-1]
            # Read the .ps1 file content NOW, while it still exists.
            # _create_lnk_shortcut's ``finally`` block deletes the
            # temp .ps1 file before returning (correct production
            # behavior — temp files must not leak), so we can't read
            # it after the function returns. Capturing the content
            # here (inside the mocked subprocess.run, which runs
            # BEFORE the finally block) lets us verify the on-disk
            # script content without disabling the production
            # cleanup.
            ps1_path = cmd[-1]
            try:
                with open(ps1_path, encoding="utf-8-sig") as f:
                    captured["content"] = f.read()
            except OSError as e:
                captured["content_error"] = e
            return _FakeCompletedProcess()

        monkeypatch.setattr("voice_typer.server.server_platform.subprocess.run", _fake_run)

        from voice_typer.server.server_platform import _create_lnk_shortcut

        # Use a description containing a dangerous character to verify
        # the escape fires end-to-end.
        result = _create_lnk_shortcut(
            lnk_path=Path("C:\\test\\Voice Typer.lnk"),
            target="C:\\Python311\\pythonw.exe",
            arguments='"C:\\app\\launcher.py"',
            icon_ico=None,
            description="Voice Typer; dictation",
        )

        assert result is True, "shortcut creation should have succeeded"
        assert "ps1_path" in captured, "subprocess.run was not invoked"
        assert "content" in captured, (
            f"failed to read .ps1 file content inside mocked subprocess.run: "
            f"{captured.get('content_error', 'unknown error')}"
        )
        content = captured["content"]

        # The .ps1 file must use single-quoted strings.
        assert "$s.CreateShortcut('" in content, f".ps1 file must use single-quoted strings; was:\n{content}"
        assert '$s.CreateShortcut("' not in content
        # The dangerous semicolon must appear literally inside the
        # description's single-quoted string.
        assert "Voice Typer; dictation" in content, (
            f"semicolon in description must appear literally inside single-quoted string; .ps1 was:\n{content}"
        )
        # The temp .ps1 file is cleaned up by the ``finally`` block
        # in _create_lnk_shortcut (correct production behavior). We
        # captured the content inside the mocked subprocess.run
        # above, so we don't need to re-read it here.


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


def test_sec9_flag_patterns_module_constants():
    """SEC-9: ``_secrets`` module must expose the new flag-pattern
    constants (static check that the fix is in place).
    """
    assert hasattr(_secrets, "_FLAG_KEY_PATTERNS")
    assert hasattr(_secrets, "_FLAG_VALUE_PATTERN")
    assert hasattr(_secrets, "_BARE_KEY_VALUE_PATTERN")
    assert hasattr(_secrets, "_SECRET_KEYWORDS")
    assert "token" in _secrets._SECRET_KEYWORDS
    assert "key" in _secrets._SECRET_KEYWORDS
    assert "password" in _secrets._SECRET_KEYWORDS
    assert "api_key" in _secrets._SECRET_KEYWORDS


def test_sec10_helper_functions_exist():
    """SEC-10: ``server_platform`` module must expose the new
    helper functions (static check that the fix is in place).
    """
    assert hasattr(server_platform, "_ps_single_quote")
    assert hasattr(server_platform, "_build_powershell_lnk_script")
    assert callable(server_platform._ps_single_quote)
    assert callable(server_platform._build_powershell_lnk_script)


# ──────────────────────────────────────────────────────────────────────
# G4-L-06: redact_secret generic threshold lowered from 32 to 20 chars
# ──────────────────────────────────────────────────────────────────────


class TestG4L06RedactSecretThreshold20:
    """G4-L-06: the generic ``[A-Za-z0-9_\\-]{N,}`` pattern threshold
    is lowered from 32 to 20 to match ``_MIN_REDACT_LEN``.

    Pre-fix, a 20-31 char bare token (e.g. a 24-char GitLab PAT, a
    20-char GitHub PAT, a 24-char Slack legacy token) fell through
    the generic pattern AND was already past the 20-char
    ``_MIN_REDACT_LEN`` early-exit guard — so it was returned
    UNREDACTED. Aligning the regex threshold with the length guard
    closes the gap.
    """

    def test_20_char_bare_token_redacted(self):
        """G4-L-06: a bare 20-char alphanumeric token is redacted.

        Pre-fix: 20 chars passed ``_MIN_REDACT_LEN`` but the generic
        pattern required 32+ chars, so the token survived untouched.
        """
        # Exactly 20 chars, no keyword prefix, no sk-/Bearer/Token.
        token = "0123456789abcdefghij"  # 20 chars
        assert len(token) == 20
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_24_char_bare_token_redacted(self):
        """G4-L-06: a 24-char bare token (e.g. GitLab PAT) is redacted."""
        # 24-char pure alphanumeric token (no prefix) to ensure the
        # generic pattern is what fires.
        token = "0123456789abcdefghij1234"  # 24 chars
        assert len(token) == 24
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_32_char_bare_token_redacted(self):
        """G4-L-06: a 32-char bare token (just under the old 33-char
        threshold) is now redacted.

        XS-98: the literal ``'0123456789abcdefghij123456789abc'`` is
        32 chars long (10 digits + 10 letters + 9 digits + 3 letters),
        but the previous assertion checked for 31 — a typo. Fixed here.
        """
        token = "0123456789abcdefghij123456789abc"  # 32 chars
        assert len(token) == 32
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_19_char_bare_token_preserved(self):
        """G4-L-06: a 19-char bare token is still preserved (below
        ``_MIN_REDACT_LEN``, which is also 20).

        This is the false-positive guard: short alphanumeric runs are
        too likely to be ordinary words/IDs to redact.
        """
        token = "0123456789abcdefghi"  # 19 chars
        assert len(token) == 19
        # Note: redact_secret returns short strings unchanged when no
        # keyword pattern fires.
        assert redact_secret(token) == token

    def test_32_char_bare_token_still_redacted(self):
        """G4-L-06: regression — the existing 32+ char behavior still
        works after lowering the threshold."""
        token = "0123456789abcdef0123456789abcdef"  # 32 chars
        assert len(token) == 32
        redacted = redact_secret(token)
        assert token not in redacted
        assert "***" in redacted

    def test_generic_pattern_threshold_constant_is_20(self):
        """G4-L-06: the regex threshold in ``_KEY_PATTERNS[-1]`` is
        ``{20,}`` (not the pre-fix ``{32,}``)."""
        import re

        # The last pattern in _KEY_PATTERNS is the generic catch-all.
        generic_pattern = _secrets._KEY_PATTERNS[-1]
        assert isinstance(generic_pattern, re.Pattern)
        pattern_str = generic_pattern.pattern
        # The pattern string is ``\b[A-Za-z0-9_\-]{20,}\b``.
        assert "{20,}" in pattern_str, (
            f"expected generic pattern threshold to be {{20,}} after G4-L-06; got pattern {pattern_str!r}"
        )
        assert "{32,}" not in pattern_str, (
            f"the pre-fix {{32,}} threshold must NOT appear in the generic "
            f"pattern after G4-L-06; got pattern {pattern_str!r}"
        )


# ──────────────────────────────────────────────────────────────────────
# G4-M-55: extend_url_allowlist emits WARNING-level audit log
# ──────────────────────────────────────────────────────────────────────


class TestG4M55ExtendUrlAllowlistAuditLog:
    """G4-M-55: ``extend_url_allowlist`` emits a WARNING-level audit
    log on every call so operators can trace every runtime expansion
    of the trusted-host set back to its origin."""

    def test_warning_emitted_with_hosts(self, caplog):
        """G4-M-55: a WARNING is emitted with the hosts being added."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server._secrets"):
                extend_url_allowlist(
                    ["audit-test.example.com"],
                    caller="test_g4_m_55_warning_emitted",
                )
            # The WARNING must mention the URL-Allowlist extension.
            assert any("[URL-Allowlist]" in r.message and r.levelname == "WARNING" for r in caplog.records), (
                f"expected WARNING-level URL-Allowlist log; got: {caplog.records!r}"
            )
            # The host must be in the log message.
            assert any("audit-test.example.com" in r.message for r in caplog.records), (
                f"host must be in the log message; got: {caplog.records!r}"
            )
        finally:
            _secrets._user_extensions.discard("audit-test.example.com")

    def test_warning_includes_caller(self, caplog):
        """G4-M-55: the WARNING includes the caller identifier."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server._secrets"):
                extend_url_allowlist(
                    ["caller-test.example.com"],
                    caller="explicit-caller-id",
                )
            joined = " ".join(r.message for r in caplog.records)
            assert "explicit-caller-id" in joined, f"caller identifier must appear in the log message; got: {joined!r}"
        finally:
            _secrets._user_extensions.discard("caller-test.example.com")

    def test_warning_auto_detects_caller_when_not_passed(self, caplog):
        """G4-M-55: when ``caller=None``, the caller is auto-detected
        via ``inspect.stack()`` and included in the WARNING."""
        try:
            with caplog.at_level("WARNING", logger="voice_typer.server._secrets"):
                # Don't pass caller — auto-detection should kick in.
                extend_url_allowlist(["auto-caller.example.com"])
            joined = " ".join(r.message for r in caplog.records)
            # The auto-detected caller should include this test function
            # name (or the test module name).
            assert (
                "test_warning_auto_detects_caller_when_not_passed" in joined
                or "TestG4M55" in joined
                or "test_sec_8_9_10_security_fixes" in joined
            ), f"auto-detected caller must reference this test; got: {joined!r}"
        finally:
            _secrets._user_extensions.discard("auto-caller.example.com")

    def test_warning_emitted_even_for_empty_input(self, caplog):
        """G4-M-55: even a no-op call (empty hosts iterable) emits a
        WARNING — operators want to see every attempt to extend the
        allowlist, including no-ops."""
        with caplog.at_level("WARNING", logger="voice_typer.server._secrets"):
            extend_url_allowlist([], caller="test-empty-input")
        assert any("[URL-Allowlist]" in r.message for r in caplog.records), (
            "even a no-op extend_url_allowlist call must emit a WARNING"
        )


# ──────────────────────────────────────────────────────────────────────
# G4-M-56: assert_url_allowed gains allow_loopback_http kwarg (default False)
# ──────────────────────────────────────────────────────────────────────


class TestG4M56AssertUrlAllowedLoopbackOptIn:
    """G4-M-56: ``assert_url_allowed`` gains an ``allow_loopback_http``
    kwarg (default ``False``). Pre-fix, loopback hosts (localhost,
    127.0.0.1, ::1) were ALWAYS exempt from the HTTPS requirement.
    Post-fix, callers must opt in via the kwarg."""

    def test_http_loopback_rejected_by_default(self):
        """G4-M-56: ``http://localhost:11434`` is REJECTED by default
        (``allow_loopback_http=False``). Pre-fix, it was accepted."""
        with pytest.raises(ValueError, match="HTTPS for loopback"):
            assert_url_allowed(
                "http://localhost:11434/v1/chat/completions",
                field_name="llm_api_url",
                client_name="test",
            )

    def test_http_loopback_allowed_when_opted_in(self):
        """G4-M-56: ``http://localhost:11434`` is ACCEPTED when the
        caller passes ``allow_loopback_http=True``."""
        # Should NOT raise.
        assert_url_allowed(
            "http://localhost:11434/v1/chat/completions",
            field_name="llm_api_url",
            client_name="test",
            allow_loopback_http=True,
        )

    def test_http_127_local_allowed_when_opted_in(self):
        """G4-M-56: ``http://127.0.0.1:8000`` is ACCEPTED when opted in."""
        assert_url_allowed(
            "http://127.0.0.1:8000/v1",
            allow_loopback_http=True,
        )

    def test_http_ipv6_loopback_allowed_when_opted_in(self):
        """G4-M-56: ``http://[::1]:8000`` is ACCEPTED when opted in."""
        assert_url_allowed(
            "http://[::1]:8000/v1",
            allow_loopback_http=True,
        )

    def test_https_loopback_allowed_without_opt_in(self):
        """G4-M-56: HTTPS to loopback is still accepted without opt-in
        (the kwarg only gates HTTP, not HTTPS)."""
        assert_url_allowed("https://localhost:8443/v1")

    def test_https_non_loopback_allowed(self):
        """G4-M-56: regression — HTTPS to a normal allowlisted host
        still works without opt-in."""
        assert_url_allowed("https://api.openai.com/v1/chat/completions")

    def test_http_non_loopback_rejected_even_with_opt_in(self):
        """G4-M-56: ``allow_loopback_http=True`` does NOT open the
        door to HTTP for non-loopback hosts — only loopback is exempted."""
        with pytest.raises(ValueError, match="HTTPS for non-loopback"):
            assert_url_allowed(
                "http://api.openai.com/v1/chat/completions",
                allow_loopback_http=True,
            )

    def test_loopback_http_error_message_mentions_kwarg(self):
        """G4-M-56: the error message for a rejected HTTP loopback URL
        mentions ``allow_loopback_http=True`` so the operator knows
        how to fix the call site."""
        with pytest.raises(ValueError, match="allow_loopback_http=True"):
            assert_url_allowed("http://localhost:11434/v1")

    def test_default_kwarg_value_is_false(self):
        """G4-M-56: the default value of ``allow_loopback_http`` is
        ``False`` — callers must explicitly opt in."""
        sig = inspect.signature(assert_url_allowed)
        param = sig.parameters["allow_loopback_http"]
        assert param.default is False, f"allow_loopback_http default must be False; got {param.default!r}"
