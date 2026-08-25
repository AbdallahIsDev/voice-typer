"""DJ-9 regression: in-flight WS handler races DB teardown.

The bug (DJ-9)
--------------
When shutdown begins, the WebSocket handler can be mid-execution of a
``transcription_final`` DB write while ``shutdown_controller._do_cleanup``
closes the DB. The handler's write either silently fails or races
with ``close()``, losing the user's final transcription.

Pre-DJ-9 state of the code:

  1. ``sidecar_ws._make_dispatch`` had a ``_shutting_down`` gate that
     rejected NEW dispatches after the flag flipped (G4-H-30) — good.
  2. ``shutdown_controller._do_cleanup`` called
     ``ws_pool.shutdown(wait=False, cancel_futures=True)`` to cancel
     QUEUED dispatches + a 5s ``pool.shutdown(wait=True)`` join to
     wait for in-flight dispatches — good.
  3. BUT: ``pool.shutdown(wait=True)`` only guarantees the
     ``ThreadPoolExecutor`` worker queue has drained. It does NOT
     guarantee the per-dispatch coroutine BODY has finished its DB
     write — the Future resolves on ``server._dispatch`` return, but
     the WS ``dispatch`` coroutine may still be in its
     ``await loop.run_in_executor`` unwind / result-serialisation tail
     when the pool reports drained. That tail can race
     ``_teardown_history_db`` / ``_teardown_crash_recovery``,
     silently losing the final write.

The fix (DJ-9)
--------------
Add EXPLICIT ``threading.Event`` coordination between
``sidecar_ws._make_dispatch`` and ``shutdown_controller._do_cleanup``:

  - ``sidecar_ws._make_dispatch`` lazily attaches three attributes to
    the IPC server instance:
      * ``_ws_drained_event`` (``threading.Event``, initially SET) —
        set when no dispatch is in-flight.
      * ``_ws_inflight_lock`` (``threading.Lock``) — guards count +
        Event mutation as a pair.
      * ``_ws_inflight_count`` (``int``, initially 0) — number of
        dispatches currently between entry and exit of the
        ``dispatch`` coroutine body.
  - Each ``dispatch`` call: under the lock, increment count + clear
    the Event (so ``_do_cleanup`` knows to wait). After the dispatch
    body returns (in a ``finally`` block), under the lock, decrement
    count + set the Event if count drops to 0.
  - ``shutdown_controller._do_cleanup``: AFTER the existing
    ``pool.shutdown(wait=False, cancel_futures=True)`` + 5s join,
    additionally wait on ``_ws_drained_event`` with a 2s bounded
    timeout. If the Event does NOT fire (in-flight handler still
    mid-DB-write), log a WARNING and proceed — never block
    indefinitely on a single stuck handler.

The ``finally`` block guarantees the Event is set even if the
dispatch body raises (the in-flight count MUST be consistent with
the actual dispatch state, otherwise ``_do_cleanup`` would wait on
an Event that never fires — a deadlock).

This closes the race: ``_do_cleanup`` blocks on the Event until the
in-flight dispatch's coroutine body fully returns (including the
post-Future unwind), so ``_teardown_history_db`` cannot start until
the in-flight ``transcription_final`` write is done.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from unittest.mock import MagicMock

import pytest

websockets = pytest.importorskip("websockets")

from voice_typer.server import sidecar_ws  # noqa: E402

_SIDECAR_WS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "sidecar_ws.py",
)
# The ``_drain_ws_dispatch_pool`` body (the WS-drain half of
# ``_do_cleanup``'s early bookend) was extracted from the
# ``shutdown_controller/_cleanup.py`` leaf into
# ``shutdown/ws_drain.py``; the mixin method there is a thin delegate.
_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "ws_drain.py",
)


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _make_server_for_dispatch() -> MagicMock:
    """Build a MagicMock IPCServer with the rate-limiter plumbing
    that ``_make_dispatch`` needs to succeed.

    The lazy-init in ``_make_dispatch`` uses
    ``getattr(server, "_ws_dispatch_pool", None)`` to detect whether
    the pool / Event / lock / count have been attached yet. A bare
    ``MagicMock`` auto-vivifies a child MagicMock for ANY attribute
    access — including ``_ws_dispatch_pool`` — so the ``is None``
    check would never fire and the real Event / lock / pool would
    never be installed. We pre-set the four lazy-init slots to
    ``None`` so ``_make_dispatch``'s lazy-init branches actually
    execute (and attach the real primitives to the server).

    ``_get_rate_limiter(server)`` (called inside ``_make_dispatch``)
    has its own lazy-init for ``server._rate_limiter_instance`` — we
    let it construct a real ``_RateLimiter`` (its ``allow()`` returns
    True on the first call within the burst budget, which is what
    these tests need).
    """
    server = MagicMock()
    server.app = MagicMock()
    server.app._shutting_down = False
    # ``server._dispatch`` is the actual handler — make it a no-op
    # that returns ``{"type": "ok"}``.
    server._dispatch = MagicMock(return_value={"type": "ok"})
    # Pre-set the four lazy-init slots to None so _make_dispatch's
    # ``if getattr(server, ..., None) is None:`` branches actually fire
    # (otherwise MagicMock auto-vivifies a child MagicMock for the
    # attribute, which is NOT None, so the branch is skipped and the
    # real Event / lock / pool are never installed).
    server._ws_dispatch_pool = None
    server._ws_drained_event = None
    server._ws_inflight_lock = None
    server._ws_inflight_count = None
    return server


# source-level contracts ───────────────────────────────────


class TestSourceContracts:
    """DJ-9: source-level contract for the Event coordination."""

    def test_sidecar_ws_attaches_drained_event(self):
        """``sidecar_ws._make_dispatch`` must attach
        ``_ws_drained_event`` to the IPC server (lazily, on first
        dispatch)."""
        src = _src(_SIDECAR_WS_PATH)
        assert "_ws_drained_event" in src, (
            "DJ-9: sidecar_ws._make_dispatch must attach _ws_drained_event to the IPC server"
        )
        # The Event must be created via threading.Event().
        assert "threading.Event()" in src, "DJ-9: _ws_drained_event must be a threading.Event"

    def test_sidecar_ws_attaches_inflight_lock(self):
        """``sidecar_ws._make_dispatch`` must attach
        ``_ws_inflight_lock`` (a threading.Lock) to the IPC server."""
        src = _src(_SIDECAR_WS_PATH)
        assert "_ws_inflight_lock" in src, "DJ-9: sidecar_ws._make_dispatch must attach _ws_inflight_lock"

    def test_sidecar_ws_attaches_inflight_count(self):
        """``sidecar_ws._make_dispatch`` must attach
        ``_ws_inflight_count`` (an int) to the IPC server."""
        src = _src(_SIDECAR_WS_PATH)
        assert "_ws_inflight_count" in src, "DJ-9: sidecar_ws._make_dispatch must attach _ws_inflight_count"

    def test_sidecar_ws_clears_event_on_dispatch_entry(self):
        """The ``dispatch`` coroutine body MUST clear
        ``_ws_drained_event`` on entry (under the lock, after
        incrementing the count)."""
        src = _src(_SIDECAR_WS_PATH)
        # The increment + clear pair must be inside a ``with ws_inflight_lock:``
        # block.
        assert "with ws_inflight_lock:" in src, (
            "DJ-9: the count increment + Event.clear pair must be under ws_inflight_lock"
        )
        assert "ws_drained_event.clear()" in src, "DJ-9: dispatch must clear _ws_drained_event on entry"

    def test_sidecar_ws_sets_event_on_dispatch_exit(self):
        """The ``dispatch`` coroutine body MUST set
        ``_ws_drained_event`` on exit (in a ``finally`` block, under
        the lock, after decrementing the count and checking it's 0)."""
        src = _src(_SIDECAR_WS_PATH)
        assert "ws_drained_event.set()" in src, (
            "DJ-9: dispatch must set _ws_drained_event on exit (when in-flight count drops to 0)"
        )
        # The set() call must be inside a ``finally`` block so it
        # fires even if the dispatch body raised. There are TWO
        # ``ws_drained_event.set()`` calls in the source:
        #   1. The lazy-init block: ``ws_drained_event.set()  # initially
        #      drained — count is 0`` (NOT inside finally).
        #   2. The dispatch exit block: inside ``finally:``.
        # Use ``rfind`` to find the LAST occurrence — that's the
        # dispatch-exit one we care about.
        set_idx = src.rfind("ws_drained_event.set()")
        assert set_idx > -1
        # Find the nearest ``finally:`` before set_idx.
        nearest_finally = src.rfind("finally:", 0, set_idx)
        assert nearest_finally > -1, (
            "DJ-9: ws_drained_event.set() (the dispatch-exit call, NOT "
            "the lazy-init call) must be inside a finally block"
        )

    def test_shutdown_controller_waits_on_drained_event(self):
        """``shutdown_controller._do_cleanup`` MUST wait on
        ``_ws_drained_event`` (with a bounded timeout) AFTER the
        existing pool.shutdown + 5s join."""
        src = _src(_SHUTDOWN_CONTROLLER_PATH)
        assert "_ws_drained_event" in src, "DJ-9: shutdown_controller._do_cleanup must reference _ws_drained_event"
        # The wait must be bounded (timeout=...).
        assert "ws_drained_event.wait(timeout=" in src, (
            "DJ-9: _do_cleanup must call ws_drained_event.wait(timeout=...) — bounded wait, never blocks indefinitely"
        )

    def test_shutdown_controller_logs_on_drain_timeout(self):
        """If the Event wait times out (in-flight handler still
        mid-DB-write), ``_do_cleanup`` MUST log a WARNING (so
        operators can see the race) and proceed (never block
        indefinitely)."""
        src = _src(_SHUTDOWN_CONTROLLER_PATH)
        # Find the wait call and the surrounding drain-timeout branch.
        wait_idx = src.find("ws_drained_event.wait(timeout=")
        assert wait_idx > -1
        # Slice a generous window after the wait.
        block = src[wait_idx : wait_idx + 1200]
        assert "WS dispatch drain Event did not" in block, "the drain-timeout branch must log the un-drained Event race"
        assert "proceeding" in block, "the drain-timeout branch must proceed instead of blocking shutdown"
        assert "log.warning" in block, "the drain-timeout branch must log at WARNING level"


# behavioral — dispatch path ───────────────────────────────


class TestDispatchEventCoordination:
    """DJ-9: behavioral verification that ``_make_dispatch``'s
    ``dispatch`` coroutine coordinates via the Event."""

    def test_dispatch_clears_event_on_entry_and_sets_on_exit(self):
        """A single dispatch: ``_ws_drained_event`` is cleared on
        entry and re-set on exit (count drops to 0)."""
        server = _make_server_for_dispatch()
        dispatch = sidecar_ws._make_dispatch(server)

        assert server._ws_drained_event.is_set(), "DJ-9: _ws_drained_event must be initially SET (count is 0)"

        async def _run():
            result = await dispatch({"type": "test_command"}, websocket=None)
            return result

        result = asyncio.run(_run())

        assert result == {"type": "ok"}
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be SET after dispatch completes (count drops back to 0)"
        )
        assert server._ws_inflight_count == 0, "DJ-9: _ws_inflight_count must be 0 after dispatch completes"

    def test_event_stays_cleared_during_inflight_dispatch(self):
        """While a dispatch is in-flight (blocked on the handler),
        ``_ws_drained_event`` must NOT be set — ``_do_cleanup`` would
        otherwise proceed without waiting."""
        server = _make_server_for_dispatch()
        # Make ``server._dispatch`` block on an event so we can
        # observe the in-flight state.
        dispatch_unblock = threading.Event()

        def _blocking_dispatch(msg):
            dispatch_unblock.wait(timeout=5.0)
            return {"type": "ok"}

        server._dispatch = _blocking_dispatch
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "test_command"}, websocket=None)

        # Run the dispatch on a separate thread + its own loop so we
        # can observe state while it's in-flight.
        loop = asyncio.new_event_loop()
        dispatch_done = threading.Event()
        dispatch_result: list = []

        def _runner():
            try:
                dispatch_result.append(loop.run_until_complete(_run()))
            finally:
                loop.close()
                dispatch_done.set()

        t = threading.Thread(target=_runner, daemon=True)
        t.start()

        # Give the dispatch a moment to enter its body.
        time.sleep(0.2)

        # While in-flight: Event must be CLEARED, count must be 1.
        assert not server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be CLEARED while a dispatch is in-flight (so _do_cleanup knows to wait)"
        )
        assert server._ws_inflight_count == 1, "DJ-9: _ws_inflight_count must be 1 while a dispatch is in-flight"

        # Unblock the dispatch.
        dispatch_unblock.set()
        assert dispatch_done.wait(timeout=5.0), "DJ-9: dispatch must complete after unblock"

        # After completion: Event must be SET, count must be 0.
        assert server._ws_drained_event.is_set(), "DJ-9: _ws_drained_event must be SET after dispatch completes"
        assert server._ws_inflight_count == 0, "DJ-9: _ws_inflight_count must be 0 after dispatch completes"
        assert dispatch_result == [{"type": "ok"}]

    def test_event_set_on_exit_even_when_dispatch_raises(self):
        """If ``server._dispatch`` raises, the ``finally`` block MUST
        still set the Event (otherwise ``_do_cleanup`` would wait on
        an Event that never fires — a deadlock)."""
        server = _make_server_for_dispatch()
        server._dispatch = MagicMock(side_effect=RuntimeError("simulated DB write failure"))
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "test_command"}, websocket=None)

        result = asyncio.run(_run())

        # The dispatch must return an internal-error envelope (the
        # existing exception handler converts to a structured error).
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.internal_error"
        # Event MUST be re-set even though the dispatch
        # raised (the finally block guarantees this).
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be SET even when dispatch raises "
            "(finally block must run so _do_cleanup does not deadlock)"
        )
        assert server._ws_inflight_count == 0

    def test_concurrent_dispatches_count_correctly(self):
        """Two concurrent dispatches: count goes 0→1→2 on entry,
        2→1→0 on exit. Event must only be set when count drops to 0
        (NOT after the first dispatch exits)."""
        server = _make_server_for_dispatch()
        dispatch_unblock_a = threading.Event()
        dispatch_unblock_b = threading.Event()

        def _blocking_dispatch_a(msg):
            dispatch_unblock_a.wait(timeout=5.0)
            return {"type": "ok", "src": "a"}

        def _blocking_dispatch_b(msg):
            dispatch_unblock_b.wait(timeout=5.0)
            return {"type": "ok", "src": "b"}

        # Alternate between the two handlers based on msg marker.
        def _router(msg):
            if msg.get("marker") == "a":
                return _blocking_dispatch_a(msg)
            return _blocking_dispatch_b(msg)

        server._dispatch = _router
        dispatch = sidecar_ws._make_dispatch(server)

        loop = asyncio.new_event_loop()
        results: list = []
        done_events = [threading.Event(), threading.Event()]

        def _runner(idx, marker, unblock_ev):
            try:
                coro = dispatch({"type": "test_command", "marker": marker}, websocket=None)
                results.append((idx, loop.run_until_complete(coro)))
            finally:
                done_events[idx].set()

        # Run two dispatches on separate threads (sharing the loop).
        # Actually, asyncio is single-threaded per loop — we need
        # separate loops per dispatch.
        def _runner_with_own_loop(idx, marker, unblock_ev):
            own_loop = asyncio.new_event_loop()
            try:
                coro = dispatch({"type": "test_command", "marker": marker}, websocket=None)
                results.append((idx, own_loop.run_until_complete(coro)))
            finally:
                own_loop.close()
                done_events[idx].set()

        t_a = threading.Thread(target=_runner_with_own_loop, args=(0, "a", dispatch_unblock_a), daemon=True)
        t_b = threading.Thread(target=_runner_with_own_loop, args=(1, "b", dispatch_unblock_b), daemon=True)
        t_a.start()
        t_b.start()

        # Let both enter their dispatch bodies.
        time.sleep(0.3)

        # Both in-flight: count must be 2, Event must be CLEARED.
        assert server._ws_inflight_count == 2, (
            f"DJ-9: _ws_inflight_count must be 2 with both dispatches in-flight; got {server._ws_inflight_count}"
        )
        assert not server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be CLEARED while dispatches are in-flight"
        )

        # Unblock dispatch A only — count should drop to 1, Event
        # should STAY cleared (B is still in-flight).
        dispatch_unblock_a.set()
        assert done_events[0].wait(timeout=5.0)
        # Give the finally block a moment to run.
        time.sleep(0.1)

        assert server._ws_inflight_count == 1, (
            f"DJ-9: _ws_inflight_count must drop to 1 after A completes "
            f"(B still in-flight); got {server._ws_inflight_count}"
        )
        assert not server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must STAY cleared while B is still in-flight (count > 0)"
        )

        # Unblock B — count drops to 0, Event is set.
        dispatch_unblock_b.set()
        assert done_events[1].wait(timeout=5.0)
        time.sleep(0.1)

        assert server._ws_inflight_count == 0, "DJ-9: _ws_inflight_count must drop to 0 after both complete"
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be SET after the last dispatch completes (count drops to 0)"
        )

    def test_shutting_down_flag_rejects_new_dispatch(self):
        """When ``server.app._shutting_down`` is True, the dispatch
        must reject new requests with a ``server.shutting_down`` error
        WITHOUT touching the in-flight count (the request never
        entered the dispatch body)."""
        server = _make_server_for_dispatch()
        server.app._shutting_down = True
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "test_command"}, websocket=None)

        result = asyncio.run(_run())

        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        # The count must NOT have been incremented (the gate fires
        # before the increment).
        assert server._ws_inflight_count == 0, (
            "DJ-9: the _shutting_down gate must fire BEFORE the count "
            "increment (rejected requests do not count as in-flight)"
        )
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must remain SET when the request was rejected (no in-flight dispatch to wait for)"
        )

    def test_shutdown_message_bypasses_shutting_down_gate(self):
        """The ``shutdown`` message itself is exempt from the
        ``_shutting_down`` gate (the host sends it to TRIGGER
        shutdown). It must go through the dispatch body and update
        the in-flight count normally."""
        server = _make_server_for_dispatch()
        server.app._shutting_down = True  # gate is on
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "shutdown"}, websocket=None)

        result = asyncio.run(_run())

        # ``shutdown`` is not rejected — it flows through server._dispatch.
        assert result == {"type": "ok"}
        assert server._ws_inflight_count == 0, "DJ-9: after the shutdown dispatch completes, count must be 0"
        assert server._ws_drained_event.is_set()


