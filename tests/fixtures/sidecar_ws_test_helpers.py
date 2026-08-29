"""Shared fake-server factory for the sidecar WS test suite.

This module owns the SINGLE canonical ``_make_fake_server`` helper used
by every test file that exercises :mod:`voice_typer.server.sidecar_ws`.
Before this module existed, six inline copies of the helper were
sprinkled across the test tree (``tests/tauri/test_sidecar_ws_unit.py``,
``tests/tauri/mig15/test_ws_hmac_windows.py``,
``tests/tauri/mig16/test_ws_hmac_macos.py``,
``tests/tauri/mig17/test_ws_hmac_linux.py``,
``tests/test_ipc_error_envelope_parity.py``, and
``tests/test_sidecar_ws_thread_safety.py``). Three of those copies had
diverged from the canonical shape and were missing the
``_ws_dispatch_pool = None`` fix — a stale copy that re-introduces the
``wrap_future`` assertion failure anytime a new
test copies the wrong helper.

Centralising the factory here means future additions to
:func:`sidecar_ws._make_dispatch` (e.g. a new ``getattr(server, "_ws_*",
None) is None`` lazy-create branch) only need to update ONE place —
this module — and every sidecar WS test picks up the fix
automatically.

The helper is intentionally a superset of every prior inline copy: it
sets every attribute any of the six call sites ever needed, so each
test file can drop its inline definition and ``import`` this one
without further per-test configuration. Tests that need a different
``_dispatch`` return value (e.g. ``side_effect=RuntimeError("boom")``)
override it after calling ``_make_fake_server()``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import MagicMock


def _make_fake_server() -> MagicMock:
    """Build a fake IPCServer with the attributes _make_dispatch / _handle_connection need.

    ``_make_dispatch`` now uses
    ``loop.run_in_executor(server._ws_dispatch_pool, ...)`` (G4-H-30 —
    dedicated thread pool for WS dispatch, separate from the default
    executor). A MagicMock attribute access auto-vivifies a child
    MagicMock, so ``getattr(server, "_ws_dispatch_pool", None)`` returns
    a non-None MagicMock — the lazy-create branch in ``_make_dispatch``
    is skipped, and the MagicMock is passed to
    ``loop.run_in_executor``. ``asyncio.futures.wrap_future`` then
    asserts the submit() return is a real ``concurrent.futures.Future``
    and fails on the MagicMock.

    The same auto-vivification trap also affects the three sibling
    lazy-create attrs introduced by the WS-dispatch drain work
    (``_ws_inflight_count``, ``_ws_inflight_lock``, ``_ws_drained_event``):
    ``getattr(server, "_ws_inflight_count", None) is None`` returns
    ``False`` on a MagicMock (the auto-vivified child mock is not
    ``None``), so the lazy-create branch is skipped and the child mock
    is later compared with ``<= 0`` — a ``TypeError``. All four attrs
    are explicitly set to ``None`` here so the lazy-create branches in
    ``_make_dispatch`` run and install real ``ThreadPoolExecutor`` /
    ``threading.Lock`` / ``threading.Event`` / ``int`` instances.

    Fix: explicitly set ``server._ws_dispatch_pool = None`` (and the
    other three lazy-create attrs) so the lazy-create branches run and
    create real concurrency primitives. The executor / lock / event /
    counter are shared across calls on the same server (so cleanup is
    the test's responsibility — we let them leak at process exit, which
    is fine for a unit test).

    Tests that exercise ``_handle_connection`` (not just
    ``_make_dispatch``) also need:

    - ``_ready_emitted = True`` — skips the post-auth ``ready`` event
      emission so the spied ``event_bus.publish`` doesn't see a stray
      ``ready`` from setup (and so the writer task doesn't try to
      serialise one before the test's own publish burst begins).
    - ``server.app.tray._state = None`` — skips the initial
      ``state_changed`` emission in ``_install_subscriber``. Without
      this, ``getattr(server.app.tray, "_state", None)`` returns an
      auto-vivified MagicMock (truthy), the ``state_changed`` event is
      published with a MagicMock ``status`` value, and the writer
      task's ``json.dumps(event)`` blows up with
      ``TypeError: Object of type MagicMock is not JSON serializable``.
    - ``server.push = MagicMock()`` — defensive: matches the original
      ``tests/test_sidecar_ws_thread_safety.py`` helper. ``server.push``
      is not currently called by ``sidecar_ws`` (the WS writer
      subscribes to ``event_bus`` instead), but pre-creating it keeps
      the mock's call log clean if a future refactor adds a
      ``server.push`` call site.
    """
    server = MagicMock()
    server._dispatch = MagicMock(return_value={"type": "result", "data": {"ok": True}})
    server.app = MagicMock()
    server.app.quit = MagicMock()
    # force the lazy-create branches in
    # ``_make_dispatch`` to run (they create a real ThreadPoolExecutor,
    # threading.Lock, threading.Event, and int counter). If we leave
    # any of these unset, MagicMock auto-vivifies a child mock that
    # either fails the ``wrap_future`` isinstance assertion
    # (``_ws_dispatch_pool``) or the ``<= 0`` comparison
    # (``_ws_inflight_count``).
    server._ws_dispatch_pool = None
    server._ws_drained_event = None
    server._ws_inflight_lock = None
    server._ws_inflight_count = None
    # ``server.app._shutting_down`` is checked by the cooperative
    # shutdown gate in ``_make_dispatch`` BEFORE the rate-limit
    # check. On a MagicMock, ``getattr(server.app, "_shutting_down",
    # False)`` returns an auto-vivified child mock (truthy, NOT
    # ``is True``) and the gate fires — every dispatch returns
    # ``server.shutting_down`` instead of reaching the rate-limit /
    # handler path. Pin it to ``False`` so the dispatch body runs
    # (and the rate-limit / readonly / state-mutating branches
    # exercise as designed).
    server.app._shutting_down = False
    # For tests that exercise ``_handle_connection`` (not just
    # ``_make_dispatch``): skip the post-auth ``ready`` emission and
    # the initial ``state_changed`` emission (the latter would
    # otherwise publish a MagicMock value that fails JSON
    # serialization in the writer task).
    server.push = MagicMock()
    server._ready_emitted = True
    server.app.tray._state = None
    server.app.tray._message = ""
    return server


__all__ = ["_make_fake_server"]

# ─── Canonical fake websocket / fake-server factories ─────────────────
#
# Before this section existed, every file in the ``test_sidecar_ws*``
# family rebuilt its own fake websocket (and, for the connection-cap
# tests, its own semaphore-bearing fake server) inline — eight copies
# of the recv-driven fake, two copies of the read-loop fake, two copies
# of the semaphore server, all drifting independently. The factories
# below are the SINGLE canonical shape: each one is a superset of every
# inline copy it replaces (it records sends/closes and pins the
# ``closed`` flag even where a particular test never asserts on them,
# which is harmless — tests that need different send/close behaviour
# overwrite the attributes after construction, exactly as they did
# against the inline copies).

# Peer address reported by every fake websocket. Tests only rely on it
# being a concrete (host, port) tuple for logging / duplicate-probe
# paths — the exact values are arbitrary.
_FAKE_PEER_ADDRESS = ("127.0.0.1", 12345)


class _BlockingAsyncIter:
    """Async iterator that never yields — parks the dispatch loop.

    The ``async for raw in websocket:`` loop in ``_handle_connection``
    blocks on ``__anext__`` until the connection task is cancelled, so
    tests can hold the connection OPEN (writer task + ``_push_to_ws``
    subscriber installed) while publishing events from other threads.
    """

    def __aiter__(self) -> _BlockingAsyncIter:
        return self

    async def __anext__(self) -> str:
        # Never resolves — the only way out is cancellation, which
        # raises CancelledError (a BaseException, NOT caught by the
        # connection's ``except Exception:`` clause, so the ``finally:``
        # cleanup still runs).
        await asyncio.Future()
        raise StopAsyncIteration  # pragma: no cover - unreachable


class _EmptyAsyncIter:
    """Async iterator that ends immediately — clean disconnect."""

    def __aiter__(self) -> _EmptyAsyncIter:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


def make_fake_websocket(
    auth_frame: str | bytes | dict | None = None,
    *,
    yield_before_recv: bool = False,
) -> MagicMock:
    """Build the canonical recv-driven fake websocket.

    Replaces the inline ``_make_fake_websocket`` copies from
    ``tests/test_sidecar_ws_auth_failed.py``,
    ``tests/test_sidecar_ws_connection_cap.py``,
    ``tests/test_sidecar_ws_races.py``, and
    ``tests/test_sidecar_ws_protocol_version.py`` (a strict superset of
    all four).

    Behaviour:

    - ``auth_frame`` is returned by the first (and every) ``recv()``
      call, consumed by ``_authenticate``:
      * ``dict`` → JSON-encoded bytes (protocol-version tests);
      * ``str`` → UTF-8 encoded bytes;
      * ``bytes`` → used verbatim (invalid-JSON tests);
      * ``None`` → ``recv()`` parks on a never-resolving future so the
        auth path appears to hang (used to verify the semaphore
        rejection / duplicate probe run BEFORE auth).
    - ``yield_before_recv=True`` makes ``recv()`` yield once (via
      ``asyncio.sleep(0)``) before returning the auth frame. This
      simulates the I/O yield a real ``websockets`` recv performs and
      is required by the concurrent-cap race tests: without the yield,
      the winning connection completes its entire auth + release before
      the loser's acquire task runs, so the race window never opens.
    - ``send`` / ``close`` are recorded into ``ws._sent_frames`` /
      ``ws._closed_with`` so tests can assert call order and args.
    - ``ws.closed = False`` — the ``websockets`` library exposes an int
      (0=open) there; the duplicate-auth probe treats
      ``not bool(existing.closed)`` as "is open", so the mock presents
      as OPEN by default.
    - ``remote_address`` is a concrete (host, port) tuple.

    Tests that need different send/close/iterator behaviour overwrite
    the attributes after construction (same as with the inline copies).
    """
    ws = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS

    if auth_frame is None:

        async def _parked_recv() -> bytes:
            await asyncio.Future()

        ws.recv = _parked_recv
    else:
        if isinstance(auth_frame, dict):
            auth_bytes = json.dumps(auth_frame).encode()
        elif isinstance(auth_frame, str):
            auth_bytes = auth_frame.encode()
        else:
            auth_bytes = auth_frame

        async def _fake_recv() -> bytes:
            if yield_before_recv:
                # Yield once to simulate the I/O wait a real
                # ``websockets`` recv performs, giving concurrent
                # acquire tasks a chance to run before the winner
                # releases.
                await asyncio.sleep(0)
            return auth_bytes

        ws.recv = _fake_recv

    sent_frames: list[str] = []
    closed_with: list[tuple[tuple, dict]] = []

    async def _track_send(payload: str) -> None:
        sent_frames.append(payload)

    async def _track_close(*args: object, **kwargs: object) -> None:
        closed_with.append((args, kwargs))

    ws.send = _track_send
    ws.close = _track_close
    ws._sent_frames = sent_frames
    ws._closed_with = closed_with
    ws.closed = False
    return ws


def make_fake_websocket_for_read_loop(frames: list[str]) -> tuple[MagicMock, list[str]]:
    """Build the canonical async-iter fake websocket that yields *frames*.

    Replaces the duplicated ``_make_fake_websocket_for_read_loop`` in
    ``tests/test_sidecar_ws_permissions_fixes.py`` and
    ``tests/test_sidecar_ws.py``. ``_read_loop`` consumes the websocket
    via ``async for raw in websocket:``, so the fake implements
    ``__aiter__`` yielding *frames* then ending (clean disconnect).

    Returns ``(ws, sent)`` where ``sent`` collects every payload passed
    to ``websocket.send`` so tests can assert acks vs. drops.
    """
    ws = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS

    async def _aiter():
        for frame in frames:
            yield frame

    ws.__aiter__ = lambda self: _aiter()  # noqa: E731

    sent: list[str] = []

    async def _send(payload: str) -> None:
        sent.append(payload)

    ws.send = _send

    async def _close(*args: object, **kwargs: object) -> None:
        return None

    ws.close = _close
    return ws, sent


def make_fake_server_with_semaphore(value: int) -> MagicMock:
    """Build the canonical fake server whose semaphore has *value* slots.

    Replaces the duplicated ``_make_server_with_semaphore`` in
    ``tests/test_sidecar_ws_races.py`` and
    ``tests/test_sidecar_ws_connection_cap.py``.

    *value* == ``sidecar_ws._MAX_WS_CONNECTIONS`` → full budget (under cap);
    *value* == 0 → exhausted (at cap, all slots held);
    *value* == 1 → one slot remaining (the connection-cap TOCTOU window).

    The lock is a REAL ``threading.RLock`` (MagicMock auto-vivifies
    ``_lock`` as a child mock, which cannot be used as a synchronous
    context manager; RLock works everywhere the production ``Lock``
    works and additionally tolerates re-entrancy). The active-connection
    slot is pinned to ``None`` so the first duplicate-auth probe sees no
    existing connection — explicit is better than implicit for race
    tests (on a raw MagicMock the probe treats the auto-vivified child
    as "closed", which happens to work but is accidental).
    """
    server = MagicMock()
    # asyncio.Semaphore(n) starts with n available slots; Semaphore(0)
    # simulates "at cap".
    server._ws_connection_semaphore = asyncio.Semaphore(value)
    server._lock = threading.RLock()
    server._ready_emitted = True  # skip the ready emit
    server.app.tray._state = None  # skip the state_changed emit
    server.push = MagicMock()
    server._active_ws_connection = None
    return server


def make_real_server_for_graceful_shutdown() -> MagicMock:
    """Build a MagicMock IPCServer for ``_attach_ws_graceful_shutdown``.

    Moved verbatim from ``tests/test_sidecar_ws.py``. A raw ``MagicMock``
    returns truthy children for any ``getattr``, which short-circuits
    ``_attach_ws_graceful_shutdown``'s idempotency guard — pre-setting
    the attributes to real values (``False``, empty sets) lets the
    install actually run.
    """
    server = MagicMock()
    server._ws_graceful_shutdown_installed = False
    server._ws_authenticated_conns = set()
    server._ws_dispatch_futures = set()
    return server


def make_fake_websocket_for_close() -> MagicMock:
    """Build a fake websocket whose ``close`` records its call args.

    Moved verbatim from ``tests/test_sidecar_ws.py``. Used by the
    graceful-shutdown tests to assert close code/reason per connection.
    """
    ws = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS
    close_calls: list[tuple[tuple, dict]] = []

    async def _track_close(*args: object, **kwargs: object) -> None:
        close_calls.append((args, kwargs))

    ws.close = _track_close
    ws._close_calls = close_calls  # type: ignore[attr-defined]
    return ws


def make_fake_websocket_parked_after_auth(auth_token: str, *, park_dispatch: bool) -> MagicMock:
    """Build a fake websocket that authenticates with *auth_token* then either
    parks the dispatch loop forever (``park_dispatch=True``) or ends it
    immediately (``park_dispatch=False``).

    Consolidates the post-auth fakes from
    ``tests/test_sidecar_ws_thread_safety.py`` (blocking — holds the
    connection open so the writer task / ``_push_to_ws`` subscriber stay
    installed while events are published from other threads) and
    ``tests/test_sidecar_ws_ready_ordering.py`` (empty — immediate clean
    disconnect after auth + ready emit).

    The first ``recv()`` returns the auth frame (consumed by
    ``_authenticate``); the ``async for`` dispatch iterator is either
    blocking or empty. ``send`` is a no-op coroutine and ``close`` a
    plain ``MagicMock`` — tests that need tracking senders overwrite
    ``ws.send`` after construction.
    """
    ws = MagicMock()
    auth_frame = json.dumps({"type": "auth", "token": auth_token}).encode()

    async def _fake_recv() -> bytes:
        return auth_frame

    ws.recv = _fake_recv
    ws.close = MagicMock()
    ws.remote_address = _FAKE_PEER_ADDRESS
    # MagicMock wraps an assigned magic-method function as a method on
    # the type, so the lambda receives ``self`` (the mock instance) as
    # its first positional arg. We accept and ignore it.
    if park_dispatch:
        ws.__aiter__ = lambda self: _BlockingAsyncIter()  # noqa: E731
    else:
        ws.__aiter__ = lambda self: _EmptyAsyncIter()  # noqa: E731

    async def _noop_send(*args: object, **kwargs: object) -> None:
        return None

    ws.send = _noop_send
    return ws


__all__ = [
    "_make_fake_server",
    "make_fake_server_with_semaphore",
    "make_fake_websocket",
    "make_fake_websocket_for_close",
    "make_fake_websocket_for_read_loop",
    "make_fake_websocket_parked_after_auth",
    "make_real_server_for_graceful_shutdown",
]
