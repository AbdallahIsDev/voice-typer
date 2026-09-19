"""Behavioral tests for ``voice_typer.server.ipc.lifecycle.LifecycleMixin``."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc import lifecycle as lifecycle_mod
from voice_typer.server.ipc_server import IPCServer
from voice_typer.server.tray_types import AppState

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes
from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    server,
)


def _make_server() -> IPCServer:
    """Build an IPCServer with the canonical fake app + service."""
    server, _fake_app, _fake_service = make_ipc_server_with_fakes(thread_registry=None)
    return server


class TestHeartbeatWatchdog:
    """``_check_heartbeat_timeout`` drives the predecessor-alive watchdog."""

    def test_no_trip_when_first_heartbeat_never_arrived(self) -> None:
        """A ``None`` ``_last_heartbeat_at`` means predecessor has not yet"""
        server = _make_server()
        assert server._last_heartbeat_at is None
        result = server._check_heartbeat_timeout()
        assert result is False
        server.app.quit.assert_not_called()

    def test_no_trip_when_heartbeat_is_fresh(self) -> None:
        """A heartbeat timestamp within the timeout window is healthy —"""
        server = _make_server()
        # Set the heartbeat to "just now", well within the 45s window.
        server._last_heartbeat_at = time.monotonic()
        result = server._check_heartbeat_timeout()
        assert result is False
        server.app.quit.assert_not_called()

    def test_trip_after_timeout_calls_app_quit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``now - last > _HEARTBEAT_TIMEOUT_SECONDS`` the watchdog"""
        # Park the force-exit grace period at 10000s, the daemon thread
        monkeypatch.setattr(
            lifecycle_mod,
            "_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            10000,
        )
        server = _make_server()
        # 100 seconds ago, well past the 45s timeout window.
        server._last_heartbeat_at = time.monotonic() - 100.0
        result = server._check_heartbeat_timeout()
        assert result is True, "heartbeat watchdog must return True when the heartbeat is overdue"
        server.app.quit.assert_called_once()


class TestStopIdempotency:
    """``stop()`` is the canonical shutdown transition. Calling it twice"""

    def test_stop_called_twice_second_is_noop(self) -> None:
        """The first ``stop()`` clears ``_tcp_client`` /"""
        server = _make_server()
        # Pretend a TCP client + listening socket are bound so the first
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

        # Second call, every resource is already None. Must not raise.
        server.stop()
        # Close counts stay at one (the second call did NOT re-close).
        fake_client.close.assert_called_once()
        fake_sock.close.assert_called_once()
        assert server._running is False

    def test_stop_when_already_stopped_is_noop(self) -> None:
        """A server that never started (or already stopped) can still be"""
        server = _make_server()
        # Nothing was started, every transport field is None already.
        assert server._tcp_client is None
        assert server._tcp_server_socket is None
        server.stop()  # must not raise
        assert server._running is False
        assert server._cached_shutting_down is True


class TestStopDispatchPoolSelfJoin:
    """PERF-SHUTDOWN-001: ``stop()`` called from INSIDE the TCP dispatch"""

    def test_stop_from_dispatch_worker_returns_fast(self) -> None:
        """Submitting ``stop()`` to a real single-worker dispatch pool"""
        from concurrent.futures import ThreadPoolExecutor

        server = _make_server()
        # Nothing bound, mirror TestStopIdempotency's prep so stop()
        server._tcp_client = None
        server._tcp_server_socket = None
        server._tcp_worker_pool = None
        # A REAL single-worker executor stands in for the production
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tcp-dispatch")
        server._tcp_dispatch_pool = pool
        start = time.monotonic()
        try:
            # If the self-join regresses, the task blocks in the 5s
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
        """NON-pool helper thread while the app is shutting down."""
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
        """``_in_pool_worker`` must return ``True`` from a pool worker"""
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tcp-dispatch")
        observed: dict[str, bool] = {}

        def _probe() -> None:
            observed["inside"] = lifecycle_mod._in_pool_worker(pool)
            real_threads = pool._threads
            try:
                pool._threads = set()  # type: ignore[attr-defined]
                observed["inside_empty_set"] = lifecycle_mod._in_pool_worker(pool)
            finally:
                pool._threads = real_threads

        try:
            assert lifecycle_mod._in_pool_worker(pool) is False, "main thread is not a pool worker, must be False"
            pool.submit(_probe).result(timeout=5.0)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        assert observed.get("inside") is True, "a running pool worker must be detected as inside the pool"
        assert observed.get("inside_empty_set") is True, (
            "a running pool worker must still be detected when ``pool._threads`` "
            "is momentarily empty (CPython 3.12 worker-start race), without the "
            "prefix fallback the quit-path self-join gate drains the pool from "
            "inside itself and burns the full 5s timeout"
        )