# behavioral — _do_cleanup waits on Event ──────────────────


class TestDoCleanupWaitsOnEvent:
    """DJ-9: ``_do_cleanup`` must wait on ``_ws_drained_event`` with
    a bounded timeout BEFORE tearing down the DB."""

    def test_do_cleanup_returns_quickly_when_event_already_set(self):
        """If ``_ws_drained_event`` is already set (no in-flight
        dispatch), ``_do_cleanup`` must NOT block on the wait — the
        Event.wait returns immediately."""
        from voice_typer.server.shutdown_controller import ShutdownController

        # Build a controller with a fake IPC server that has the
        # Event pre-set (simulating "no in-flight dispatch").
        app = MagicMock()
        app._cleanup_done = False
        app._shutting_down = True
        app._shutting_down_event = MagicMock()
        app._shutting_down_event.set = MagicMock()

        ipc_server = MagicMock()
        ws_pool = MagicMock()
        ws_pool.shutdown = MagicMock()  # synchronous no-op
        ipc_server._ws_dispatch_pool = ws_pool

        ws_drained_event = threading.Event()
        ws_drained_event.set()  # no in-flight dispatch
        ipc_server._ws_drained_event = ws_drained_event
        ipc_server._ws_inflight_count = 0

        app._ipc_server = ipc_server
        # Stub out the rest of _do_cleanup so we can isolate the
        # Event-wait path.
        controller = ShutdownController.__new__(ShutdownController)
        controller._app = app
        controller._quit_lock = threading.Lock()
        controller._recorder_teardown_done = threading.Event()
        controller._recorder_force_closed = False
        # Stub all teardown helpers to no-op.
        for name in [
            "_teardown_asr_models",
            "_teardown_crash_recovery",
            "_teardown_history_db",
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_restore_volume",
            "_teardown_waveform_wiring",
            "_teardown_sounddevice",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_devnull_files",
            "_teardown_level_monitor",
            "_teardown_hotkeys",
            "_teardown_electron",
            "_teardown_event_bus",
        ]:
            setattr(controller, name, MagicMock())
        app.tray = MagicMock()
        app.tray.stop = MagicMock()

        start = time.monotonic()
        controller._do_cleanup()
        elapsed = time.monotonic() - start

        # The Event was already set, so wait returned immediately.
        # The whole _do_cleanup should be very fast (no real teardown
        # work; all stubbed). Generous bound for CI scheduling.
        assert elapsed < 3.0, (
            f"DJ-9: _do_cleanup must not block on ws_drained_event.wait "
            f"when the Event is already set; took {elapsed:.2f}s"
        )

    def test_do_cleanup_waits_for_inflight_dispatch_to_finish(self):
        """If ``_ws_drained_event`` is NOT set (an in-flight dispatch
        is mid-DB-write), ``_do_cleanup`` must wait for the Event to
        fire — the in-flight dispatch completes within the 2s budget
        and the Event fires, then _do_cleanup proceeds."""
        from voice_typer.server.shutdown_controller import ShutdownController

        app = MagicMock()
        app._cleanup_done = False
        app._shutting_down = True
        app._shutting_down_event = MagicMock()
        app._shutting_down_event.set = MagicMock()

        ipc_server = MagicMock()
        ws_pool = MagicMock()
        ws_pool.shutdown = MagicMock()
        ipc_server._ws_dispatch_pool = ws_pool

        ws_drained_event = threading.Event()
        # NOT set — simulate in-flight dispatch.

        ipc_server._ws_drained_event = ws_drained_event
        ipc_server._ws_inflight_count = 1

        app._ipc_server = ipc_server

        controller = ShutdownController.__new__(ShutdownController)
        controller._app = app
        controller._quit_lock = threading.Lock()
        controller._recorder_teardown_done = threading.Event()
        controller._recorder_force_closed = False
        for name in [
            "_teardown_asr_models",
            "_teardown_crash_recovery",
            "_teardown_history_db",
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_restore_volume",
            "_teardown_waveform_wiring",
            "_teardown_sounddevice",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_devnull_files",
            "_teardown_level_monitor",
            "_teardown_hotkeys",
            "_teardown_electron",
            "_teardown_event_bus",
        ]:
            setattr(controller, name, MagicMock())
        # Stub _teardown_history_db to record WHEN it runs relative to
        # the Event firing — the key race we're fixing.
        history_telemetry: list = []
        controller._teardown_history_db = MagicMock(
            side_effect=lambda: history_telemetry.append(("history_db_teardown", time.monotonic()))
        )
        app.tray = MagicMock()
        app.tray.stop = MagicMock()

        # Simulate the in-flight dispatch completing after 200ms.
        def _complete_dispatch_after_delay():
            time.sleep(0.2)
            ws_drained_event.set()

        completer = threading.Thread(target=_complete_dispatch_after_delay, daemon=True)
        completer.start()

        start = time.monotonic()
        controller._do_cleanup()
        elapsed = time.monotonic() - start

        # _do_cleanup must have waited ~200ms for the Event to fire
        # (within the 2s budget) and then proceeded.
        assert elapsed >= 0.15, (
            f"DJ-9: _do_cleanup must wait for the in-flight dispatch "
            f"to complete (Event.wait blocked); took only {elapsed:.2f}s"
        )
        assert elapsed < 3.0, (
            f"DJ-9: _do_cleanup must not block >3s total (200ms dispatch "
            f"+ 5s pool shutdown join which is a no-op since pool.shutdown "
            f"is stubbed); took {elapsed:.2f}s"
        )
        # _teardown_history_db must have run AFTER the Event fired
        # (the race is closed).
        assert history_telemetry, "DJ-9: _teardown_history_db must have been called"
        history_time = history_telemetry[0][1]
        # history_db teardown ran at >= 200ms after _do_cleanup started
        # (the Event fired at ~200ms).
        assert history_time - start >= 0.15, (
            f"DJ-9: _teardown_history_db must run AFTER the ws_drained_event "
            f"fires (the race is closed); ran at {history_time - start:.2f}s"
        )

    def test_do_cleanup_proceeds_after_timeout_when_event_never_fires(self):
        """If ``_ws_drained_event`` NEVER fires (a stuck handler),
        ``_do_cleanup`` MUST log a WARNING and proceed after the
        bounded timeout — never block indefinitely."""
        from voice_typer.server.shutdown_controller import ShutdownController

        app = MagicMock()
        app._cleanup_done = False
        app._shutting_down = True
        app._shutting_down_event = MagicMock()
        app._shutting_down_event.set = MagicMock()

        ipc_server = MagicMock()
        ws_pool = MagicMock()
        ws_pool.shutdown = MagicMock()
        ipc_server._ws_dispatch_pool = ws_pool

        ws_drained_event = threading.Event()
        # NEVER set — simulate a stuck handler.

        ipc_server._ws_drained_event = ws_drained_event
        ipc_server._ws_inflight_count = 1

        app._ipc_server = ipc_server

        controller = ShutdownController.__new__(ShutdownController)
        controller._app = app
        controller._quit_lock = threading.Lock()
        controller._recorder_teardown_done = threading.Event()
        controller._recorder_force_closed = False
        for name in [
            "_teardown_asr_models",
            "_teardown_crash_recovery",
            "_teardown_history_db",
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_restore_volume",
            "_teardown_waveform_wiring",
            "_teardown_sounddevice",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_devnull_files",
            "_teardown_level_monitor",
            "_teardown_hotkeys",
            "_teardown_electron",
            "_teardown_event_bus",
        ]:
            setattr(controller, name, MagicMock())
        history_called: list = []
        controller._teardown_history_db = MagicMock(side_effect=lambda: history_called.append(1))
        app.tray = MagicMock()
        app.tray.stop = MagicMock()

        start = time.monotonic()
        controller._do_cleanup()
        elapsed = time.monotonic() - start

        # The Event wait must have timed out (~2s) — _do_cleanup
        # proceeded anyway (the stuck handler is on its own).
        assert elapsed >= 1.9, (
            f"DJ-9: _do_cleanup must wait the full 2s timeout when "
            f"ws_drained_event never fires; took only {elapsed:.2f}s"
        )
        # And _do_cleanup must have proceeded (history_db teardown ran).
        assert history_called == [1], (
            "DJ-9: _do_cleanup must PROCEED with history_db teardown after the drain timeout (never block indefinitely)"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
