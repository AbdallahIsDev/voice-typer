"""Behavioral tests for ``voice_typer.server.ipc.lifecycle.LifecycleMixin``.

These tests exercise the lifecycle mixin's runtime behavior (heartbeat
watchdog, ``stop()`` idempotency, relaunch-ack coordination, tray-state
hook, and the ``VOICE_TYPER_ALLOW_STDIN_IPC`` stdin-listener gate)
without spinning up the real ``VoiceTyperApp`` — every test uses a
``MagicMock`` app + service so the heavy subsystems (logging config,
single-instance mutex, PortAudio) are skipped.

The mixin accesses instance state declared on :class:`IPCServer`
(``_running``, ``_tcp_client``, ``_tcp_server_socket``,
``_tcp_worker_pool``, ``_tcp_dispatch_pool``, ``_push_fn``,
``_cached_shutting_down``, ``_heartbeat_stop_event``,
``_heartbeat_thread``, ``_stdin_thread``, ``_relaunch_ack_event``,
``_last_heartbeat_at``, ``_ready_emitted``, ``_shutdown_started``)
which are all initialized by ``IPCServer.__init__``; the tests
construct a real ``IPCServer(app, service=MagicMock())`` so every
attribute is present without manual setup.

Scope: lifecycle behavior only. TCP/WS transport, dispatcher, and
stdin-runner behaviors are covered by their dedicated test files
(``test_tcp_io``, ``test_run_loop``, ``test_ipc_stdin_runner``).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc import lifecycle as lifecycle_mod
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.tray_types import AppState

from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    server,
)

# ── Helpers ────────────────────────────────────────────────────────────


def _make_server() -> IPCServer:
    """Build an IPCServer with MagicMock app + service for lifecycle tests.

    ``_shutting_down`` is an explicit bool so the dispatch gate sees a
    real ``False`` (not a child mock that's truthy). ``_thread_registry``
    is ``None`` so the thread-registry registration path is skipped
    (the lifecycle methods tolerate its absence via ``getattr``).
    """
    app = MagicMock()
    app._shutting_down = False
    app._thread_registry = None
    # The tray-state hook checks ``app.tray.set_state._vt_wrapped`` to
    # dedupe; a fresh MagicMock reports a child mock (truthy) which would
    # short-circuit the hook on first call. Force it to False so the
    # hook actually wraps the original.
    app.tray.set_state._vt_wrapped = False
    service = MagicMock()
    return IPCServer(app, service=service)


# ── Heartbeat watchdog ────────────────────────────────────────────────


class TestHeartbeatWatchdog:
    """``_check_heartbeat_timeout`` drives the Electron-alive watchdog.

    The daemon thread started by ``start()`` calls this every
    ``_HEARTBEAT_INTERVAL_SECONDS``; tests invoke it directly so they
    don't have to wait for the real 45s timeout.
    """

    def test_no_trip_when_first_heartbeat_never_arrived(self) -> None:
        """A ``None`` ``_last_heartbeat_at`` means Electron has not yet
        sent its first heartbeat. The watchdog must NOT fire — otherwise
        a slow Electron cold start (10+ s for the torch import) would
        cause a false-positive exit."""
        server = _make_server()
        assert server._last_heartbeat_at is None
        result = server._check_heartbeat_timeout()
        assert result is False
        server.app.quit.assert_not_called()

    def test_no_trip_when_heartbeat_is_fresh(self) -> None:
        """A heartbeat timestamp within the timeout window is healthy —
        the watchdog returns False without calling ``app.quit()``."""
        server = _make_server()
        # Set the heartbeat to "just now" — well within the 45s window.
        server._last_heartbeat_at = time.monotonic()
        result = server._check_heartbeat_timeout()
        assert result is False
        server.app.quit.assert_not_called()

    def test_trip_after_timeout_calls_app_quit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``now - last > _HEARTBEAT_TIMEOUT_SECONDS`` the watchdog
        fires: returns True and calls ``app.quit()`` so the process can
        unwind through the shared ``_do_cleanup()`` path.

        The force-exit fallback thread (which calls ``os._exit(1)`` after
        ``_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS``) is parked on a very
        large grace period so it can't actually exit the test process —
        ``app.quit()`` is mocked so the real ``_do_cleanup`` never runs,
        but the daemon thread is still spawned by the watchdog body.
        """
        # Park the force-exit grace period at 10000s — the daemon thread
        # will still be sleeping (harmlessly) when the test process exits.
        monkeypatch.setattr(
            lifecycle_mod,
            "_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            10000,
        )
        server = _make_server()
        # 100 seconds ago — well past the 45s timeout window.
        server._last_heartbeat_at = time.monotonic() - 100.0
        result = server._check_heartbeat_timeout()
        assert result is True, "heartbeat watchdog must return True when the heartbeat is overdue"
        server.app.quit.assert_called_once()


# ── stop() idempotency ────────────────────────────────────────────────


class TestStopIdempotency:
    """``stop()`` is the canonical shutdown transition. Calling it twice
    must not raise and must not attempt to close already-None resources
    (the second call is a no-op).
    """

    def test_stop_called_twice_second_is_noop(self) -> None:
        """The first ``stop()`` clears ``_tcp_client`` /
        ``_tcp_server_socket`` and shuts down the worker pools; the
        second call sees them all as ``None`` and skips every close
        branch. No exception is raised."""
        server = _make_server()
        # Pretend a TCP client + listening socket are bound so the first
        # stop() exercises the close paths.
        fake_client = MagicMock()
        fake_sock = MagicMock()
        server._tcp_client = fake_client
        server._tcp_server_socket = fake_sock
        server._running = True

        server.stop()
        assert server._running is False
        assert server._tcp_client is None
        assert server._tcp_server_socket is None
        fake_client.close.assert_called_once()
        fake_sock.close.assert_called_once()

        # Second call — every resource is already None. Must not raise.
        server.stop()
        # Close counts stay at one (the second call did NOT re-close).
        fake_client.close.assert_called_once()
        fake_sock.close.assert_called_once()
        assert server._running is False

    def test_stop_when_already_stopped_is_noop(self) -> None:
        """A server that never started (or already stopped) can still be
        ``stop()``-ed without raising — every resource is ``None``."""
        server = _make_server()
        # Nothing was started — every transport field is None already.
        assert server._tcp_client is None
        assert server._tcp_server_socket is None
        server.stop()  # must not raise
        assert server._running is False
        assert server._cached_shutting_down is True


# ── PERF-SHUTDOWN-001: dispatch-pool self-join ───────────────────────


class TestStopDispatchPoolSelfJoin:
    """PERF-SHUTDOWN-001: ``stop()`` called from INSIDE the TCP dispatch
    pool must not self-join.

    ``quit_app`` is dispatched onto ``_tcp_dispatch_pool`` (see
    ``_tcp_dispatch_and_respond``), so the quit handler runs
    ``app.quit()`` → ``_do_cleanup()`` → ``ipc_server.stop()`` on a
    pool worker. Draining that pool from inside one of its own workers
    is a self-join that can never complete — ``shutdown(wait=True)``
    waits for every worker, including the caller blocked inside
    ``stop()`` — so the drain burned its full 5s timeout on every
    quit (measured: shutdown took 8.6s, of which 5s was this
    deadlock).
    """

    def test_stop_from_dispatch_worker_returns_fast(self) -> None:
        """Submitting ``stop()`` to a real single-worker dispatch pool
        must complete quickly, not burn the 5s drain timeout."""
        from concurrent.futures import ThreadPoolExecutor

        server = _make_server()
        # Nothing bound — mirror TestStopIdempotency's prep so stop()
        # only exercises the pool drain paths.
        server._tcp_client = None
        server._tcp_server_socket = None
        server._tcp_worker_pool = None
        # A REAL single-worker executor stands in for the production
        # ``_tcp_dispatch_pool``: the self-join only reproduces with a
        # real pool whose ``_threads`` set contains the caller.
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tcp-dispatch")
        server._tcp_dispatch_pool = pool
        start = time.monotonic()
        try:
            # If the self-join regresses, the task blocks in the 5s
            # drain and this raises TimeoutError after 2s.
            pool.submit(server.stop).result(timeout=2.0)
            elapsed = time.monotonic() - start
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        assert elapsed < 2.0, (
            f"PERF-SHUTDOWN-001: stop() from a dispatch worker must not "
            f"self-join on the pool drain; took {elapsed:.2f}s"
        )
        assert server._cached_shutting_down is True

    def test_stop_skips_dispatch_drain_when_app_shutting_down(self) -> None:
        """The drain-skip gate must also fire when ``stop()`` runs on a
        NON-pool helper thread while the app is shutting down.

        This is the production quit shape that the thread-membership
        check alone cannot see: ``quit_app`` runs on a ``tcp-dispatch``
        worker, which blocks inside ``_do_cleanup()`` waiting for a
        ``_run_with_timeout("ipc_server.stop", ...)`` helper thread.
        ``stop()`` therefore executes OUTSIDE the pool, and draining the
        pool would wait on a worker that is transitively blocked on
        this very call — burning the full 5s drain timeout on every
        quit (measured: 8.8s end-to-end). ``app._shutting_down`` guards
        that transitive self-join.
        """
        from concurrent.futures import ThreadPoolExecutor

        server = _make_server()
        server.app._shutting_down = True
        server._tcp_client = None
        server._tcp_server_socket = None
        server._tcp_worker_pool = None
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tcp-dispatch")
        server._tcp_dispatch_pool = pool
        stop_returned = threading.Event()
        worker_released = threading.Event()

        try:
            def _quit_handler() -> None:
                # Mirrors the dispatch worker executing quit_app → app.quit()
                # → _do_cleanup(): it stays blocked until stop() returns.
                worker_released.wait(timeout=5.0)

            pool.submit(_quit_handler)

            def _stop_from_helper_thread() -> None:
                server.stop()
                stop_returned.set()

            helper = threading.Thread(target=_stop_from_helper_thread, name="cleanup-ipc_server.stop")
            start = time.monotonic()
            helper.start()
            helper.join(timeout=1.0)
            elapsed = time.monotonic() - start
            try:
                assert stop_returned.is_set(), (
                    f"stop() during app shutdown must skip the dispatch-pool drain "
                    f"(transitive self-join); blocked {elapsed:.2f}s"
                )
            finally:
                worker_released.set()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def test_in_pool_worker_detects_pool_thread(self) -> None:
        """``_in_pool_worker`` must return ``True`` from a pool worker
        and ``False`` from a non-worker thread — the exact predicate
        that gates the self-join skip."""
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tcp-dispatch")
        observed: dict[str, bool] = {}

        def _probe() -> None:
            observed["inside"] = lifecycle_mod._in_pool_worker(pool)
            # CPython 3.12 ``_adjust_thread_count`` calls ``t.start()``
            # BEFORE ``self._threads.add(t)``, so a live worker can run
            # while ``_threads`` is momentarily EMPTY. Simulate that
            # window and pin the prefix fallback.
            real_threads = pool._threads
            try:
                pool._threads = set()  # type: ignore[attr-defined]
                observed["inside_empty_set"] = lifecycle_mod._in_pool_worker(pool)
            finally:
                pool._threads = real_threads

        try:
            assert lifecycle_mod._in_pool_worker(pool) is False, (
                "main thread is not a pool worker — must be False"
            )
            pool.submit(_probe).result(timeout=5.0)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        assert observed.get("inside") is True, (
            "a running pool worker must be detected as inside the pool"
        )
        assert observed.get("inside_empty_set") is True, (
            "a running pool worker must still be detected when ``pool._threads`` "
            "is momentarily empty (CPython 3.12 worker-start race) — without the "
            "prefix fallback the quit-path self-join gate drains the pool from "
            "inside itself and burns the full 5s timeout"
        )


# ── relaunch-ack coordination ─────────────────────────────────────────


class TestRelaunchAckCoordination:
    """``_handle_relaunch_ack`` sets the per-instance ``_relaunch_ack_event``
    so ``restart_app``'s bounded wait returns early instead of always
    blocking the configured timeout. ``wait_for_relaunch_ack`` clears the
    event before waiting so a stale ack from a prior restart cannot
    satisfy a fresh one.
    """

    def test_handle_relaunch_ack_sets_event(self) -> None:
        """The handler sets ``_relaunch_ack_event`` and returns ``None``
        (the ack is fire-and-forget — the response envelope is unused
        because ``restart_app`` owns the socket teardown)."""
        server = _make_server()
        server._relaunch_ack_event.clear()
        assert not server._relaunch_ack_event.is_set()
        result = server._handle_relaunch_ack(data=None, resp={"id": 1})
        assert result is None
        assert server._relaunch_ack_event.is_set()

    def test_wait_for_relaunch_ack_returns_true_when_acked_in_window(
        self,
    ) -> None:
        """When the ack arrives BEFORE the wait deadline, the wait
        returns True quickly — the tray thread is unblocked as soon as
        Electron acks instead of always blocking the configured timeout."""
        server = _make_server()

        def _ack_after_short_delay() -> None:
            time.sleep(0.05)
            server._handle_relaunch_ack(data=None, resp={"id": 1})

        # capture the thread handle and join it after the wait
        # returns so we don't leak a daemon Thread-without-join (the
        # thread has already fired _handle_relaunch_ack by the time
        # wait_for_relaunch_ack returns True, so the join is
        # near-instant).
        ack_thread = threading.Thread(target=_ack_after_short_delay, daemon=True)
        ack_thread.start()
        start = time.monotonic()
        acked = server.wait_for_relaunch_ack(timeout=2.0)
        elapsed = time.monotonic() - start
        assert acked is True
        # Returned well before the 2s deadline (ack arrived at ~50ms).
        assert elapsed < 1.0, f"wait_for_relaunch_ack should return as soon as the ack arrives; waited {elapsed:.3f}s"
        # Best-effort join so the daemon thread doesn't linger past
        # the test (it should have exited within ~50ms of start).
        ack_thread.join(timeout=1.0)

    def test_wait_for_relaunch_ack_returns_false_on_timeout(self) -> None:
        """When no ack arrives within the timeout, the wait returns
        False — the tray thread is unblocked by the timeout and proceeds
        with cleanup."""
        server = _make_server()
        start = time.monotonic()
        acked = server.wait_for_relaunch_ack(timeout=0.1)
        elapsed = time.monotonic() - start
        assert acked is False
        # Tolerant lower bound: ``Event.wait`` may return a few ms early
        # on Windows (timer granularity), so require at least half the
        # timeout — enough to prove the wait did NOT short-circuit.
        assert elapsed >= 0.05, f"waited only {elapsed:.3f}s for the 0.1s timeout"

    def test_wait_for_relaunch_ack_clears_event_before_waiting(self) -> None:
        """The wait clears the event before waiting so a stale ack from
        a prior restart cycle cannot satisfy a fresh one. We pre-set the
        event, then call wait — it must NOT return immediately from the
        stale set state (it should wait for a fresh ack or time out)."""
        server = _make_server()
        server._relaunch_ack_event.set()  # stale ack from prior cycle
        start = time.monotonic()
        acked = server.wait_for_relaunch_ack(timeout=0.1)
        elapsed = time.monotonic() - start
        # The stale event was cleared; no fresh ack arrived → timeout.
        assert acked is False
        # Tolerant lower bound: ``Event.wait`` may return a few ms early
        # on Windows (timer granularity), so require at least half the
        # timeout — enough to prove the stale event was NOT served.
        assert elapsed >= 0.05, f"waited only {elapsed:.3f}s for the 0.1s timeout"

    def test_relaunch_ack_arrives_after_shutdown_started_is_safe(
        self,
    ) -> None:
        """Even after ``_shutdown_started`` is set (e.g. the Tauri host
        already sent a ``shutdown`` and the cleanup thread is running),
        a late ``relaunch_ack`` must still set the event without raising.

        The handler does NOT consult ``_shutdown_started`` — the ack is
        a one-shot signal that's harmless once shutdown is underway.
        ``restart_app``'s wait either already timed out (the ack is
        late) or hasn't been called yet; setting the event in either
        case is a no-op-with-side-effect that doesn't corrupt state.
        """
        server = _make_server()
        server._shutdown_started.set()
        server._relaunch_ack_event.clear()
        # Must not raise — late acks are tolerated.
        result = server._handle_relaunch_ack(data=None, resp={"id": 1})
        assert result is None
        assert server._relaunch_ack_event.is_set()


# ── Tray state hook ────────────────────────────────────────────────────


class TestTrayStateHook:
    """``_hook_tray_set_state`` monkey-patches ``app.tray.set_state`` so
    every state change emits a ``status_change`` push event back to the
    frontend. The hook is idempotent so a start → stop → start cycle
    doesn't stack wrappers.
    """

    def test_hook_wraps_set_state_and_pushes_status_change(self) -> None:
        """After the hook is installed, calling ``app.tray.set_state(state)``
        invokes the original AND pushes a ``status_change`` event.

        The hook replaces ``app.tray.set_state`` with a plain Python
        closure (``wrapped``) that calls the captured ``original`` and
        then ``self.push(...)``. We capture the original MagicMock
        BEFORE hooking so we can assert it was called by the wrapper.
        """
        server = _make_server()
        push_calls: list[dict] = []
        server.push = lambda msg: push_calls.append(msg)  # type: ignore[assignment]
        original_set_state = server.app.tray.set_state
        server._hook_tray_set_state()
        # Trigger a state change.
        server.app.tray.set_state(AppState.RECORDING, message="recording")
        # The original (captured in the closure) was invoked with the
        # state + message args (passed positionally by the wrapper).
        original_set_state.assert_called_once_with(AppState.RECORDING, "recording")
        # The push was called with a status_change envelope.
        assert len(push_calls) == 1
        assert push_calls[0]["type"] == "status_change"
        assert push_calls[0]["data"]["status"] == "recording"
        assert push_calls[0]["data"]["message"] == "recording"

    def test_hook_is_idempotent(self) -> None:
        """Calling ``_hook_tray_set_state`` twice must NOT double-wrap —
        the second call sees ``_vt_wrapped=True`` and returns without
        stacking another wrapper. Without this guard, N start cycles
        would emit N push events per state change."""
        server = _make_server()
        push_calls: list[dict] = []
        server.push = lambda msg: push_calls.append(msg)  # type: ignore[assignment]
        server._hook_tray_set_state()
        server._hook_tray_set_state()  # second call — no-op
        server.app.tray.set_state(AppState.IDLE)
        # Exactly one push despite double-hooking.
        assert len(push_calls) == 1

    def test_hook_fires_before_ready_emitted_pushes_anyway(self) -> None:
        """The hook is installed by ``start()`` BEFORE the first WS
        connection lands (``_ready_emitted`` is still False at that
        point). The push event is emitted unconditionally — the
        ``_ready_emitted`` gate lives in ``sidecar_ws._handle_connection``
        (not here), so the tray hook's push reaches the event bus
        regardless of WS state. Subscribers (or the buffered pending-TCP
        list) decide whether the event reaches the frontend.

        This test pins the contract: a state change BEFORE
        ``_ready_emitted=True`` still calls ``self.push``.
        """
        server = _make_server()
        server._ready_emitted = False
        push_calls: list[dict] = []
        server.push = lambda msg: push_calls.append(msg)  # type: ignore[assignment]
        server._hook_tray_set_state()
        server.app.tray.set_state(AppState.ERROR, message="early failure")
        # Push fired even though _ready_emitted is still False.
        assert len(push_calls) == 1
        assert push_calls[0]["data"]["status"] == "error"


# ── stdin-IPC env-var gate ─────────────────────────────────────────────


class TestStdinIpcEnvVarGate:
    """``VOICE_TYPER_ALLOW_STDIN_IPC=1`` is the canonical gate for the
    unauthenticated stdin/stdout IPC listener. When ``_tcp_mode`` is
    False (the legacy stdin path) AND the env var is set, ``start()``
    spawns the ``ipc-server`` stdin listener thread; when the env var is
    unset (or ``=0``), ``start()`` refuses and leaves
    ``_stdin_thread`` as ``None``.

    Production callers (``main()``) always set ``_tcp_mode = True``
    before ``start()``, so this gate never fires in production — it
    exists to catch direct-API / test paths that would otherwise expose
    an unauthenticated command channel on the user's terminal.
    """

    def test_env_var_set_to_one_spawns_stdin_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``_tcp_mode=False`` AND ``VOICE_TYPER_ALLOW_STDIN_IPC=1``,
        ``start()`` spawns the ``ipc-server`` stdin listener thread."""
        # Force-import the module so we can patch its ``threading``
        # attribute (the production path does ``threading.Thread(...)``
        # in ``lifecycle.py``, resolved through the ``threading`` name
        # imported at the top of that module).
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        app = MagicMock()
        app._shutting_down = False
        app._thread_registry = None
        app.tray.set_state._vt_wrapped = False
        server = IPCServer(app, service=MagicMock())
        server._tcp_mode = False

        # Don't actually subscribe the push fn to the real event bus.
        monkeypatch.setattr(event_bus, "subscribe", lambda fn: None)
        monkeypatch.setattr(event_bus, "unsubscribe", lambda fn: None)

        created_threads: list[str] = []

        class _FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.name = name
                self.target = target
                self.daemon = daemon
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        # Patch ``threading.Thread`` on the lifecycle module (where
        # ``start()`` resolves the name from).
        monkeypatch.setattr(lifecycle_mod.threading, "Thread", _FakeThread)
        # Also patch on ipc_server (some MRO paths may resolve there).
        monkeypatch.setattr(ipc_server_mod.threading, "Thread", _FakeThread)

        server.start()
        try:
            assert "ipc-server" in created_threads, (
                "VOICE_TYPER_ALLOW_STDIN_IPC=1 must spawn the 'ipc-server' "
                "stdin listener thread when _tcp_mode is False."
            )
            assert server._stdin_thread is not None
        finally:
            server.stop()

    def test_env_var_unset_does_not_spawn_stdin_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``_tcp_mode=False`` AND the env var is unset (or ``=0``),
        ``start()`` refuses to spawn the stdin listener. A WARNING is
        logged and ``_stdin_thread`` is left as ``None``."""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Setting the env var to "0" (not "1") must also refuse.
        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "0")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        app = MagicMock()
        app._shutting_down = False
        app._thread_registry = None
        app.tray.set_state._vt_wrapped = False
        server = IPCServer(app, service=MagicMock())
        server._tcp_mode = False

        monkeypatch.setattr(event_bus, "subscribe", lambda fn: None)
        monkeypatch.setattr(event_bus, "unsubscribe", lambda fn: None)

        created_threads: list[str] = []

        class _FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.name = name
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        monkeypatch.setattr(lifecycle_mod.threading, "Thread", _FakeThread)
        monkeypatch.setattr(ipc_server_mod.threading, "Thread", _FakeThread)

        server.start()
        try:
            assert "ipc-server" not in created_threads, (
                "VOICE_TYPER_ALLOW_STDIN_IPC=0 must refuse to spawn the "
                "stdin listener — the unauthenticated path is gated off."
            )
            assert server._stdin_thread is None
        finally:
            server.stop()

    def test_tcp_mode_skips_stdin_thread_regardless_of_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``_tcp_mode=True``, the stdin listener is never spawned
        regardless of the env var — TCP/WS is the authenticated path and
        stdin is unused (inherited from Electron, connected to
        ``/dev/null`` or ``NUL``)."""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        # Even with the env var set, TCP mode must not spawn stdin.
        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        app = MagicMock()
        app._shutting_down = False
        app._thread_registry = None
        app.tray.set_state._vt_wrapped = False
        server = IPCServer(app, service=MagicMock())
        server._tcp_mode = True

        monkeypatch.setattr(event_bus, "subscribe", lambda fn: None)
        monkeypatch.setattr(event_bus, "unsubscribe", lambda fn: None)

        created_threads: list[str] = []

        class _FakeThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.name = name
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        monkeypatch.setattr(lifecycle_mod.threading, "Thread", _FakeThread)
        monkeypatch.setattr(ipc_server_mod.threading, "Thread", _FakeThread)

        server.start()
        try:
            assert "ipc-server" not in created_threads
            assert server._stdin_thread is None
        finally:
            server.stop()
