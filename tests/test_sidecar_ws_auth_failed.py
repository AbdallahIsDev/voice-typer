"""regression: WS path must send an ``auth_failed`` error
frame BEFORE closing the socket with code 1008.

Before the WebSocket transport's auth-failure path diverged from
the TCP transport:

- TCP path (``ipc_server._handle_tcp_connection``, ~L925): on a bad
  token, writes a JSON error frame
  ``{"type":"error","data":{"code":"auth_failed","message":...}}`` to
  the socket and THEN closes it. The Rust host
  (``src-tauri/src/sidecar/ws.rs:340``) has a dedicated ``auth_failed``
  match arm that consumes this frame, logs it, and triggers an supervisor
  respawn with a fresh token.

- WS path (``sidecar_ws._handle_connection``): closed the socket with
  WS close-code 1008 ("Policy Violation") and sent NO error frame. The
  Rust client's ``auth_failed`` arm was therefore dead code on the WS
  path — the close-code-1008 teardown path never produced the envelope
  the arm was matching against. Clients could not distinguish an auth
  failure from any other transport-level close without sniffing the
  close reason string.

aligns the two transports: the WS path now sends the same
``auth_failed`` error frame the TCP path sends, BEFORE the
``websocket.close(code=1008)`` call. Both frames are wrapped in
``contextlib.suppress(Exception)`` so a half-closed socket (e.g. the
client RST'd after sending the bad token) does not crash the
connection handler before the authoritative close runs.

These tests exercise:

1. **Mismatched token** → ``auth_failed`` frame is sent, then close
   with code 1008.
2. **Missing token env var** → ``auth_failed`` frame is sent, then
   close with code 1008 (the env-missing path is the same rejection
   surface — a client that connects to a misconfigured sidecar should
   still see the envelope, not an opaque close).
3. **Invalid JSON auth frame** → ``auth_failed`` frame is sent, then
   close with code 1008.
4. **Non-auth first frame** → ``auth_failed`` frame is sent, then
   close with code 1008.
5. **Frame ordering** — the error frame is sent BEFORE
   ``websocket.close`` is called (the test asserts call order via the
   mock's ``call_args_list``).
6. **Send-then-close suppression** — if ``websocket.send`` raises
   (e.g. socket already half-closed), the close call still runs. This
   is the ``contextlib.suppress`` contract: a half-dead socket must
   not prevent the authoritative teardown.

The tests use ``MagicMock`` websockets (the same pattern as
``test_sidecar_ready_emitted.py`` and
``test_sidecar_ws_thread_safety.py``); no real ``websockets`` server
is bound.
"""

from __future__ import annotations

import contextlib
import json
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────


def _make_fake_websocket(auth_frame: bytes | str) -> MagicMock:
    """Build a mock websocket that yields *auth_frame* on the first recv.

    The mock's ``recv`` is a coroutine that returns *auth_frame* once
    (consumed by ``_authenticate``). ``send`` and ``close`` are
    ``MagicMock`` coroutines so the tests can assert call order and
    args. The dispatch loop is parked via an empty async iterator
    (same pattern as ``test_sidecar_ready_emitted.py``) — but for the
    auth-failure tests the dispatch loop never runs because
    ``_handle_connection`` returns early after the close.
    """
    ws = MagicMock()

    auth_frame_bytes = auth_frame.encode() if isinstance(auth_frame, str) else auth_frame

    async def _fake_recv():
        return auth_frame_bytes

    ws.recv = _fake_recv
    ws.remote_address = ("127.0.0.1", 12345)

    async def _track_send(payload):
        # Record the raw payload so the test can inspect the JSON.
        ws._sent_frames.append(payload)

    async def _track_close(*args, **kwargs):
        ws._closed_with.append((args, kwargs))

    ws._sent_frames: list[str] = []
    ws._closed_with: list[tuple[tuple, dict]] = []
    ws.send = _track_send
    ws.close = _track_close
    return ws


