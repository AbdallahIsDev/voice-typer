"""DJ-9 regression: in-flight WS handler races DB teardown."""

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
    "sidecar_ws_internals",
    "dispatch.py",
)
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
    """Build a MagicMock IPCServer with the rate-limiter plumbing"""
    server = MagicMock()
    server.app = MagicMock()
    server.app._shutting_down = False
    # ``server._dispatch`` is the actual handler, make it a no-op
    server._dispatch = MagicMock(return_value={"type": "ok"})
    # Pre-set the four lazy-init slots to None so _make_dispatch's
    server._ws_dispatch_pool = None
    server._ws_drained_event = None
    server._ws_inflight_lock = None
    server._ws_inflight_count = None
    return server


class TestSourceContracts:
    """DJ-9: source-level contract for the Event coordination."""

    def test_sidecar_ws_attaches_drained_event(self):
        """``sidecar_ws._make_dispatch`` must attach"""
        src = _src(_SIDECAR_WS_PATH)
        assert "_ws_drained_event" in src, (
            "DJ-9: sidecar_ws._make_dispatch must attach _ws_drained_event to the IPC server"
        )
        # The Event must be created via threading.Event().
        assert "threading.Event()" in src, "DJ-9: _ws_drained_event must be a threading.Event"

    def test_sidecar_ws_attaches_inflight_lock(self):
        """``sidecar_ws._make_dispatch`` must attach"""
        src = _src(_SIDECAR_WS_PATH)
        assert "_ws_inflight_lock" in src, "DJ-9: sidecar_ws._make_dispatch must attach _ws_inflight_lock"

    def test_sidecar_ws_attaches_inflight_count(self):
        """``sidecar_ws._make_dispatch`` must attach"""
        src = _src(_SIDECAR_WS_PATH)
        assert "_ws_inflight_count" in src, "DJ-9: sidecar_ws._make_dispatch must attach _ws_inflight_count"

    def test_sidecar_ws_clears_event_on_dispatch_entry(self):
        """The ``dispatch`` coroutine body MUST clear"""
        src = _src(_SIDECAR_WS_PATH)
        # The increment + clear pair must be inside a ``with ws_inflight_lock:``
        assert "with ws_inflight_lock:" in src, (
            "DJ-9: the count increment + Event.clear pair must be under ws_inflight_lock"
        )
        assert "ws_drained_event.clear()" in src, "DJ-9: dispatch must clear _ws_drained_event on entry"

    def test_sidecar_ws_sets_event_on_dispatch_exit(self):
        """The ``dispatch`` coroutine body MUST set"""
        src = _src(_SIDECAR_WS_PATH)
        assert "ws_drained_event.set()" in src, (
            "DJ-9: dispatch must set _ws_drained_event on exit (when in-flight count drops to 0)"
        )
        # The set() call must be inside a ``finally`` block so it
        set_idx = src.rfind("ws_drained_event.set()")
        assert set_idx > -1
        # Find the nearest ``finally:`` before set_idx.
        nearest_finally = src.rfind("finally:", 0, set_idx)
        assert nearest_finally > -1, (
            "DJ-9: ws_drained_event.set() (the dispatch-exit call, NOT "
            "the lazy-init call) must be inside a finally block"
        )

    def test_shutdown_controller_waits_on_drained_event(self):
        """existing pool.shutdown + 5s join."""
        src = _src(_SHUTDOWN_CONTROLLER_PATH)
        assert "_ws_drained_event" in src, "DJ-9: shutdown_controller._do_cleanup must reference _ws_drained_event"
        # The wait must be bounded (timeout=...).
        assert "ws_drained_event.wait(timeout=" in src, (
            "DJ-9: _do_cleanup must call ws_drained_event.wait(timeout=...), bounded wait, never blocks indefinitely"
        )

    def test_shutdown_controller_logs_on_drain_timeout(self):
        """If the Event wait times out (in-flight handler still"""
        src = _src(_SHUTDOWN_CONTROLLER_PATH)
        # Find the wait call and the surrounding drain-timeout branch.
        wait_idx = src.find("ws_drained_event.wait(timeout=")
        assert wait_idx > -1
        # Slice a generous window after the wait.
        block = src[wait_idx : wait_idx + 1200]
        assert "WS dispatch drain Event did not" in block, "the drain-timeout branch must log the un-drained Event race"
        assert "proceeding" in block, "the drain-timeout branch must proceed instead of blocking shutdown"
        assert "log.warning" in block, "the drain-timeout branch must log at WARNING level"


