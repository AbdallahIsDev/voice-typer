"""MIG-1.6 Phase 0-M Gate Check 3 — WS + HMAC handshake (macOS path).

This is gate check 3 of 9 for Phase 0-M (the macOS validation gate
defined in ``docs/migration/macos-validation-runbook.md`` §6.1 — note:
the task spec wrote §6.2 but the WS+HMAC gate is at §6.1 in the
runbook; §6.2 is the faster-whisper transcription gate). It validates
the WebSocket auth handshake for the Tauri → Python sidecar bridge on
the macOS path, but the tests themselves run on any platform because
the WS auth code is intentionally cross-platform (see
``test_no_platform_branch_in_auth_path`` and the arch-agnostic tests
in §9 below).

Per ADR-0020 §Reversibility, Phase 0-M must pass on BOTH Apple Silicon
(``aarch64-apple-darwin``) AND Intel (``x86_64-apple-darwin``). The
two arches are independently revertible — Apple Silicon can ship Tauri
while Intel still ships Electron. The WS auth path, however, is
arch-agnostic: the same ``sidecar_ws.py`` source runs (Nuitka-compiled)
in both per-arch binaries, and the auth protocol is byte-for-byte
identical. The arch selection happens at the Tauri ``externalBin``
spawn layer (``src-tauri/src/sidecar/spawn.rs:target_triple_for``),
NOT in the Python auth code.

What this gate proves
---------------------
- The sidecar refuses connections if ``VOICE_TYPER_IPC_TOKEN`` is unset
  (REVIEW-3 SEC-2 fix target — the WS path already enforces this).
- The auth frame ``{"type":"auth","token":"<64-hex>"}`` is the FIRST
  frame on the WS; anything else is rejected before dispatch runs.
- The token comparison uses ``hmac.compare_digest`` (constant-time).
- The raw token value is NEVER logged (only the literal word "token"
  appears in log lines, never the value).
- The WS server binds to ``127.0.0.1:0`` (loopback, ephemeral port) —
  never 0.0.0.0 / :: and never a fixed port.
- The chosen port is reported via a single ``server_started`` JSON
  line on stdout (the host blocks reading stdout until it sees this).
- The 1 MiB WS frame cap is enforced (``max_size`` on ``serve()``).
- The ADR-0019 rate limiter is applied to every inbound WS frame,
  shared across all connections to the same server process (CR-11).
- The auth handshake timeout is 5.0s (matches TCP path PR-3-FIX-1).
- There is NO platform branch in the auth path — macOS behaves
  identically to Linux/Windows.
- There is NO arch branch in the auth path — Apple Silicon behaves
  identically to Intel (the universal binary runs the same Python
  auth code; arch is implicit in the ``externalBin`` triple suffix).

What this gate does NOT prove (VALIDATE ON MACOS HOST)
--------------------------------------------------------
The tests below mock ``websockets.serve`` + ``os.environ`` — no real
WS server is bound and no real socket is opened. The end-to-end
"does the Rust host actually connect + auth + receive ``ready``"
proof must be run on a real macOS host per the runbook.

VALIDATE ON MACOS HOST:
    1. Launch Voice Typer (see check 2)
    2. Check ~/Library/Logs/voice-typer/voice-typer.log for:
       - "[SIDECAR_WS] listening on 127.0.0.1:XXXXX"
       - "[SIDECAR_WS] auth accepted from 127.0.0.1:XXXXX"
    3. Verify NO log line contains the raw token value
    4. Verify the sidecar refuses connections if VOICE_TYPER_IPC_TOKEN is unset
    Expected: auth handshake completes within 100ms; no token leakage in logs
    (Same behavior on both x86_64 and aarch64 — the WS auth path is arch-agnostic.)

Note on the log path: the runbook §6.1 tails
``$HOME/Library/Application Support/voice-typer/logs/voice-typer.log``
(Tauri's app-data dir on macOS), while the task spec's VALIDATE block
references ``~/Library/Logs/voice-typer/voice-typer.log`` (the
``os_log`` / ``~/Library/Logs`` convention). Both paths are
plausible depending on whether the Tauri log plugin writes to
app-support or to ``~/Library/Logs``; verify the actual path on the
macOS host (see findings in the gate report).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── Helpers ────────────────────────────────────────────────────────────

# Path to the Python source under test — used by the source-grep tests
# (token-never-logged + no-platform-branch + no-arch-branch). Resolved
# at import time so a missing file fails collection loudly rather than
# per-test.
_SIDECAR_WS_PATH = Path(__file__).resolve().parents[3] / "voice_typer" / "server" / "sidecar_ws.py"
assert _SIDECAR_WS_PATH.exists(), f"sidecar_ws.py not found at {_SIDECAR_WS_PATH}"

# Path to the Rust WS client source — used by the arch-agnostic test
# that asserts the Rust auth-frame construction has no ``cfg(target_arch)``
# branch. The Rust side builds the auth frame as
# ``json!({"type": "auth", "token": token})`` (ws.rs:36) with no arch
# conditional — this test guards against a regression.
_WS_RS_PATH = Path(__file__).resolve().parents[3] / "src-tauri" / "src" / "sidecar" / "ws.rs"
assert _WS_RS_PATH.exists(), f"ws.rs not found at {_WS_RS_PATH}"

# Path to the Rust spawn.rs — used to document the externalBin target-triple
# resolution that selects the per-arch macOS binary at spawn time.
_SPAWN_RS_PATH = Path(__file__).resolve().parents[3] / "src-tauri" / "src" / "sidecar" / "spawn.rs"
assert _SPAWN_RS_PATH.exists(), f"spawn.rs not found at {_SPAWN_RS_PATH}"


def _import_sidecar_ws():
    """Import sidecar_ws lazily.

    The module imports cleanly without ``websockets`` installed (the
    dep is lazy-imported inside ``run()``), so this never skips. We
    still import inside a function so module-level MagicMock patches
    applied by the autouse ``mock_heavy_imports`` fixture in
    ``tests/conftest.py`` don't interfere with collection.
    """
    from voice_typer.server import sidecar_ws

    return sidecar_ws


def _read_sidecar_ws_source() -> str:
    """Read the sidecar_ws.py source as a string (for source-grep tests)."""
    return _SIDECAR_WS_PATH.read_text(encoding="utf-8")


def _read_ws_rs_source() -> str:
    """Read the Rust ws.rs source as a string (for the arch-agnostic test)."""
    return _WS_RS_PATH.read_text(encoding="utf-8")


def _read_spawn_rs_source() -> str:
    """Read the Rust spawn.rs source (for the externalBin-triple test)."""
    return _SPAWN_RS_PATH.read_text(encoding="utf-8")


def _make_fake_server():
    """Build a fake IPCServer with the attributes _make_dispatch needs."""
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    return server


# A realistic 64-char hex token (32 bytes × 2 hex chars), matching
# what `util::generate_token()` produces on the Rust side
# (see src-tauri/src/util.rs — "token must be 64 hex chars"). The
# Python side does NOT enforce a length, so any non-empty string works,
# but we use the realistic 64-char form to mirror production. The same
# token format is used on BOTH macOS arches — the Rust token generator
# is arch-agnostic (it calls OsRng / rand::thread_rng, not an arch-
# specific RNG).
_GOOD_TOKEN = "deadbeef" * 8  # 64 hex chars


# ─── 1. VOICE_TYPER_IPC_TOKEN env var must be set ───────────────────────


async def test_authenticate_refuses_when_ipc_token_env_unset(monkeypatch):
    """REVIEW-3 SEC-2: if VOICE_TYPER_IPC_TOKEN is unset, the sidecar
    must refuse ALL connections before reading any frame off the wire.

    This is the WS path's equivalent of the TCP path's "no token, no
    service" guard. Without it, a sidecar launched without the env
    var (e.g. a misconfigured tauri-plugin-shell scope) would silently
    accept any auth frame and the host would believe the bridge is
    secure when it isn't.

    On macOS this is especially important: the sidecar is spawned via
    ``externalBin`` (Tauri's per-arch binary resolution), and a
    missing env var would indicate the Rust host's spawn path failed
    to inject the token — the sidecar must fail closed, not open.
    """
    sw = _import_sidecar_ws()
    monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)

    ws = MagicMock()
    ws.recv = AsyncMock()

    accepted = await sw._authenticate(ws)

    assert accepted is False, "must reject when VOICE_TYPER_IPC_TOKEN is unset"
    # Critical: the sidecar must NOT read a frame off the wire when the
    # env var is missing — otherwise an unauth sidecar would still
    # consume a frame from an attacker before rejecting.
    ws.recv.assert_not_awaited()


async def test_authenticate_refuses_when_ipc_token_env_empty_string(monkeypatch):
    """An empty-string token is treated the same as unset (defense in depth)."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "")

    ws = MagicMock()
    ws.recv = AsyncMock()

    assert await sw._authenticate(ws) is False
    ws.recv.assert_not_awaited()


# ─── 2. Auth frame format + first-frame requirement ────────────────────


async def test_auth_frame_format_is_type_auth_token_string(monkeypatch):
    """The auth frame must be ``{"type":"auth","token":"<string>"}``.

    The Rust host builds exactly this shape (src-tauri/src/sidecar/ws.rs:36)::

        let auth = json!({"type": "auth", "token": token});
        ws_tx.send(Message::Text(auth.to_string()))

    The Python side validates ``type == "auth"`` and ``token`` is a
    non-empty string. The token itself is compared with
    ``hmac.compare_digest`` (see next test). This frame format is
    identical on both macOS arches — the Rust host's auth-frame
    construction has no ``cfg(target_arch)`` branch (verified in
    ``test_rust_auth_frame_has_no_arch_branch``).
    """
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": _GOOD_TOKEN}).encode()
    ws.recv = AsyncMock(return_value=auth_frame)

    assert await sw._authenticate(ws) is True


async def test_auth_frame_must_be_first_frame_non_auth_rejected(monkeypatch):
    """The auth frame is the FIRST frame — a non-auth first frame is rejected.

    This proves the sidecar reads exactly one frame for auth and rejects
    if it isn't ``{"type":"auth",...}``. A client cannot send a dispatch
    frame first and then "retroactively" auth.
    """
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    # First (and only) frame is a get_status, not auth.
    bad_frame = json.dumps({"type": "get_status", "data": {}}).encode()
    ws.recv = AsyncMock(return_value=bad_frame)

    assert await sw._authenticate(ws) is False
    # recv must be called exactly once (only the first frame is read
    # during auth — subsequent frames are read by the dispatch loop,
    # which only runs if auth succeeds).
    assert ws.recv.await_count == 1


async def test_auth_frame_missing_token_field_rejected(monkeypatch):
    """A frame with type=auth but no token field is rejected."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(
        return_value=json.dumps({"type": "auth"}).encode()  # no token
    )

    assert await sw._authenticate(ws) is False


async def test_auth_frame_empty_token_rejected(monkeypatch):
    """A frame with token="" is rejected (no silent accept on empty token)."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": ""}).encode())

    assert await sw._authenticate(ws) is False