class TestRelaunchAckCoordination:
    """``_handle_relaunch_ack`` sets the per-instance ``_relaunch_ack_event``"""

    def test_handle_relaunch_ack_sets_event(self) -> None:
        """The handler sets ``_relaunch_ack_event`` and returns ``None``"""
        server = _make_server()
        server._relaunch_ack_event.clear()
        assert not server._relaunch_ack_event.is_set()
        result = server._handle_relaunch_ack(data=None, resp={"id": 1})
        assert result is None
        assert server._relaunch_ack_event.is_set()

    def test_wait_for_relaunch_ack_returns_true_when_acked_in_window(
        self,
    ) -> None:
        """When the ack arrives BEFORE the wait deadline, the wait"""
        server = _make_server()

        def _ack_after_short_delay() -> None:
            time.sleep(0.05)
            server._handle_relaunch_ack(data=None, resp={"id": 1})

        ack_thread = threading.Thread(target=_ack_after_short_delay, daemon=True)
        ack_thread.start()
        start = time.monotonic()
        acked = server.wait_for_relaunch_ack(timeout=2.0)
        elapsed = time.monotonic() - start
        assert acked is True
        # Returned well before the 2s deadline (ack arrived at ~50ms).
        assert elapsed < 1.0, f"wait_for_relaunch_ack should return as soon as the ack arrives; waited {elapsed:.3f}s"
        # Best-effort join so the daemon thread doesn't linger past
        ack_thread.join(timeout=1.0)

    def test_wait_for_relaunch_ack_returns_false_on_timeout(self) -> None:
        """When no ack arrives within the timeout, the wait returns"""
        server = _make_server()
        start = time.monotonic()
        acked = server.wait_for_relaunch_ack(timeout=0.1)
        elapsed = time.monotonic() - start
        assert acked is False
        # Tolerant lower bound: ``Event.wait`` may return a few ms early
        assert elapsed >= 0.05, f"waited only {elapsed:.3f}s for the 0.1s timeout"

    def test_wait_for_relaunch_ack_clears_event_before_waiting(self) -> None:
        """stale set state (it should wait for a fresh ack or time out)."""
        server = _make_server()
        server._relaunch_ack_event.set()  # stale ack from prior cycle
        start = time.monotonic()
        acked = server.wait_for_relaunch_ack(timeout=0.1)
        elapsed = time.monotonic() - start
        # The stale event was cleared; no fresh ack arrived → timeout.
        assert acked is False
        # Tolerant lower bound: ``Event.wait`` may return a few ms early
        assert elapsed >= 0.05, f"waited only {elapsed:.3f}s for the 0.1s timeout"

    def test_relaunch_ack_arrives_after_shutdown_started_is_safe(
        self,
    ) -> None:
        """Even after ``_shutdown_started`` is set (e.g. the Tauri host"""
        server = _make_server()
        server._shutdown_started.set()
        server._relaunch_ack_event.clear()
        # Must not raise, late acks are tolerated.
        result = server._handle_relaunch_ack(data=None, resp={"id": 1})
        assert result is None
        assert server._relaunch_ack_event.is_set()


