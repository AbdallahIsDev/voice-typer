"""Regression tests for the three Medium findings:

* **IN-34**: ``sidecar_ws._read_loop``'s heartbeat fast-path bypassed
  the ADR-0019 per-frame rate limiter (the limiter lives inside
  ``_make_dispatch``, which the fast-path skips to keep ack latency
  at the WS round-trip). A hostile or buggy client could spam
  ``{"type":"heartbeat"}`` at line rate and starve the event loop
  with ack sends. Fix: a cheap per-connection sliding-window cap
  (max ``_HEARTBEAT_RATE_MAX_PER_WINDOW`` (100) heartbeats per
  ``_HEARTBEAT_RATE_WINDOW_SECONDS`` (10s)) drops the flood WITHOUT
  acking.

* **IN-35**: ``sidecar_ws._start_writer._writer`` did
  ``raw = json.dumps(event, ensure_ascii=False)`` inline on the
  asyncio loop thread — for near-cap frames (~1 MiB) at 1-5 Hz that
  was 50-100 ms of pure CPU stalling every other connection's reads
  + the heartbeat fast-path. It also did ``await websocket.send(raw)``
  without a timeout, so a wedged peer (full TCP send buffer, half-open
  socket) tied up the writer task (and the loop thread) forever.
  Fix: offload ``json.dumps`` to ``loop.run_in_executor(None, ...)``
  and wrap ``websocket.send`` in
  ``asyncio.wait_for(..., timeout=_WS_SEND_TIMEOUT_SECONDS)`` (5s);
  on timeout, close the connection so the host's reconnect path
  takes over.

* **IN-36**: ``permissions.check_permissions_payload`` (104 lines)
  and its helper ``permission_probe_error_payload`` were dead code
  with a misleading docstring claiming they were the canonical entry
  point for the ``onboarding_check_permissions`` IPC handlers — but
  the actual handler calls
  ``OnboardingController().check_permissions()`` (different function,
  different module, different i18n strategy). Fix: delete both
  helpers; update ``OnboardingController.check_permissions()``'s
  docstring to declare it the canonical entry point.

The tests use a fake websocket + a real ``asyncio.Queue`` so the
``_writer`` coroutine is exercised exactly as production runs it
(no MagicMock auto-vivification hiding contract violations).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

# ─── IN-34: heartbeat fast-path rate cap ────────────────────────────


def _make_fake_websocket_for_read_loop(frames: list[str]) -> tuple[MagicMock, list[str]]:
    """Build a fake websocket that yields the given frames then closes.

    Returns ``(ws, sent)`` where ``sent`` is a list collecting every
    string passed to ``websocket.send`` so the test can assert on
    acks vs. drops.
    """
    ws = MagicMock()
    ws.remote_address = ("127.0.0.1", 54321)

    async def _aiter():
        for f in frames:
            yield f

    ws.__aiter__ = lambda self: _aiter()  # noqa: E731

    sent: list[str] = []

    async def _send(payload):
        sent.append(payload)

    ws.send = _send

    async def _close(*args, **kwargs):
        return None

    ws.close = _close
    return ws, sent


@pytest.mark.asyncio
async def test_in34_heartbeat_rate_cap_drops_flood_beyond_limit() -> None:
    """IN-34: a flood of ``heartbeat`` frames beyond the per-window cap
    is silently dropped (no ack sent) instead of fanning out acks.

    The cap is ``_HEARTBEAT_RATE_MAX_PER_WINDOW`` (100) per
    ``_HEARTBEAT_RATE_WINDOW_SECONDS`` (10s). We send 100 + 5 = 105
    heartbeats back-to-back; the first 100 must be acked, the last 5
    must be dropped (no ``heartbeat_ack`` for them).
    """
    cap = sidecar_ws._HEARTBEAT_RATE_MAX_PER_WINDOW
    assert cap == 100, f"IN-34: _HEARTBEAT_RATE_MAX_PER_WINDOW must be 100 (per finding); got {cap}"
    window = sidecar_ws._HEARTBEAT_RATE_WINDOW_SECONDS
    assert window == 10.0, f"IN-34: _HEARTBEAT_RATE_WINDOW_SECONDS must be 10.0 (per finding); got {window}"

    flood = [json.dumps({"type": "heartbeat"}) for _ in range(cap + 5)]
    ws, sent = _make_fake_websocket_for_read_loop(flood)

    server = MagicMock()
    dispatch = MagicMock(return_value=None)

    await sidecar_ws._read_loop(ws, server, dispatch)

    # Every sent frame must be a heartbeat_ack (no other frames here).
    acks = [s for s in sent if "heartbeat_ack" in s]
    # Exactly `cap` acks — the 5 overflow frames must be dropped.
    assert len(acks) == cap, (
        f"IN-34: expected {cap} heartbeat_acks (the cap), got {len(acks)} — the rate cap is not dropping the overflow"
    )
    # The dispatch pool must NOT have been invoked (heartbeat fast-path
    # bypasses dispatch — verified by zero ``dispatch`` calls).
    assert dispatch.call_count == 0, (
        f"IN-34: heartbeat fast-path must NOT reach dispatch(); got {dispatch.call_count} calls"
    )


@pytest.mark.asyncio
async def test_in34_heartbeat_rate_cap_allows_legitimate_rate() -> None:
    """IN-34: a well-behaved host sending 1 heartbeat per 10s is never
    dropped. We send 5 heartbeats (well under the 100/10s cap) and
    verify all 5 are acked.

    This guards against a regression where the cap is mis-tuned (e.g.
    someone drops it to 1/10s and breaks the legitimate host).
    """
    frames = [json.dumps({"type": "heartbeat", "id": i}) for i in range(5)]
    ws, sent = _make_fake_websocket_for_read_loop(frames)

    server = MagicMock()
    dispatch = MagicMock(return_value=None)

    await sidecar_ws._read_loop(ws, server, dispatch)

    acks = [s for s in sent if "heartbeat_ack" in s]
    assert len(acks) == 5, (
        f"IN-34: legitimate-rate heartbeats (5 < 100/10s) must all be acked; got {len(acks)} acks for 5 frames"
    )


@pytest.mark.asyncio
async def test_in34_heartbeat_rate_cap_window_evicts_old_entries() -> None:
    """IN-34: the sliding window evicts entries older than the window,
    so a burst followed by a wait followed by another burst does not
    trip the cap on the second burst.

    We use ``time.monotonic`` patching to simulate: 100 frames at
    t=0, then 5 frames at t=11 (past the 10s window). All 105 must
    be acked — the first 100 fill the window, the window slides past
    them at t=11, the next 5 are within the new budget.
    """

    cap = sidecar_ws._HEARTBEAT_RATE_MAX_PER_WINDOW
    window = sidecar_ws._HEARTBEAT_RATE_WINDOW_SECONDS

    # Patch ``time.monotonic`` used INSIDE ``sidecar_ws`` (the module
    # does ``import time`` then ``time.monotonic()``).
    fake_clock = {"t": 0.0}

    import voice_typer.server.sidecar_ws as sw

    original_monotonic = sw.time.monotonic

    def _fake_monotonic():
        return fake_clock["t"]

    sw.time.monotonic = _fake_monotonic
    try:
        # 100 frames at t=0 (the cap).
        frames = [json.dumps({"type": "heartbeat"}) for _ in range(cap)]
        # 5 more frames at t=11 (past the 10s window — old entries
        # must be evicted).
        frames_with_time_jump = [(f, 0.0) for f in frames]
        frames_with_time_jump += [(json.dumps({"type": "heartbeat"}), window + 1.0) for _ in range(5)]

        # We need to interleave frame reads with time advances. The
        # simplest way: yield each frame, advance the clock before the
        # next ``send``.
        ws = MagicMock()
        ws.remote_address = ("127.0.0.1", 54321)

        async def _aiter():
            for f, t in frames_with_time_jump:
                fake_clock["t"] = t
                yield f

        ws.__aiter__ = lambda self: _aiter()  # noqa: E731

        sent: list[str] = []

        async def _send(payload):
            sent.append(payload)

        ws.send = _send

        async def _close(*args, **kwargs):
            return None

        ws.close = _close

        server = MagicMock()
        dispatch = MagicMock(return_value=None)

        await sidecar_ws._read_loop(ws, server, dispatch)

        acks = [s for s in sent if "heartbeat_ack" in s]
        assert len(acks) == cap + 5, (
            f"IN-34: window eviction broken — expected {cap + 5} acks "
            f"(100 at t=0 + 5 at t=11 with 10s window), got {len(acks)}. "
            f"The sliding window is not popping old entries."
        )
    finally:
        sw.time.monotonic = original_monotonic


def test_in34_constants_exist() -> None:
    """IN-34: the heartbeat rate cap constants must exist on the module."""
    assert hasattr(sidecar_ws, "_HEARTBEAT_RATE_MAX_PER_WINDOW"), (
        "IN-34: _HEARTBEAT_RATE_MAX_PER_WINDOW constant must exist on sidecar_ws"
    )
    assert hasattr(sidecar_ws, "_HEARTBEAT_RATE_WINDOW_SECONDS"), (
        "IN-34: _HEARTBEAT_RATE_WINDOW_SECONDS constant must exist on sidecar_ws"
    )


def test_in34_read_loop_uses_rate_cap() -> None:
    """IN-34: the ``_read_loop`` source must reference the rate cap
    constants (structural check — guards against the rate-cap block
    being accidentally deleted in a future refactor)."""
    src = inspect.getsource(sidecar_ws._read_loop)
    assert "_HEARTBEAT_RATE_MAX_PER_WINDOW" in src, "IN-34: _read_loop must reference _HEARTBEAT_RATE_MAX_PER_WINDOW"
    assert "_HEARTBEAT_RATE_WINDOW_SECONDS" in src, "IN-34: _read_loop must reference _HEARTBEAT_RATE_WINDOW_SECONDS"
    assert "heartbeat_window" in src, "IN-34: _read_loop must declare a heartbeat_window sliding-window deque"


# ─── IN-35: writer offloads json.dumps + send timeout ──────────────


def test_in35_constants_exist() -> None:
    """IN-35: the WS send timeout constant must exist on the module."""
    assert hasattr(sidecar_ws, "_WS_SEND_TIMEOUT_SECONDS"), (
        "IN-35: _WS_SEND_TIMEOUT_SECONDS constant must exist on sidecar_ws"
    )
    assert sidecar_ws._WS_SEND_TIMEOUT_SECONDS == 5.0, (
        f"IN-35: _WS_SEND_TIMEOUT_SECONDS must be 5.0 (per finding); got {sidecar_ws._WS_SEND_TIMEOUT_SECONDS}"
    )


def test_in35_writer_uses_executor_for_json_dumps() -> None:
    """IN-35: the ``_writer`` coroutine must offload ``json.dumps`` to
    the default executor via ``loop.run_in_executor`` (structural
    check on the source)."""
    src = inspect.getsource(sidecar_ws._start_writer)
    assert "run_in_executor" in src, (
        "IN-35: _start_writer._writer must use loop.run_in_executor to offload json.dumps off the event loop thread"
    )
    assert "json.dumps" in src, (
        "IN-35: _start_writer._writer must still reference json.dumps (passed as the executor callable)"
    )


def test_in35_writer_wraps_send_in_wait_for_with_timeout() -> None:
    """IN-35: the ``websocket.send`` call must be wrapped in
    ``asyncio.wait_for(..., timeout=_WS_SEND_TIMEOUT_SECONDS)`` and
    the writer must close the connection on ``TimeoutError``."""
    src = inspect.getsource(sidecar_ws._start_writer)
    assert "asyncio.wait_for" in src, "IN-35: _start_writer._writer must wrap websocket.send in asyncio.wait_for"
    assert "_WS_SEND_TIMEOUT_SECONDS" in src, "IN-35: _start_writer._writer must reference _WS_SEND_TIMEOUT_SECONDS"
    assert "TimeoutError" in src, "IN-35: _start_writer._writer must catch TimeoutError and close the connection"
    assert "websocket.close" in src, "IN-35: _start_writer._writer must close the connection on send timeout"


@pytest.mark.asyncio
async def test_in35_writer_offloads_json_dumps_to_executor() -> None:
    """IN-35: integration test — ``_writer`` produces the same JSON
    payload via ``run_in_executor`` as the inline ``json.dumps`` did.

    Uses a real ``asyncio.Queue`` and a fake websocket that records
    every sent frame. The recorded frame must equal
    ``json.dumps(event, ensure_ascii=False)``.
    """
    outbound: asyncio.Queue = asyncio.Queue()
    event = {"type": "test_event", "data": {"nested": [1, 2, 3], "unicode": "héllo"}}
    expected_raw = json.dumps(event, ensure_ascii=False)

    ws = MagicMock()
    sent: list[str] = []

    async def _send(payload):
        sent.append(payload)

    ws.send = _send

    async def _close(*args, **kwargs):
        return None

    ws.close = _close

    writer_task = sidecar_ws._start_writer(ws, outbound)
    await outbound.put(event)
    # Give the writer one loop tick to drain the queue.
    for _ in range(5):
        await asyncio.sleep(0)
        if sent:
            break
    # Sentinel to stop the writer.
    await outbound.put(None)
    try:
        await asyncio.wait_for(writer_task, timeout=2.0)
    except TimeoutError:
        writer_task.cancel()

    assert len(sent) == 1, f"IN-35: expected one send, got {sent!r}"
    assert sent[0] == expected_raw, (
        f"IN-35: writer via run_in_executor must produce identical JSON to "
        f"inline json.dumps; got {sent[0]!r} vs expected {expected_raw!r}"
    )


@pytest.mark.asyncio
async def test_in35_writer_closes_connection_on_send_timeout() -> None:
    """IN-35: when ``websocket.send`` exceeds the send timeout, the
    writer must close the connection (code=1011, reason="send timeout")
    and exit.

    We simulate the timeout by making ``websocket.send`` block forever
    (never resolves) and lowering ``_WS_SEND_TIMEOUT_SECONDS`` to a
    short test value (0.05s) via monkeypatching the module attribute.
    """
    outbound: asyncio.Queue = asyncio.Queue()

    ws = MagicMock()
    close_calls: list[tuple[tuple, dict]] = []

    async def _blocking_send(payload):
        # Block forever — simulates a wedged peer.
        await asyncio.Event().wait()

    async def _track_close(*args, **kwargs):
        close_calls.append((args, kwargs))

    ws.send = _blocking_send
    ws.close = _track_close

    # Lower the timeout so the test doesn't take 5s.
    original_timeout = sidecar_ws._WS_SEND_TIMEOUT_SECONDS
    sidecar_ws._WS_SEND_TIMEOUT_SECONDS = 0.05
    try:
        writer_task = sidecar_ws._start_writer(ws, outbound)
        await outbound.put({"type": "test_event"})
        # Wait for the writer to time out + close. The timeout is 0.05s
        # + close handshake; allow up to 2s for the loop to schedule.
        try:
            await asyncio.wait_for(writer_task, timeout=2.0)
        except TimeoutError:
            writer_task.cancel()
            pytest.fail(
                "IN-35: writer did not exit within 2s after send timeout — "
                "the TimeoutError handler is not closing the connection"
            )

        assert len(close_calls) == 1, (
            f"IN-35: writer must call websocket.close() exactly once on send timeout; got {len(close_calls)} calls"
        )
        args, kwargs = close_calls[0]
        assert kwargs.get("code") == 1011, f"IN-35: close code must be 1011 (internal error); got {kwargs}"
        assert kwargs.get("reason") == "send timeout", f"IN-35: close reason must be 'send timeout'; got {kwargs}"
    finally:
        sidecar_ws._WS_SEND_TIMEOUT_SECONDS = original_timeout


# ─── IN-36: dead code removal ───────────────────────────────────────


def test_in36_check_permissions_payload_removed() -> None:
    """IN-36: ``permissions.check_permissions_payload`` must be deleted.

    It was 104 lines of dead code with a misleading docstring claiming
    it was the canonical entry point for the
    ``onboarding_check_permissions`` IPC handlers — but the actual
    handler calls ``OnboardingController().check_permissions()``
    (different function, different module, different i18n strategy).
    """
    from voice_typer.server import permissions

    assert not hasattr(permissions, "check_permissions_payload"), (
        "IN-36: permissions.check_permissions_payload must be deleted (dead code with misleading docstring)"
    )


def test_in36_permission_probe_error_payload_removed() -> None:
    """IN-36: ``permissions.permission_probe_error_payload`` must be
    deleted (it was only called from the now-deleted
    ``check_permissions_payload``)."""
    from voice_typer.server import permissions

    assert not hasattr(permissions, "permission_probe_error_payload"), (
        "IN-36: permissions.permission_probe_error_payload must be deleted "
        "(only caller was the dead check_permissions_payload)"
    )


def test_in36_onboarding_check_permissions_docstring_declares_canonical() -> None:
    """IN-36: ``OnboardingController.check_permissions``'s docstring
    must declare it the canonical entry point for the
    ``onboarding_check_permissions`` IPC handlers (replacing the
    misleading claim in the deleted ``check_permissions_payload``'s
    docstring)."""
    from voice_typer.server.onboarding import OnboardingController

    doc = OnboardingController.check_permissions.__doc__ or ""
    assert "canonical entry point" in doc.lower(), (
        "IN-36: OnboardingController.check_permissions docstring must declare "
        "it the canonical entry point (replacing the deleted "
        "check_permissions_payload's misleading claim)"
    )


def test_in36_onboarding_check_permissions_still_returns_correct_shape() -> None:
    """IN-36: regression — deleting ``check_permissions_payload`` must
    not break ``OnboardingController.check_permissions()``. Verify the
    method still returns a payload with the expected keys on a
    platform-agnostic stub."""
    from voice_typer.server import onboarding as onboarding_mod
    from voice_typer.server import permissions as perm_mod
    from voice_typer.server.onboarding import OnboardingController

    # Force the "unknown platform" branch so no platform-specific
    # probe runs — the method should still return a well-formed dict.
    onboarding_mod.is_windows = lambda: False  # type: ignore[attr-defined]
    onboarding_mod.is_macos = lambda: False  # type: ignore[attr-defined]
    onboarding_mod.is_linux = lambda: False  # type: ignore[attr-defined]
    perm_mod.is_windows = lambda: False  # type: ignore[attr-defined]
    perm_mod.is_macos = lambda: False  # type: ignore[attr-defined]
    perm_mod.is_linux = lambda: False  # type: ignore[attr-defined]

    controller = OnboardingController.__new__(OnboardingController)
    result = controller.check_permissions()

    assert isinstance(result, dict), f"IN-36: check_permissions must return a dict; got {type(result)}"
    for key in ("platform", "state", "needed", "instructions"):
        assert key in result, f"IN-36: check_permissions payload must include '{key}'; got keys {list(result.keys())}"
    assert result["platform"] == "unknown"
    assert result["needed"] is False
    assert result["instructions"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-o", "addopts="])