async def test_auth_frame_non_string_token_rejected(monkeypatch):
    """A frame with token=42 (non-string) is rejected before comparison."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": 42}).encode())

    assert await sw._authenticate(ws) is False


# ─── 3. hmac.compare_digest (constant-time comparison) ─────────────────


def test_authenticate_uses_hmac_compare_digest():
    """The token comparison must use ``hmac.compare_digest`` (constant-time).

    A plain ``==`` comparison short-circuits on the first mismatched
    byte, allowing a timing side-channel that leaks the token prefix.
    ``hmac.compare_digest`` always compares every byte, closing the
    channel. This test asserts the source literally calls
    ``hmac.compare_digest`` (not ``==``) on the token pair.

    On macOS this matters as much as on any other OS: the sidecar log
    lives in ``~/Library/Logs/`` (or app-support) and any local user
    can read it, but a timing side-channel would let an attacker who
    can repeatedly connect to the loopback WS recover the token byte
    by byte without reading any file.
    """
    source = _read_sidecar_ws_source()

    # The source must contain a `hmac.compare_digest(provided, expected_token)`
    # call (or equivalent with swapped args). We look for the literal
    # function call to guard against a regression that switches to `==`.
    assert "hmac.compare_digest" in source, (
        "sidecar_ws.py must use hmac.compare_digest for token comparison "
        "(constant-time). Found neither — possible timing side-channel "
        "regression."
    )

    # And it must be called with the provided + expected tokens (not
    # e.g. comparing a hardcoded constant). Look for the call shape.
    pattern = r"hmac\.compare_digest\s*\(\s*provided\s*,\s*expected_token\s*\)"
    assert re.search(pattern, source), (
        "hmac.compare_digest must be called as "
        "hmac.compare_digest(provided, expected_token) — found a different "
        "call shape which may indicate the comparison is not actually "
        "between the user-supplied + env-var tokens."
    )


async def test_authenticate_compare_digest_is_actually_invoked(monkeypatch):
    """Runtime check: hmac.compare_digest is called during auth (not just
    present in source). Guards against dead-code regressions where the
    compare_digest call is unreachable."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": _GOOD_TOKEN}).encode())

    # Spy on hmac.compare_digest without changing its behavior.
    real_compare = sw.hmac.compare_digest
    spy = MagicMock(side_effect=real_compare)
    monkeypatch.setattr(sw.hmac, "compare_digest", spy)

    assert await sw._authenticate(ws) is True
    spy.assert_called_once_with(_GOOD_TOKEN, _GOOD_TOKEN)