class TestTrayStateHook:
    """``_hook_tray_set_state`` monkey-patches ``app.tray.set_state`` so"""

    def test_hook_wraps_set_state_and_pushes_status_change(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After the hook is installed, calling ``app.tray.set_state(state)``"""
        from voice_typer.server import event_bus

        server = _make_server()
        publish_calls: list[dict] = []
        monkeypatch.setattr(event_bus, "publish", lambda msg: publish_calls.append(msg))
        original_set_state = server.app.tray.set_state
        server._hook_tray_set_state()
        # Trigger a state change.
        server.app.tray.set_state(AppState.RECORDING, message="recording")
        original_set_state.assert_called_once_with(AppState.RECORDING, "recording")
        # The event was published with a status_change envelope.
        assert len(publish_calls) == 1
        assert publish_calls[0]["type"] == "status_change"
        assert publish_calls[0]["data"]["status"] == "recording"
        assert publish_calls[0]["data"]["message"] == "recording"

    def test_hook_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling ``_hook_tray_set_state`` twice must NOT double-wrap —"""
        from voice_typer.server import event_bus

        server = _make_server()
        publish_calls: list[dict] = []
        monkeypatch.setattr(event_bus, "publish", lambda msg: publish_calls.append(msg))
        server._hook_tray_set_state()
        server._hook_tray_set_state()  # second call, no-op
        server.app.tray.set_state(AppState.IDLE)
        # Exactly one publish despite double-hooking.
        assert len(publish_calls) == 1

    def test_hook_fires_before_ready_emitted_pushes_anyway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The hook is installed by ``start()`` BEFORE the first WS"""
        from voice_typer.server import event_bus

        server = _make_server()
        server._ready_emitted = False
        publish_calls: list[dict] = []
        monkeypatch.setattr(event_bus, "publish", lambda msg: publish_calls.append(msg))
        server._hook_tray_set_state()
        server.app.tray.set_state(AppState.ERROR, message="early failure")
        # Publish fired even though _ready_emitted is still False.
        assert len(publish_calls) == 1
        assert publish_calls[0]["data"]["status"] == "error"


class TestStdinIpcEnvVarGate:
    """unauthenticated stdin/stdout IPC listener. When ``_tcp_mode`` is"""

    def test_env_var_set_to_one_spawns_stdin_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``_tcp_mode=False`` AND ``VOICE_TYPER_ALLOW_STDIN_IPC=1``,"""
        # Force-import the module so we can patch its ``threading``
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        server = _make_server()
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
        """When ``_tcp_mode=False`` AND the env var is unset (or ``=0``),"""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        monkeypatch.delenv("VOICE_TYPER_ALLOW_STDIN_IPC", raising=False)
        # Setting the env var to "0" (not "1") must also refuse.
        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "0")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        server = _make_server()
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
                "stdin listener, the unauthenticated path is gated off."
            )
            assert server._stdin_thread is None
        finally:
            server.stop()

    def test_tcp_mode_skips_stdin_thread_regardless_of_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``_tcp_mode=True``, the stdin listener is never spawned"""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        # Even with the env var set, TCP mode must not spawn stdin.
        monkeypatch.setenv("VOICE_TYPER_ALLOW_STDIN_IPC", "1")
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        server = _make_server()
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


class TestBackgroundIntegrationsWiring:
    """``wire_background_integrations`` runs only from ``start()``."""

    _WIRE_THREAD_NAME = "ipc-cache-invalidator-wiring"

    def test_construction_does_not_spawn_background_integrator_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructing ``IPCServer`` must not spawn any thread, and"""
        created_threads: list[str] = []

        class _CountingThread:
            def __init__(self, target=None, name=None, daemon=False):
                self.name = name
                created_threads.append(name)

            def start(self):
                pass

            def is_alive(self):
                return False

            def join(self, timeout=None):
                pass

        monkeypatch.setattr(lifecycle_mod.threading, "Thread", _CountingThread)
        monkeypatch.setattr(
            __import__("voice_typer.server.ipc_server", fromlist=["threading"]).threading,
            "Thread",
            _CountingThread,
        )

        server, _app, _service = make_ipc_server_with_fakes(thread_registry=None)

        assert created_threads == [], (
            f"IPCServer construction spawned threads: {created_threads}. "
            "Construction must stay side-effect-free; deferred wiring "
            "belongs in wire_background_integrations()."
        )
        assert server._background_integrations_wired is False
        assert self._WIRE_THREAD_NAME not in created_threads

    def test_construction_without_start_leaves_integrations_unwired(self) -> None:
        """service-layer mic cache invalidator."""
        server, fake_app, _fake_service = make_ipc_server_with_fakes(thread_registry=None)

        assert server._background_integrations_wired is False
        # Construction alone must not reach that attribute chain.
        assert server.wire_background_integrations.__self__ is server

    def test_start_calls_wire_background_integrations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``start()`` must run ``wire_background_integrations`` as its"""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        server = _make_server()
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

        assert server._background_integrations_wired is False
        server.start()
        try:
            assert server._background_integrations_wired is True, (
                "start() must call wire_background_integrations(); "
                "the mic-cache invalidator would otherwise be dead code."
            )
            assert self._WIRE_THREAD_NAME in created_threads, (
                f"start() did not spawn the invalidator wiring thread (created: {created_threads})."
            )
        finally:
            server.stop()

    def test_double_start_wires_exactly_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A second ``start()`` after ``stop()`` must not re-spawn the"""
        import voice_typer.server.ipc_server as ipc_server_mod
        from voice_typer.server import event_bus

        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        server = _make_server()
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
        server.stop()
        server.start()
        try:
            wire_count = created_threads.count(self._WIRE_THREAD_NAME)
            assert wire_count == 1, (
                f"Expected the invalidator wiring thread exactly once "
                f"across two start() calls; got {wire_count} "
                f"(all: {created_threads})."
            )
            assert server._background_integrations_wired is True
        finally:
            server.stop()