class TestDispatchEventCoordination:
    """DJ-9: behavioral verification that ``_make_dispatch``'s"""

    def test_dispatch_clears_event_on_entry_and_sets_on_exit(self):
        """A single dispatch: ``_ws_drained_event`` is cleared on"""
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
        """
        While a dispatch is in-flight (blocked on the handler),
        ``_ws_drained_event`` must NOT be set: ``_do_cleanup`` would
        """
        server = _make_server_for_dispatch()
        # Make ``server._dispatch`` block on an event so we can
        dispatch_unblock = threading.Event()

        def _blocking_dispatch(msg):
            dispatch_unblock.wait(timeout=5.0)
            return {"type": "ok"}

        server._dispatch = _blocking_dispatch
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "test_command"}, websocket=None)

        # Run the dispatch on a separate thread + its own loop so we
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
        """If ``server._dispatch`` raises, the ``finally`` block MUST"""
        server = _make_server_for_dispatch()
        server._dispatch = MagicMock(side_effect=RuntimeError("simulated DB write failure"))
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "test_command"}, websocket=None)

        result = asyncio.run(_run())

        assert result["type"] == "error"
        assert result["data"]["code"] == "server.internal_error"
        # Event MUST be re-set even though the dispatch
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be SET even when dispatch raises "
            "(finally block must run so _do_cleanup does not deadlock)"
        )
        assert server._ws_inflight_count == 0

    def test_concurrent_dispatches_count_correctly(self):
        """Two concurrent dispatches: count goes 0→1→2 on entry,"""
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

        # Unblock dispatch A only, count should drop to 1, Event
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

        # Unblock B, count drops to 0, Event is set.
        dispatch_unblock_b.set()
        assert done_events[1].wait(timeout=5.0)
        time.sleep(0.1)

        assert server._ws_inflight_count == 0, "DJ-9: _ws_inflight_count must drop to 0 after both complete"
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must be SET after the last dispatch completes (count drops to 0)"
        )

    def test_shutting_down_flag_rejects_new_dispatch(self):
        """When ``server.app._shutting_down`` is True, the dispatch"""
        server = _make_server_for_dispatch()
        server.app._shutting_down = True
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "test_command"}, websocket=None)

        result = asyncio.run(_run())

        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        # The count must NOT have been incremented (the gate fires
        assert server._ws_inflight_count == 0, (
            "DJ-9: the _shutting_down gate must fire BEFORE the count "
            "increment (rejected requests do not count as in-flight)"
        )
        assert server._ws_drained_event.is_set(), (
            "DJ-9: _ws_drained_event must remain SET when the request was rejected (no in-flight dispatch to wait for)"
        )

    def test_shutdown_message_bypasses_shutting_down_gate(self):
        """``_shutting_down`` gate (the host sends it to TRIGGER"""
        server = _make_server_for_dispatch()
        server.app._shutting_down = True  # gate is on
        dispatch = sidecar_ws._make_dispatch(server)

        async def _run():
            return await dispatch({"type": "shutdown"}, websocket=None)

        result = asyncio.run(_run())

        # ``shutdown`` is not rejected, it flows through server._dispatch.
        assert result == {"type": "ok"}
        assert server._ws_inflight_count == 0, "DJ-9: after the shutdown dispatch completes, count must be 0"
        assert server._ws_drained_event.is_set()


class TestDoCleanupWaitsOnEvent:
    """DJ-9: ``_do_cleanup`` must wait on ``_ws_drained_event`` with"""

    def test_do_cleanup_returns_quickly_when_event_already_set(self):
        """If ``_ws_drained_event`` is already set (no in-flight"""
        from voice_typer.server.shutdown_controller import ShutdownController

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
            "_teardown_host_child",
            "_teardown_event_bus",
        ]:
            setattr(controller, name, MagicMock())
        app.tray = MagicMock()
        app.tray.stop = MagicMock()

        start = time.monotonic()
        controller._do_cleanup()
        elapsed = time.monotonic() - start

        # The Event was already set, so wait returned immediately.
        assert elapsed < 3.0, (
            f"DJ-9: _do_cleanup must not block on ws_drained_event.wait "
            f"when the Event is already set; took {elapsed:.2f}s"
        )

    def test_do_cleanup_waits_for_inflight_dispatch_to_finish(self):
        """If ``_ws_drained_event`` is NOT set (an in-flight dispatch"""
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
        # NOT set, simulate in-flight dispatch.

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
            "_teardown_host_child",
            "_teardown_event_bus",
        ]:
            setattr(controller, name, MagicMock())
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
        # (the race is closed).
        assert history_telemetry, "DJ-9: _teardown_history_db must have been called"
        history_time = history_telemetry[0][1]
        assert history_time - start >= 0.15, (
            f"DJ-9: _teardown_history_db must run AFTER the ws_drained_event "
            f"fires (the race is closed); ran at {history_time - start:.2f}s"
        )

    def test_do_cleanup_proceeds_after_timeout_when_event_never_fires(self):
        """If ``_ws_drained_event`` NEVER fires (a stuck handler),"""
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
        # NEVER set, simulate a stuck handler.

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
            "_teardown_host_child",
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

        # The Event wait must have timed out (~2s), _do_cleanup
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