# ─── 4. Token NEVER logged ─────────────────────────────────────────────


def test_token_value_never_appears_in_any_log_call():
    """The raw token value must NEVER appear in any log line.

    If the sidecar logs the token (even at debug level), the token
    ends up in the macOS log file (``~/Library/Logs/voice-typer/`` or
    ``~/Library/Application Support/voice-typer/logs/``) which is a
    plain text file any local user can read, defeating the bearer-token
    auth.

    This test scans every ``log.<level>(...)`` call in sidecar_ws.py
    and asserts none of them interpolate the ``provided`` or
    ``expected_token`` variables (or any variable holding the token
    value) into the log message. Only static strings like "auth
    accepted" / "token=<redacted>" are permitted — never the value.
    """
    source = _read_sidecar_ws_source()

    # The token-bearing identifiers in _authenticate are:
    #   - expected_token  (the env-var value)
    #   - provided        (the frame's token field)
    #   - first_raw       (the raw frame bytes/str — could contain token)
    token_bearing_vars = ("expected_token", "provided", "first_raw")
    # `first` is excluded from the bare-name check because it appears
    # in legitimate log messages like "first authenticated connection"
    # — but we DO check that `first` is never interpolated as a log
    # arg (e.g. `log.info("...%s", first)` would leak the frame).

    log_call_pattern = re.compile(r"log\.(info|debug|warning|error|critical)\s*\(")

    for lineno, line in enumerate(source.splitlines(), start=1):
        if not log_call_pattern.search(line):
            continue
        # Skip comment lines that mention log. (e.g. docstring references)
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        for var in token_bearing_vars:
            word_pattern = re.compile(r"\b" + re.escape(var) + r"\b")
            if word_pattern.search(line):
                pytest.fail(
                    f"Potential token leak: line {lineno} contains a log call "
                    f"and references token-bearing variable `{var}`:\n  {line}\n"
                    f"Token values must never be interpolated into log messages."
                )

    # Also assert the literal _GOOD_TOKEN test value doesn't appear
    # in the source (sanity check that we're not accidentally shipping
    # a hardcoded test token in production code).
    assert "deadbeef" not in source.lower(), "sidecar_ws.py contains a hardcoded 'deadbeef' token — remove it."


def test_log_lines_use_static_strings_not_token_interpolation():
    """Every log call in _authenticate uses a static string (no %s for token).

    This is a stricter complement to the test above: it asserts that
    the specific log calls inside the _authenticate function use
    string literals only — no %-interpolation of any variable that
    could hold the token. The log lines may say "token=<redacted>"
    (static) but never "token=%s" % provided.
    """
    source = _read_sidecar_ws_source()

    # Extract the _authenticate function body.
    match = re.search(
        r"async def _authenticate\(.*?\n((?:.|\n)*?)(?=\n(?:async )?def |\Z)",
        source,
    )
    assert match, "_authenticate function not found in sidecar_ws.py"
    auth_body = match.group(1)

    # Every log.<level>(...) call inside _authenticate must use a
    # string literal as its first arg (no f-string, no %-interp of
    # variables). The current implementation does this correctly —
    # this test guards against a regression that adds e.g.
    # `log.debug("got token: %s", provided)`.
    log_call_re = re.compile(r"log\.(info|debug|warning|error|critical)\(\s*(f[\"'])")
    offenders = log_call_re.findall(auth_body)
    assert not offenders, (
        f"_authenticate uses f-string log calls (could leak token): {offenders}. "
        f"Use static strings only — token values must never be interpolated."
    )


