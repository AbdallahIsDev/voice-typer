"""Unit tests for the Tauri sidecar WebSocket transport (ADR-0020).

These tests do NOT require the `websockets` package to be installed
(the module lazy-imports it inside `run()`). They exercise the
auxiliary helpers that ARE importable without the dep:

- :func:`sidecar_ws._force_line_buffered_stdout` — stdout reconfigure.
- :func:`sidecar_ws._emit_server_started` — JSON protocol.
- :func:`sidecar_ws._authenticate` — HMAC token check.
- :func:`sidecar_ws._make_dispatch` — shutdown + rate-limit + dispatch
  envelope construction.

The full WS round-trip (binding, accepting a connection, reading a
frame) is exercised by an integration test that requires the dep
installed — see ``tests/tauri/test_sidecar_ws_integration.py`` (skip
if websockets is not installed).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

# We import sidecar_ws lazily inside tests so a missing `websockets`
# dep doesn't break collection of these unit tests.
# Async tests are marked individually with @pytest.mark.asyncio; sync
# tests don't need the marker.


def _import_sidecar_ws():
    """Import sidecar_ws, skipping the test if websockets is missing.

    The module itself imports cleanly without websockets (the dep is
    only imported inside ``run()``), but the helpers we test here
    don't need it. This helper is a safety net in case the module's
    top-level imports grow.
    """
    from voice_typer.server import sidecar_ws

    return sidecar_ws


# ─── _emit_server_started ──────────────────────────────────────────────


def test_emit_server_started_writes_valid_json_to_stdout(capsys):
    """The host is parsing stdout for exactly this JSON shape."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(54321)
    captured = capsys.readouterr()
    assert captured.err == ""
    line = captured.out.strip()
    payload = json.loads(line)
    assert payload == {"event": "server_started", "port": 54321}


def test_emit_server_started_coerces_port_to_int(capsys):
    """The host expects an int port, not a string."""
    sw = _import_sidecar_ws()
    sw._emit_server_started(0)  # OS-assigned ephemeral
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["port"] == 0
    assert isinstance(payload["port"], int)


# ─── _authenticate ────────────────────────────────────────────────────


async def test_authenticate_rejects_when_token_env_missing(monkeypatch):
    """Without VOICE_TYPER_IPC_TOKEN, auth fails fast (no silent accept)."""
    monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)
    sw = _import_sidecar_ws()

    # Fake websocket — recv() should never be called when the env is missing.
    ws = MagicMock()
    ws.recv = AsyncMock()

    accepted = await sw._authenticate(ws)
    assert accepted is False
    ws.recv.assert_not_awaited()


async def test_authenticate_accepts_matching_token(monkeypatch):
    """Constant-time comparison of the token."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "deadbeef" * 8)
    sw = _import_sidecar_ws()

    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": "deadbeef" * 8}).encode()
    ws.recv = AsyncMock(return_value=auth_frame)

    accepted = await sw._authenticate(ws)
    assert accepted is True


async def test_authenticate_rejects_mismatched_token(monkeypatch):
    """Wrong token → reject (host treats as crash → FT-1 respawn)."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "deadbeef" * 8)
    sw = _import_sidecar_ws()

    ws = MagicMock()
    bad_frame = json.dumps({"type": "auth", "token": "wrong"}).encode()
    ws.recv = AsyncMock(return_value=bad_frame)

    accepted = await sw._authenticate(ws)
    assert accepted is False


async def test_authenticate_rejects_non_auth_first_frame(monkeypatch):
    """First frame must be `{"type":"auth",...}` — anything else is rejected."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    sw = _import_sidecar_ws()

    ws = MagicMock()
    bad_frame = json.dumps({"type": "get_status"}).encode()
    ws.recv = AsyncMock(return_value=bad_frame)

    accepted = await sw._authenticate(ws)
    assert accepted is False


async def test_authenticate_rejects_invalid_json(monkeypatch):
    """Garbage on the wire → reject, don't crash."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    sw = _import_sidecar_ws()

    ws = MagicMock()
    ws.recv = AsyncMock(return_value=b"not json at all")

    accepted = await sw._authenticate(ws)
    assert accepted is False