def _assert_auth_failed_frame(payload: str) -> dict:
    """Parse *payload* as JSON and assert it is the auth_failed envelope."""
    frame = json.loads(payload)
    assert frame.get("type") == "error", f"expected an error frame, got type={frame.get('type')!r} in {frame!r}"
    data = frame.get("data", {})
    assert data.get("code") == "auth_failed", f"expected code='auth_failed', got {data.get('code')!r} in {frame!r}"
    assert "message" in data and isinstance(data["message"], str), (
        f"expected a string 'message' field, got {data.get('message')!r} in {frame!r}"
    )
    return frame


# ── Tests: each auth-rejection path emits auth_failed before close ────


@pytest.mark.asyncio
async def test_mismatched_token_emits_auth_failed_frame_before_close(
    monkeypatch,
) -> None:
    """Wrong token → ``auth_failed`` error frame, THEN close with 1008.

    This is the canonical EC-11 parity case: the TCP path sends the
    envelope, so the WS path must too. The Rust host's
    ``ws.rs:340`` ``auth_failed`` match arm was dead code on the WS
    path before this fix.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "expected-secret")
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "wrong-secret"}))

    server = MagicMock()
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    # Exactly one error frame was sent.
    assert len(ws._sent_frames) == 1, f"expected exactly one auth_failed frame before close, got {ws._sent_frames}"
    _assert_auth_failed_frame(ws._sent_frames[0])

    # Close was called with code 1008.
    assert len(ws._closed_with) == 1, f"expected exactly one close call, got {ws._closed_with}"
    close_args, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008, f"expected close(code=1008), got kwargs={close_kwargs}"

    # Dispatch was NEVER invoked (auth failed before the dispatch loop).
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_missing_token_env_emits_auth_failed_frame_before_close(
    monkeypatch,
) -> None:
    """No ``VOICE_TYPER_IPC_TOKEN`` env var → auth_failed frame + close.

    Even when the sidecar is misconfigured (no token set), the client
    should see the envelope so it can branch on the code rather than
    treat the close as an opaque transport failure. The TCP path
    already closes the socket without sending a frame in this case
    (because the env check returns before the auth read); the WS path
    sends the frame unconditionally because the close-code 1008 is
    insufficient to communicate the cause.
    """
    monkeypatch.delenv("VOICE_TYPER_IPC_TOKEN", raising=False)
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "anything"}))

    server = MagicMock()
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    assert len(ws._sent_frames) == 1, f"expected one auth_failed frame even when env is missing, got {ws._sent_frames}"
    _assert_auth_failed_frame(ws._sent_frames[0])

    assert len(ws._closed_with) == 1
    close_args, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008


@pytest.mark.asyncio
async def test_invalid_json_auth_frame_emits_auth_failed_before_close(
    monkeypatch,
) -> None:
    """Garbage on the wire → auth_failed frame + close.

    The TCP path closes without a frame here (the JSON parse exception
    short-circuits before the ``code: "auth_failed"`` write). The WS
    path emits the frame for cross-transport envelope parity: any
    auth-time rejection produces the same envelope shape so the client
    can branch uniformly on ``code == "auth_failed"``.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    ws = _make_fake_websocket(b"not json at all")

    server = MagicMock()
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    assert len(ws._sent_frames) == 1, f"expected one auth_failed frame for invalid JSON, got {ws._sent_frames}"
    _assert_auth_failed_frame(ws._sent_frames[0])
    assert len(ws._closed_with) == 1
    close_args, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008