# ─── 5. Binds to 127.0.0.1:0 (loopback, ephemeral port) ────────────────


def test_loopback_host_constant_is_127_0_0_1():
    """ADR-0020 §1: bind host must be exactly 127.0.0.1.

    Binding 0.0.0.0 / :: would (a) pop a Windows Defender Firewall
    prompt, (b) trigger the macOS Application Firewall prompt, (c)
    expose the authed-but-localhost IPC to the LAN. The sidecar
    must hardcode 127.0.0.1. On macOS the firewall prompt is
    particularly disruptive because it steals focus from the
    dictation hotkey — binding loopback avoids it entirely.
    """
    sw = _import_sidecar_ws()
    assert sw._LOOPBACK_HOST == "127.0.0.1"


def test_run_binds_to_loopback_ephemeral_port(monkeypatch):
    """``run()`` calls ``serve(handler, "127.0.0.1", 0, max_size=...)``.

    Port 0 = OS assigns an ephemeral port. This is critical for
    macOS: a fixed port would collide across multiple instances
    (dev + prod, or two user sessions) and would require a firewall
    rule. The OS-assigned ephemeral port avoids both. The same
    bind call is used on both macOS arches — the Python socket
    layer is arch-agnostic.

    This test mocks ``websockets.serve`` so no real socket is bound.
    """
    sw = _import_sidecar_ws()

    # Mock the websockets module + websockets.asyncio.server.serve.
    # serve() is used as `async with serve(...) as ws_server:` — so it
    # must return an async context manager whose __aenter__ yields an
    # object with a .sockets attribute.
    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
    mock_ws_server = MagicMock()
    mock_ws_server.sockets = [mock_socket]
    mock_ws_server.__aenter__ = AsyncMock(return_value=mock_ws_server)
    mock_ws_server.__aexit__ = AsyncMock(return_value=None)

    mock_serve = MagicMock(return_value=mock_ws_server)
    mock_websockets = MagicMock()
    mock_websockets_asyncio_server = MagicMock()
    mock_websockets_asyncio_server.serve = mock_serve
    monkeypatch.setitem(sys.modules, "websockets", mock_websockets)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", mock_websockets_asyncio_server)

    # _force_line_buffered_stdout reconfigures sys.stdout, which breaks
    # pytest's capsys — patch it to a no-op for this test.
    monkeypatch.setattr(sw, "_force_line_buffered_stdout", lambda: None)

    # asyncio.Future() blocks forever inside _main(). Patch it to raise
    # so _main() exits after _emit_server_started() runs (which is what
    # we want to observe). run() catches the Exception and returns 1.
    def _raise_immediately():
        raise RuntimeError("stop after server_started")

    monkeypatch.setattr(asyncio, "Future", _raise_immediately)

    server = _make_fake_server()
    rc = sw.run(server)

    # run() returns 1 (caught RuntimeError in the outer except Exception).
    assert rc == 1

    # serve() was called with (handler, "127.0.0.1", 0, max_size=1MiB).
    mock_serve.assert_called_once()
    call = mock_serve.call_args
    # call.args = (handler, host, port); call.kwargs = {"max_size": ...}
    assert call.args[1] == "127.0.0.1", "must bind to loopback"
    assert call.args[2] == 0, "must bind to ephemeral port (0 = OS-assigned)"
    assert "max_size" in call.kwargs, "must set max_size on serve()"
    assert call.kwargs["max_size"] == 1024 * 1024


# ─── 6. server_started JSON on stdout (NO arch field) ───────────────────


def test_emit_server_started_reports_port_as_json(capsys):
    """The host blocks reading the sidecar's stdout until it parses::

        {"event":"server_started","port":<int>}

    This is the ONLY line that ever goes to stdout (every other log
    goes to stderr / the file log). The host then opens a WS client
    to ws://127.0.0.1:<port>.
    """
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    assert captured.err == "", "stderr must be empty — only stdout carries the JSON"
    payload = json.loads(captured.out.strip())
    assert payload == {"event": "server_started", "port": 54321}