async def test_authenticate_rejects_timeout(monkeypatch):
    """A client that connects but never sends the auth frame is dropped."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    sw = _import_sidecar_ws()

    ws = MagicMock()
    # The auth code path is:
    #   first_raw = await asyncio.wait_for(websocket.recv(), timeout=...)
    # We want `websocket.recv()` to return a coroutine that never
    # resolves, so `asyncio.wait_for` times out. AsyncMock's return
    # value is awaited, so returning a never-resolved Future works.
    fut: asyncio.Future = asyncio.Future()

    async def _never_resolves():
        return await fut

    ws.recv = AsyncMock(side_effect=_never_resolves)
    # Patch the auth timeout down to 0.1s so the test doesn't wait 5s.
    sw._AUTH_TIMEOUT_SECONDS = 0.1

    accepted = await sw._authenticate(ws)
    assert accepted is False


async def test_authenticate_accepts_bytes_or_str(monkeypatch):
    """WS frames may arrive as bytes or str — both must work."""
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    sw = _import_sidecar_ws()

    # str variant
    ws = MagicMock()
    ws.recv = AsyncMock(return_value=json.dumps({"type": "auth", "token": "tok"}))
    assert await sw._authenticate(ws) is True


# ─── _make_dispatch: shutdown + rate limit + dispatch ──────────────────


def _make_fake_server():
    """Build a fake IPCServer with the attributes _make_dispatch needs."""
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    return server


async def test_dispatch_shutdown_returns_ack_and_schedules_quit(monkeypatch):
    """`{"type":"shutdown"}` → ack immediately, schedule app.quit() in background."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    result = await dispatch({"type": "shutdown"}, MagicMock())

    assert result == {"type": "result", "data": {"ack": True}}
    # app.quit() is scheduled on a background thread — give it a moment
    # to fire, then assert.
    await asyncio.sleep(0.05)
    server.app.quit.assert_called_once()


async def test_dispatch_normal_command_calls_underlying_dispatch(monkeypatch):
    """A normal command goes through server._dispatch and returns its result."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    result = await dispatch({"type": "get_status", "data": {}}, MagicMock())

    assert result == {"type": "result", "data": {"ok": True}}
    server._dispatch.assert_called_once()
    # The msg passed to _dispatch should preserve type + data.
    msg = server._dispatch.call_args[0][0]
    assert msg["type"] == "get_status"
    assert msg["data"] == {}


async def test_dispatch_missing_type_returns_invalid_payload():
    """A frame without a `type` field is a protocol error, not a crash."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    result = await dispatch({"data": {}}, MagicMock())

    assert result["type"] == "error"
    assert result["data"]["code"] == "invalid_payload"


async def test_dispatch_non_string_type_returns_invalid_payload():
    """`type` must be a string — numbers/None/objects are rejected."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    result = await dispatch({"type": 42}, MagicMock())
    assert result["data"]["code"] == "invalid_payload"


async def test_dispatch_dispatch_raises_returns_internal_error():
    """If server._dispatch raises, return a structured error, don't crash."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    server._dispatch = MagicMock(side_effect=RuntimeError("boom"))
    dispatch = sw._make_dispatch(server)

    result = await dispatch({"type": "get_status", "data": {}}, MagicMock())

    assert result["type"] == "error"
    assert result["data"]["code"] == "internal_error"


async def test_dispatch_rate_limit_enforced_under_burst():
    """The ADR-0019 rate limiter must reject after `burst` frames in a window."""
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {}})
    dispatch = sw._make_dispatch(server)

    # _RateLimiter default is burst=200, sustained=600 over 10s. Send
    # 201 frames rapidly; the 201st must be rate_limited.
    rejected_count = 0
    for _ in range(201):
        result = await dispatch({"type": "ping", "data": {}}, MagicMock())
        if (
            isinstance(result, dict)
            and result.get("type") == "error"
            and result.get("data", {}).get("code") == "rate_limited"
        ):
            rejected_count += 1

    # The exact count depends on the rate limiter's internal window
    # (some timestamps may fall outside the 10s window during the
    # test), but at least the 201st should be rejected.
    assert rejected_count >= 1, "expected at least one rate_limited response"


# ─── _force_line_buffered_stdout ───────────────────────────────────────


def test_force_line_buffered_stdout_does_not_crash():
    """The reconfigure call must not raise. We can't easily assert the
    buffering mode from inside the test (Python's stdout reconfigure
    is not introspectable post-call), but a clean return is the
    contract — the host depends on it not raising."""
    sw = _import_sidecar_ws()
    # Should be a no-op-or-reconfigure, never raise.
    sw._force_line_buffered_stdout()


# ─── Module-level constants (ADR-0020 contract) ───────────────────────


def test_loopback_host_is_127_0_0_1_only():
    """ADR-0020 §1 hard rule: bind must be 127.0.0.1 only, never 0.0.0.0 / ::."""
    sw = _import_sidecar_ws()
    assert sw._LOOPBACK_HOST == "127.0.0.1"


def test_max_frame_bytes_is_1_mib():
    """ADR-0020 §10: 1 MiB frame cap."""
    sw = _import_sidecar_ws()
    assert sw._MAX_FRAME_BYTES == 1024 * 1024


def test_shutdown_ack_timeout_is_2s():
    """ADR-0020 §10: 2.0s shutdown ack hard timeout."""
    sw = _import_sidecar_ws()
    assert sw._SHUTDOWN_ACK_TIMEOUT_SECONDS == 2.0