@pytest.mark.asyncio
async def test_non_auth_first_frame_emits_auth_failed_before_close(
    monkeypatch,
) -> None:
    """First frame is not ``{"type":"auth",...}`` → auth_failed + close.

    A client that sends a ``get_status`` frame before auth is
    rejected. Pre-the close was opaque; now the envelope is
    sent so the client can log the cause.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    ws = _make_fake_websocket(json.dumps({"type": "get_status"}))

    server = MagicMock()
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    assert len(ws._sent_frames) == 1, f"expected one auth_failed frame for non-auth first frame, got {ws._sent_frames}"
    _assert_auth_failed_frame(ws._sent_frames[0])
    assert len(ws._closed_with) == 1
    close_args, close_kwargs = ws._closed_with[0]
    assert close_kwargs.get("code") == 1008


@pytest.mark.asyncio
async def test_auth_failed_frame_is_sent_before_close(monkeypatch) -> None:
    """The error frame is sent BEFORE the close call (frame ordering).

    If the close runs first, the client may tear down its reader
    before the error frame arrives, making the envelope useless. The
    ``contextlib.suppress`` wrapping on both calls means the ordering
    is the only contract — the send must precede the close.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}))

    # Track the call sequence with a shared ordered list.
    call_log: list[str] = []

    async def _track_send(payload):
        call_log.append("send")

    async def _track_close(*args, **kwargs):
        call_log.append("close")

    ws.send = _track_send
    ws.close = _track_close

    server = MagicMock()
    dispatch = MagicMock()

    await sidecar_ws._handle_connection(ws, server, dispatch)

    assert call_log == ["send", "close"], f"expected send BEFORE close, got sequence {call_log!r}"


@pytest.mark.asyncio
async def test_send_failure_does_not_block_close(monkeypatch) -> None:
    """If ``websocket.send`` raises, the close call still runs.

    A client that RSTs after sending a bad token leaves the socket
    half-closed; ``websocket.send`` may raise. The
    ``contextlib.suppress(Exception)`` around the send ensures the
    close — the authoritative teardown — still runs. Without this, a
    half-dead socket would leak the connection handler.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok")
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "wrong"}))

    async def _failing_send(payload):
        raise ConnectionResetError("client RST'd")

    closed: list[bool] = []

    async def _track_close(*args, **kwargs):
        closed.append(True)

    ws.send = _failing_send
    ws.close = _track_close

    server = MagicMock()
    dispatch = MagicMock()

    # Must not raise — the ConnectionResetError is suppressed.
    await sidecar_ws._handle_connection(ws, server, dispatch)

    assert closed == [True], (
        "websocket.close must run even if websocket.send raised (the "
        "contextlib.suppress(Exception) around the send is the contract "
        "that the authoritative teardown always runs)"
    )
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_successful_auth_does_not_emit_auth_failed(monkeypatch) -> None:
    """Sanity check: a successful auth does NOT send an auth_failed frame.

    Locks in the contract that the auth_failed emission is gated on
    auth rejection — a future refactor that accidentally moves the
    emit outside the ``if not await _authenticate(...)`` branch would
    break this test.
    """
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "good-token")
    ws = _make_fake_websocket(json.dumps({"type": "auth", "token": "good-token"}))

    # Park the dispatch loop so _handle_connection doesn't exit before
    # we can inspect the post-auth state.
    class _EmptyAsyncIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    ws.__aiter__ = lambda self: _EmptyAsyncIter()  # noqa: E731

    # Make send / close no-op coroutines so the writer task doesn't
    # error out.
    sent_frames: list[str] = []

    async def _track_send(payload):
        sent_frames.append(payload)

    async def _no_close(*args, **kwargs):
        return None

    ws.send = _track_send
    ws.close = _no_close

    # Build a fake server with the attributes _handle_connection
    # accesses post-auth (lock, _ready_emitted, app.tray, push).
    server = MagicMock()
    server._ready_emitted = True  # skip the ready emit
    server._lock = MagicMock()
    server._lock.__enter__ = MagicMock(return_value=None)
    server._lock.__exit__ = MagicMock(return_value=False)
    server.app.tray._state = None  # skip the state_changed emit
    server.push = MagicMock()
    dispatch = MagicMock()

    # Cleanup exceptions from the writer task teardown are
    # acceptable — we only care that no auth_failed frame was sent.
    with contextlib.suppress(Exception):
        await sidecar_ws._handle_connection(ws, server, dispatch)

    auth_failed_frames = [f for f in sent_frames if "auth_failed" in f]
    assert auth_failed_frames == [], f"successful auth must NOT send an auth_failed frame, got {auth_failed_frames}"