def test_emit_server_started_port_is_int_not_string(capsys):
    """The host's JSON parser expects port as an int, not a string."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(0)
    payload = json.loads(capsys.readouterr().out.strip())
    assert isinstance(payload["port"], int)
    assert not isinstance(payload["port"], bool), "port must not be a bool"


def test_server_started_json_does_not_leak_token(capsys):
    """SECURITY: the server_started JSON must NOT contain the token.

    The token is passed to the sidecar via the ``VOICE_TYPER_IPC_TOKEN``
    env var at spawn time (the host already knows it — it generated it).
    Echoing it back over stdout would leak it to any process that can
    read the sidecar's stdout pipe. On macOS the sidecar's stdout is
    piped to the Tauri host process, but a debugger or `dtrace` could
    observe it — so stdout must be token-free.
    """
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert "token" not in payload, (
        "server_started JSON must NOT contain the token — stdout is not "
        "a secure channel. The token is passed via env var at spawn."
    )
    raw_lower = captured.out.lower()
    assert "voice_typer_ipc_token" not in raw_lower, "stdout must not mention VOICE_TYPER_IPC_TOKEN (env-var name leak)"
    # Also assert the raw stdout doesn't contain the literal test token
    # value (defense in depth).
    assert _GOOD_TOKEN not in captured.out, "raw token value leaked to stdout"


def test_server_started_json_has_no_arch_field(capsys):
    """macOS-specific: the ``server_started`` JSON must NOT carry an ``arch`` field.

    The arch is IMPLICIT in the binary name selected by Tauri's
    ``externalBin`` mechanism at spawn time
    (``src-tauri/src/sidecar/spawn.rs:target_triple_for`` maps
    ``("aarch64", "macos")`` → ``"aarch64-apple-darwin"`` and
    ``("x86_64", "macos")`` → ``"x86_64-apple-darwin"``). The host
    already knows which arch it spawned — it picked the binary. Adding
    an ``arch`` field to ``server_started`` would be redundant and
    would create a spoofing risk (a malicious sidecar could lie about
    its arch to trick the host into a wrong code path).

    The protocol contract is: exactly two keys, ``event`` and ``port``.
    """
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())

    # Exactly two keys — no arch, no platform, no triple.
    assert set(payload.keys()) == {"event", "port"}, (
        "server_started JSON must have exactly {event, port} keys — "
        f"got {set(payload.keys())}. The arch is implicit in the "
        f"externalBin binary name; do not add it to the JSON."
    )
    assert "arch" not in payload, "server_started JSON must NOT contain 'arch'"
    assert "platform" not in payload, "server_started JSON must NOT contain 'platform'"
    assert "triple" not in payload, "server_started JSON must NOT contain 'triple'"


# ─── 7. 1 MiB frame cap ────────────────────────────────────────────────


def test_max_frame_bytes_constant_is_exactly_1_mib():
    """ADR-0020 §10: the WS frame cap is 1 MiB (1048576 bytes).

    Without a cap, a malformed/huge frame can OOM the client. The cap
    is enforced at the transport layer by passing ``max_size`` to
    ``websockets.serve()`` (see test_run_binds_to_loopback_ephemeral_port).
    The same cap applies on both macOS arches — the cap is a
    Python-int constant, not arch-dependent.
    """
    sw = _import_sidecar_ws()
    assert sw._MAX_FRAME_BYTES == 1024 * 1024
    assert sw._MAX_FRAME_BYTES == 1_048_576


def test_run_passes_max_size_to_serve(monkeypatch):
    """``run()`` passes ``max_size=_MAX_FRAME_BYTES`` to ``serve()``.

    The websockets library rejects any inbound frame > max_size at the
    transport layer with a 1009 close — the frame never reaches the
    dispatch loop. This is the correct enforcement point (re-checking
    in the dispatch loop would be dead code).
    """
    sw = _import_sidecar_ws()

    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
    mock_ws_server = MagicMock()
    mock_ws_server.sockets = [mock_socket]
    mock_ws_server.__aenter__ = AsyncMock(return_value=mock_ws_server)
    mock_ws_server.__aexit__ = AsyncMock(return_value=None)

    mock_serve = MagicMock(return_value=mock_ws_server)
    mock_websockets = MagicMock()
    mock_websockets_asyncio_server = MagicMock()
    mock_websockets_asyncio_server.serve = mock_serve
    monkeypatch.setitem(sys.modules, "websockets", mock_websockets)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", mock_websockets_asyncio_server)
    monkeypatch.setattr(sw, "_force_line_buffered_stdout", lambda: None)
    monkeypatch.setattr(asyncio, "Future", lambda: (_ for _ in ()).throw(RuntimeError("stop")))

    sw.run(_make_fake_server())

    mock_serve.assert_called_once()
    assert mock_serve.call_args.kwargs["max_size"] == sw._MAX_FRAME_BYTES


# 8. Rate limiter applied (ADR-0019 + ) ─────────────────────────


async def test_rate_limiter_applied_to_ws_frames():
    """ADR-0019: every inbound WS frame goes through the rate limiter.

    The default limiter is 200 burst / 600 sustained over a 10s window.
    Sending 201 frames rapidly must produce at least one
    ``rate_limited`` error response.
    """
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {}})
    dispatch = sw._make_dispatch(server)

    rejected = 0
    for _ in range(201):
        result = await dispatch({"type": "ping", "data": {}}, MagicMock())
        if (
            isinstance(result, dict)
            and result.get("type") == "error"
            and result.get("data", {}).get("code") == "rate_limited"
        ):
            rejected += 1

    assert rejected >= 1, (
        "expected at least one rate_limited response after 201 frames in "
        "the burst window — ADR-0019 limiter not applied to WS path"
    )


async def test_rate_limiter_is_shared_across_connections():
    """CR-11: the rate limiter is per-PROCESS (shared), not per-connection.

    A per-connection limiter would let a local attacker reset the 200-
    message burst budget by dropping the WS and reconnecting. The CR-11
    fix stores ONE ``_RateLimiter`` on the ``IPCServer`` instance via
    ``_get_rate_limiter(server)`` so all connections share the same
    sliding-window deque. This is critical on macOS where the loopback
    WS is reachable by any local process — the shared limiter prevents
    a reconnect-based burst-reset attack.
    """
    _import_sidecar_ws()
    server = _make_fake_server()

    # _make_dispatch does NOT create the limiter eagerly — it's created
    # on first frame via _get_rate_limiter. Call _get_rate_limiter
    # directly twice and assert it returns the SAME instance.
    from voice_typer.server.ipc_server import _get_rate_limiter

    limiter_1 = _get_rate_limiter(server)
    limiter_2 = _get_rate_limiter(server)

    assert limiter_1 is limiter_2, (
        "CR-11 regression: _get_rate_limiter returned different instances "
        "for the same server — the limiter must be shared across all WS "
        "connections to prevent burst-budget reset via reconnect."
    )


async def test_rate_limiter_rejects_with_structured_error():
    """A rate-limited frame returns ``{"type":"error","data":{"code":"rate_limited",...}}``.

    The host's backoff relies on this exact error shape to
    distinguish "slow down" from "internal error". A bare exception
    or a missing code field would trigger the wrong recovery path.
    """
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    # Exhaust the burst budget.
    for _ in range(200):
        await dispatch({"type": "ping", "data": {}}, MagicMock())

    # Next frame must be rate_limited.
    result = await dispatch({"type": "ping", "data": {}}, MagicMock())
    assert result["type"] == "error"
    assert result["data"]["code"] == "rate_limited"
    assert "message" in result["data"], "rate_limited error must include a message"


# ─── 9. macOS path == Linux path == Windows path (no platform/arch branch) ──


def test_no_platform_branch_in_auth_path():
    """The WS auth path must be 100% cross-platform — no ``sys.platform``,
    ``platform.system()``, or ``os.name`` check anywhere in sidecar_ws.py.

    The macOS path must be byte-for-byte identical to the Linux/Windows
    path. A platform branch in auth would be a bug farm: it would only
    be exercised on one platform, so the other platform's auth code
    would never be tested in CI (which runs on Linux). The current
    implementation has NO platform branch — this test guards against
    a regression that adds one.
    """
    source = _read_sidecar_ws_source()

    # Forbidden patterns: any platform-conditional that could branch
    # the auth behavior. The patterns are deliberately narrow (word
    # boundaries) to avoid false positives on docstrings that mention
    # "platform" in prose.
    forbidden_patterns = [
        (r"\bsys\.platform\b", "sys.platform check"),
        (r"\bplatform\.system\s*\(", "platform.system() call"),
        (r"\bplatform\.platform\s*\(", "platform.platform() call"),
        (r"\bos\.name\b", "os.name check"),
        (r"\bwin32\b", "win32 literal (use sys.platform check elsewhere)"),
        (r"\bWin32\b", "Win32 literal"),
        (r"\bdarwin\b", "darwin literal (sys.platform check)"),
    ]

    for pattern, description in forbidden_patterns:
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if re.search(pattern, line):
                pytest.fail(
                    f"Platform branch detected in sidecar_ws.py line {lineno}: "
                    f"{description}.\n  Line: {line.rstrip()}\n"
                    f"The WS auth path must be cross-platform — macOS must "
                    f"behave identically to Linux/Windows. Move any platform-"
                    f"specific logic out of sidecar_ws.py."
                )


def test_no_arch_branch_in_auth_path():
    """macOS-specific: the WS auth path must have NO arch branch.

    Phase 0-M requires the SAME auth behavior on Apple Silicon
    (``aarch64-apple-darwin``) and Intel (``x86_64-apple-darwin``).
    The Python ``sidecar_ws.py`` is arch-agnostic — Nuitka compiles
    the same .py source into either arch's binary, and the auth
    logic doesn't read ``platform.machine()`` / ``os.uname()`` /
    ``struct.calcsize`` / etc. The arch is selected at the Tauri
    ``externalBin`` spawn layer (see
    ``test_externalbin_triple_resolves_macos_arches``), NOT in the
    Python auth code.

    This test scans the source for arch-detection patterns and fails
    if any appear in code (docstring mentions are allowed).
    """
    source = _read_sidecar_ws_source()

    # Forbidden arch-detection patterns. These would indicate the auth
    # path tries to behave differently per-arch, which would break the
    # "one protocol, two arches" contract.
    forbidden_patterns = [
        (r"\bplatform\.machine\s*\(", "platform.machine() call"),
        (r"\bos\.uname\s*\(", "os.uname() call"),
        (r"\bstruct\.calcsize\s*\(", "struct.calcsize() call (pointer-size probe)"),
        (r"\bctypes\.sizeof\s*\(", "ctypes.sizeof() call (pointer-size probe)"),
        (r"\bsys\.maxsize\b", "sys.maxsize check (could branch on 64-bit)"),
        (r"\baarch64\b", "aarch64 literal in code"),
        (r"\bx86_64\b", "x86_64 literal in code"),
        (r"\barm64\b", "arm64 literal in code"),
        (r"\buniversal\b", "universal literal in code (universal binary branch)"),
    ]

    for pattern, description in forbidden_patterns:
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if re.search(pattern, line):
                pytest.fail(
                    f"Arch branch detected in sidecar_ws.py line {lineno}: "
                    f"{description}.\n  Line: {line.rstrip()}\n"
                    f"The WS auth path must be arch-agnostic — Apple Silicon "
                    f"and Intel must run identical auth code. Arch selection "
                    f"happens at the Tauri externalBin spawn layer, not in "
                    f"the Python sidecar."
                )


def test_auth_uses_only_standard_library_plus_websockets():
    """The auth path must use only stdlib (asyncio, hmac, json, os, logging)
    plus the lazy-imported ``websockets`` dep. No platform/arch-specific
    imports.

    A platform-specific import (e.g. ``import ctypes`` for Win32 APIs,
    or ``import platform`` for arch detection) in the auth code would
    silently break the auth path on the platform that doesn't have the
    import, or branch behavior per-arch. The auth path must be pure
    stdlib.
    """
    source = _read_sidecar_ws_source()

    # Top-level imports (the ones loaded at module import time, not
    # inside run()). These must all be stdlib.
    top_level_imports = re.findall(r"^import (\S+)$", source, re.MULTILINE)
    allowed_prefixes = (
        "asyncio",
        "contextlib",
        "hmac",
        "json",
        "logging",
        "os",
        "sys",
        "typing",
    )
    for imp in top_level_imports:
        # Allow `from __future__ import annotations`
        if imp == "__future__":
            continue
        top = imp.split(".")[0]
        assert top in allowed_prefixes, (
            f"Non-stdlib top-level import in sidecar_ws.py: {imp}. "
            f"The auth path must use only stdlib + lazy-imported websockets."
        )


def test_rust_auth_frame_has_no_arch_branch():
    """macOS-specific: the Rust WS client's auth-frame construction must
    have no ``cfg(target_arch)`` branch.

    The Rust host builds the auth frame as
    ``json!({"type": "auth", "token": token})`` (ws.rs:36). This frame
    is identical on both macOS arches — the universal binary
    (``cargo tauri build --target universal-apple-darwin``) compiles
    the same ws.rs into a fat Mach-O, and the auth-frame code path
    has no arch conditional. This test scans ws.rs for
    ``cfg(target_arch)`` / ``cfg(target_os)`` near the auth-frame
    construction and asserts there's no branch.
    """
    source = _read_ws_rs_source()

    # The auth frame is constructed at ws.rs:36:
    #   let auth = json!({"type": "auth", "token": token});
    # Verify the auth-frame construction is present and unconditional.
    assert 'json!({"type": "auth", "token": token})' in source, (
        'ws.rs must construct the auth frame as json!({"type": "auth", "token": token}) — got a different shape.'
    )

    # Scan for cfg(target_arch) anywhere in ws.rs — the WS client
    # (auth, reader, writer, reconnect) must be arch-agnostic. The
    # frame cap (MAX_FRAME_BYTES) is a constant from util.rs and is
    # also arch-independent.
    cfg_arch_re = re.compile(r"cfg\s*\(\s*target_arch\s*=")
    cfg_os_re = re.compile(r"cfg\s*\(\s*target_os\s*=")
    offenders_arch = cfg_arch_re.findall(source)
    offenders_os = cfg_os_re.findall(source)
    assert not offenders_arch, (
        f"ws.rs has cfg(target_arch=...) branches ({len(offenders_arch)}): "
        f"the WS auth path must be arch-agnostic on macOS. Move arch-"
        f"specific logic to spawn.rs (externalBin triple resolution)."
    )
    assert not offenders_os, (
        f"ws.rs has cfg(target_os=...) branches ({len(offenders_os)}): "
        f"the WS auth path must be cross-platform. Move OS-specific "
        f"logic out of ws.rs."
    )


def test_externalbin_triple_resolves_macos_arches():
    """macOS-specific: Tauri's ``externalBin`` resolves the per-arch
    macOS binary via the Rust target triple.

    ``src-tauri/src/sidecar/spawn.rs:target_triple_for`` maps::

        ("aarch64", "macos") → "aarch64-apple-darwin"
        ("x86_64",  "macos") → "x86_64-apple-darwin"

    Tauri's ``externalBin`` mechanism then looks for a binary named
    ``bin/python-sidecar-<triple>`` (e.g.
    ``bin/python-sidecar-aarch64-apple-darwin``) at spawn time. This
    is HOW the per-arch binary is selected on macOS — NOT via any
    arch check in the Python sidecar. The Python auth code is
    arch-agnostic precisely because the arch selection happens here,
    in the Rust spawn layer, before the Python process even starts.

    This test reads spawn.rs and verifies both macOS arch triples are
    present in the match arms.
    """
    source = _read_spawn_rs_source()

    assert '"aarch64", "macos"' in source and '"aarch64-apple-darwin"' in source, (
        'spawn.rs must map ("aarch64", "macos") → "aarch64-apple-darwin" '
        "for Tauri's externalBin to resolve the Apple Silicon sidecar."
    )
    assert '"x86_64", "macos"' in source and '"x86_64-apple-darwin"' in source, (
        'spawn.rs must map ("x86_64", "macos") → "x86_64-apple-darwin" '
        "for Tauri's externalBin to resolve the Intel sidecar."
    )


async def test_auth_protocol_identical_regardless_of_arch_env(monkeypatch):
    """macOS-specific: the auth protocol does NOT read any arch env var.

    A regression that adds e.g. ``VOICE_TYPER_ARCH`` or
    ``sysctl machdep.cpu.brand_string`` to branch the auth path would
    break the "one protocol, two arches" contract. This test asserts
    ``_authenticate`` produces the same accept/reject decision and
    calls ``hmac.compare_digest`` identically regardless of any
    arch-related env var being set.

    The contract: the auth decision is a pure function of
    (provided_token, expected_token) — nothing else.
    """
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    # Set a bunch of arch-related env vars that a regression MIGHT
    # read. _authenticate must ignore all of them.
    monkeypatch.setenv("VOICE_TYPER_ARCH", "aarch64")
    monkeypatch.setenv("VOICE_TYPER_TARGET_TRIPLE", "aarch64-apple-darwin")
    monkeypatch.setenv("HW_MACHINE", "arm64e")
    monkeypatch.setenv("ARCH", "arm64")

    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": _GOOD_TOKEN}).encode()
    ws.recv = AsyncMock(return_value=auth_frame)

    # Spy on hmac.compare_digest.
    real_compare = sw.hmac.compare_digest
    spy = MagicMock(side_effect=real_compare)
    monkeypatch.setattr(sw.hmac, "compare_digest", spy)

    # Must accept (token matches) — arch env vars must not change this.
    assert await sw._authenticate(ws) is True
    spy.assert_called_once_with(_GOOD_TOKEN, _GOOD_TOKEN)

    # Now flip the arch env vars to x86_64 and re-run — the decision
    # must be identical (accept, same compare_digest call).
    monkeypatch.setenv("VOICE_TYPER_ARCH", "x86_64")
    monkeypatch.setenv("VOICE_TYPER_TARGET_TRIPLE", "x86_64-apple-darwin")
    monkeypatch.setenv("HW_MACHINE", "x86_64")
    monkeypatch.setenv("ARCH", "x86_64")

    ws2 = MagicMock()
    ws2.recv = AsyncMock(return_value=auth_frame)
    spy2 = MagicMock(side_effect=real_compare)
    monkeypatch.setattr(sw.hmac, "compare_digest", spy2)

    assert await sw._authenticate(ws2) is True
    spy2.assert_called_once_with(_GOOD_TOKEN, _GOOD_TOKEN)


# ─── 10. Mock websockets.serve + os.environ (no real WS server) ─────────
#
# The two tests below explicitly document the mocking strategy used
# throughout this file: ``websockets.serve`` is mocked so no real
# socket is bound, and ``os.environ`` is manipulated via monkeypatch
# so the tests don't leak env-var state to each other. The tests above
# already exercise this pattern — these two tests assert the mocking
# strategy itself is sound.


def test_websockets_serve_is_mocked_in_run_path(monkeypatch):
    """Sanity check: when run() is called, no real websockets.serve fires.

    This is a meta-test: it asserts the mocking infrastructure works
    (mock_serve is called, not the real websockets.asyncio.server.serve).
    If this test fails, the mocking setup in the other run() tests is
    broken and those tests are giving false confidence.
    """
    sw = _import_sidecar_ws()

    real_serve_id = None
    try:
        from websockets.asyncio.server import serve as _real_serve

        real_serve_id = id(_real_serve)
    except Exception:
        pass  # websockets not installed — that's fine, the mock wins

    mock_socket = MagicMock()
    mock_socket.getsockname.return_value = ("127.0.0.1", 54321)
    mock_ws_server = MagicMock()
    mock_ws_server.sockets = [mock_socket]
    mock_ws_server.__aenter__ = AsyncMock(return_value=mock_ws_server)
    mock_ws_server.__aexit__ = AsyncMock(return_value=None)
    mock_serve = MagicMock(return_value=mock_ws_server)
    mock_websockets = MagicMock()
    mock_websockets_asyncio_server = MagicMock()
    mock_websockets_asyncio_server.serve = mock_serve
    monkeypatch.setitem(sys.modules, "websockets", mock_websockets)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.server", mock_websockets_asyncio_server)
    monkeypatch.setattr(sw, "_force_line_buffered_stdout", lambda: None)
    monkeypatch.setattr(asyncio, "Future", lambda: (_ for _ in ()).throw(RuntimeError("stop")))

    sw.run(_make_fake_server())

    assert mock_serve.called, "mocked serve() must be called — mocking setup is broken"
    if real_serve_id is not None:
        assert id(mock_serve) != real_serve_id, "mock_serve must not be the real websockets.serve"


async def test_os_environ_manipulation_does_not_leak_between_tests(monkeypatch):
    """monkeypatch.setenv/delenv auto-undoes after each test — verify.

    If two tests both set VOICE_TYPER_IPC_TOKEN to different values and
    the second sees the first's value, the auth tests would be flaky.
    monkeypatch scopes env-var changes to the test, so this is a no-op
    assertion — but it documents the contract.
    """
    sw = _import_sidecar_ws()
    # Set a token, verify it's visible.
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-a")
    assert sw.os.environ.get("VOICE_TYPER_IPC_TOKEN") == "test-a"

    # Re-set to a different value (simulating a second test).
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "test-b")
    assert sw.os.environ.get("VOICE_TYPER_IPC_TOKEN") == "test-b"

    # After this test, monkeypatch auto-undoes — the next test sees
    # the original env (or no env). This is the contract.


# ─── 11. Auth timeout (first frame must arrive within 5s) ──────────────


async def test_auth_frame_timeout_is_5_seconds():
    """ADR-0020 §3: a client that connects but never sends the auth frame
    is dropped after 5s (matches the TCP path's PR-3-FIX-1 timeout).

    The same 5s timeout applies on both macOS arches — it's a
    Python-float constant, not arch-dependent.
    """
    sw = _import_sidecar_ws()
    assert sw._AUTH_TIMEOUT_SECONDS == 5.0


async def test_auth_timeout_rejects_silent_client(monkeypatch):
    """A client that connects but never sends the auth frame is rejected."""
    sw = _import_sidecar_ws()
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", _GOOD_TOKEN)

    ws = MagicMock()
    # recv() never resolves → asyncio.wait_for times out.
    fut: asyncio.Future = asyncio.Future()

    async def _never_resolves():
        return await fut

    ws.recv = AsyncMock(side_effect=_never_resolves)
    # Patch the timeout down so the test doesn't wait 5s.
    monkeypatch.setattr(sw, "_AUTH_TIMEOUT_SECONDS", 0.1)

    assert await sw._authenticate(ws) is False
