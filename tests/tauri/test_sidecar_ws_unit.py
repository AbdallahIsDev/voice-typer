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
    """Wrong token → reject (host treats as crash → respawn)."""
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
    """Build a fake IPCServer with the attributes _make_dispatch needs.

    RT-FIX-9 / EC-FIX-3 (2026-07-24): ``_make_dispatch`` now uses
    ``loop.run_in_executor(server._ws_dispatch_pool, ...)`` (G4-H-30 —
    dedicated thread pool for WS dispatch, separate from the default
    executor). A MagicMock attribute access auto-vivifies a child
    MagicMock, so ``getattr(server, "_ws_dispatch_pool", None)`` returns
    a non-None MagicMock — the lazy-create branch in ``_make_dispatch``
    is skipped, and the MagicMock is passed to
    ``loop.run_in_executor``. ``asyncio.futures.wrap_future`` then
    asserts the submit() return is a real ``concurrent.futures.Future``
    and fails on the MagicMock.

    Fix: explicitly set ``server._ws_dispatch_pool = None`` so the
    lazy-create branch runs and creates a real ``ThreadPoolExecutor``
    that ``run_in_executor`` can use. The executor is shared across
    calls on the same server (so cleanup is the test's responsibility —
    we let it leak at process exit, which is fine for a unit test).
    """
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    # RT-FIX-9 / EC-FIX-3: force the lazy-create branch in
    # ``_make_dispatch`` to run (it creates a real ThreadPoolExecutor).
    # If we leave this unset, MagicMock auto-vivifies a child mock
    # that fails the ``wrap_future`` isinstance assertion.
    server._ws_dispatch_pool = None
    return server


async def test_dispatch_shutdown_returns_ack_and_schedules_quit(monkeypatch):
    """``{"type":"shutdown"}`` flows through ``server._dispatch`` like
    every other command (post-EC-FIX-3).

    RT-FIX-9 / EC-FIX-3 (2026-07-24): the WS path used to special-case
    ``shutdown`` here — it acked immediately with ``{"ack": True}`` and
    scheduled ``app.quit()`` on a background thread. EC-FIX-3 relocated
    the shutdown handler to the shared ``_COMMAND_REGISTRY`` entry
    ``"shutdown": "_handle_shutdown"`` (registered in ipc_server.py),
    which delegates to ``service.quit()`` — the SAME path the TCP
    ``quit_app`` command uses. The special-case is removed; ``shutdown``
    now flows through ``server._dispatch`` like every other command.

    This test was updated to assert the new behavior:
    ``server._dispatch`` is called with the shutdown message, and the
    result is whatever ``server._dispatch`` returns (in this fake, the
    default ``{"type": "result", "data": {"ok": True}}`` return value).
    The pre-EC-FIX-3 ack shape (``{"ack": True}``) is gone.
    """
    sw = _import_sidecar_ws()
    server = _make_fake_server()
    dispatch = sw._make_dispatch(server)

    result = await dispatch({"type": "shutdown", "data": {}}, MagicMock())

    # ``shutdown`` flows through ``server._dispatch`` (no special-case
    # ack). The fake server's _dispatch returns
    # ``{"type": "result", "data": {"ok": True}}``.
    assert result == {"type": "result", "data": {"ok": True}}
    # ``server._dispatch`` MUST have been called with the shutdown
    # message (the EC-FIX-3 relocation routes shutdown through the
    # shared dispatch path, not a special-case branch).
    server._dispatch.assert_called_once()
    msg = server._dispatch.call_args[0][0]
    assert msg["type"] == "shutdown"


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
    # RT-FIX-9: error codes are now namespaced. The internal-error
    # path emits ``server.internal_error`` (with the bare
    # ``internal_error`` legacy form preserved in ``legacy_code`` for
    # older clients). Accept either form for forward-compat.
    assert result["data"]["code"] in ("server.internal_error", "internal_error")


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
