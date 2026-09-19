"""regression tests for the predecessor-alive heartbeat watchdog."""

from __future__ import annotations

import contextlib
import threading
import time
import typing
from unittest.mock import patch

import pytest
from voice_typer.server.ipc_server import (
    _HEARTBEAT_TIMEOUT_SECONDS,
    IPCServer,
)

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service


@pytest.fixture
def server() -> typing.Iterator[IPCServer]:
    """Construct a real IPCServer with fake app/service for unit tests."""
    app = make_fake_app()
    service = make_fake_service()
    s = IPCServer(app, service=service)
    # Match the post-start() state so the watchdog treats us as live.
    s._running = True
    try:
        yield s
    finally:
        # Defensive teardown: stop the heartbeat thread + unregister
        with contextlib.suppress(Exception):
            s.stop()


class TestHeartbeatHandler:
    """the ``heartbeat`` IPC handler updates the timestamp."""

    def test_handler_updates_last_heartbeat_at(self, server: IPCServer) -> None:
        """Calling ``_handle_heartbeat`` records ``time.monotonic()``."""
        assert server._last_heartbeat_at is None

        resp = {"id": 1}
        result = server._handle_heartbeat(None, resp)

        assert server._last_heartbeat_at is not None
        assert isinstance(server._last_heartbeat_at, float)
        # Response is well-formed so sendToPython() can resolve.
        assert result is resp
        assert result["type"] == "heartbeat_ack"

    def test_handler_registers_in_command_registry(self) -> None:
        """The ``heartbeat`` command must be in the dispatch registry."""
        assert "heartbeat" in IPCServer._COMMAND_REGISTRY
        assert IPCServer._COMMAND_REGISTRY["heartbeat"] == "_handle_heartbeat"

    def test_dispatch_routes_heartbeat_to_handler(self, server: IPCServer) -> None:
        """``_dispatch({\"type\": \"heartbeat\"})`` invokes the handler."""
        assert server._last_heartbeat_at is None

        result = server._dispatch({"type": "heartbeat", "id": 42})

        assert result is not None
        assert result["type"] == "heartbeat_ack"
        assert result["id"] == 42
        assert server._last_heartbeat_at is not None

    def test_repeated_heartbeat_calls_update_timestamp(self, server: IPCServer) -> None:
        """Each heartbeat call updates ``_last_heartbeat_at``."""
        # First heartbeat at t=100.
        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
            server._handle_heartbeat(None, {"id": 1})
        first = server._last_heartbeat_at
        assert first == 100.0

        # Second heartbeat at t=105.
        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=105.0):
            server._handle_heartbeat(None, {"id": 2})
        second = server._last_heartbeat_at
        assert second == 105.0
        assert second > first


class TestHeartbeatWatchdog:
    """the watchdog fires only when the heartbeat is overdue."""

    @pytest.fixture(autouse=True)
    def _park_force_exit_thread(self, monkeypatch) -> typing.Iterator[None]:
        """Park the ``heartbeat-force-exit`` daemon thread so it can't"""
        import voice_typer.server.ipc.lifecycle as lifecycle_mod

        monkeypatch.setattr(lifecycle_mod, "_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS", 10000)
        yield

    def test_does_not_fire_before_first_heartbeat(self, server: IPCServer) -> None:
        """The watchdog must NOT fire before the predecessor's first heartbeat."""
        assert server._last_heartbeat_at is None

        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=10_000.0):
            fired = server._check_heartbeat_timeout()

        assert fired is False
        server.app.quit.assert_not_called()

    def test_does_not_fire_within_grace_period(self, server: IPCServer) -> None:
        """Within the grace period, the watchdog must not fire."""
        # Heartbeat at t=100.
        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
            server._handle_heartbeat(None, {"id": 1})

        # 0.1s before the timeout, still inside the grace period.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS - 0.1,
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is False
        server.app.quit.assert_not_called()

    def test_fires_after_timeout(self, server: IPCServer) -> None:
        """The watchdog fires ``app.quit()`` after the timeout elapses."""
        # First heartbeat at t=100.
        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
            server._handle_heartbeat(None, {"id": 1})

        # Simulate that the timeout + 5s has elapsed (clearly past
        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is True
        server.app.quit.assert_called_once_with()

    def test_fires_exactly_at_timeout_boundary(self, server: IPCServer) -> None:
        """The watchdog fires as soon as the timeout is exceeded."""
        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
            server._handle_heartbeat(None, {"id": 1})

        # Exactly at the timeout boundary (15.0s later), strictly
        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 0.001,
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is True
        server.app.quit.assert_called_once_with()

    def test_quit_exception_is_swallowed(self, server: IPCServer) -> None:
        """If ``app.quit()`` raises, the watchdog must not propagate."""
        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
            server._handle_heartbeat(None, {"id": 1})

        # Make app.quit() raise.
        server.app.quit.side_effect = RuntimeError("quit failed")

        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ):
            # Must NOT raise.
            fired = server._check_heartbeat_timeout()

        assert fired is True
        server.app.quit.assert_called_once_with()

    def test_does_not_fire_when_app_already_shutting_down(self, server: IPCServer) -> None:
        """The watchdog should not trigger a redundant quit."""
        # Simulate the app already being in shutdown.
        server.app._shutting_down = True

        with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
            server._handle_heartbeat(None, {"id": 1})

        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ):
            fired = server._check_heartbeat_timeout()

        # Watchdog still fires, relies on app.quit() being idempotent.
        assert fired is True
        server.app.quit.assert_called_once_with()


class TestHeartbeatThreadLifecycle:
    """the watchdog thread must be a daemon and exit on stop()."""

    @pytest.fixture(autouse=True)
    def _clear_tauri_sidecar_env(self, monkeypatch) -> typing.Iterator[None]:
        """Ensure ``TAURI_SIDECAR`` is unset so ``start()`` spawns the watchdog."""
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)
        yield

    def test_start_spawns_daemon_thread(self) -> None:
        """``start()`` must spawn the heartbeat thread as a daemon."""
        app = make_fake_app()
        service = make_fake_service()
        s = IPCServer(app, service=service)

        # Capture the thread object so we can assert on its daemon
        original_thread = threading.Thread
        captured: list[threading.Thread] = []

        class CapturingThread(original_thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if kwargs.get("name") == "heartbeat-watchdog":
                    captured.append(self)

        with patch("voice_typer.server.ipc_server.threading.Thread", CapturingThread):
            s.start()

        try:
            assert len(captured) == 1, "start() must spawn exactly one heartbeat-watchdog thread"
            assert captured[0].daemon is True, "heartbeat-watchdog thread MUST be a daemon so it doesn't block shutdown"
            assert captured[0].is_alive(), "heartbeat-watchdog thread should be running after start()"
        finally:
            s.stop()
            captured[0].join(timeout=2.0)

    def test_stop_signals_watchdog_to_exit(self) -> None:
        """``stop()`` sets the stop event so the watchdog exits promptly."""
        app = make_fake_app()
        service = make_fake_service()
        s = IPCServer(app, service=service)
        s.start()
        heartbeat_thread = s._heartbeat_thread
        assert heartbeat_thread is not None
        assert heartbeat_thread.is_alive()

        s.stop()

        # The stop event must be set.
        assert s._heartbeat_stop_event.is_set()
        # Thread exits within 2 seconds (well under the 5s wait).
        heartbeat_thread.join(timeout=2.0)
        assert not heartbeat_thread.is_alive(), "heartbeat-watchdog thread did not exit after stop()"

    def test_watchdog_loop_calls_check_heartbeat_timeout(self) -> None:
        """The loop body delegates to ``_check_heartbeat_timeout``."""
        app = make_fake_app()
        service = make_fake_service()
        s = IPCServer(app, service=service)

        check_calls: list[bool] = []
        original_check = s._check_heartbeat_timeout

        def spy() -> bool:
            result = original_check()
            check_calls.append(result)
            return result

        # Patch the interval to 50ms so we don't wait 5s per tick.
        with (
            patch("voice_typer.server.ipc.lifecycle._HEARTBEAT_INTERVAL_SECONDS", 0.05),
            patch.object(s, "_check_heartbeat_timeout", spy),
        ):
            s.start()
            # Wait for at least 2 ticks (poll instead of fixed sleep).
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if len(check_calls) >= 2:
                    break
                time.sleep(0.02)
            s.stop()

        assert len(check_calls) >= 2, (
            f"watchdog loop should have called _check_heartbeat_timeout "
            f"at least twice in 200ms (got {len(check_calls)} calls)"
        )

    def test_watchdog_loop_exits_after_quit_triggered(self) -> None:
        """When ``_check_heartbeat_timeout`` returns True, the loop exits."""
        app = make_fake_app()
        service = make_fake_service()
        s = IPCServer(app, service=service)

        check_calls: list[bool] = []

        def always_fires() -> bool:
            check_calls.append(True)
            return True  # simulate timeout fired

        with (
            patch("voice_typer.server.ipc.lifecycle._HEARTBEAT_INTERVAL_SECONDS", 0.05),
            patch.object(s, "_check_heartbeat_timeout", always_fires),
        ):
            s.start()
            # Wait for the first tick to fire (poll instead of fixed sleep).
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if check_calls:
                    break
                time.sleep(0.02)

        # The loop should have called _check_heartbeat_timeout exactly
        s._heartbeat_stop_event.set()
        if s._heartbeat_thread is not None:
            s._heartbeat_thread.join(timeout=2.0)

        # TEST-ISOLATION-FIX: call stop() to unregister the push function
        with contextlib.suppress(Exception):
            s.stop()

        assert len(check_calls) == 1, (
            f"watchdog loop should have called _check_heartbeat_timeout "
            f"exactly once (returned True → exit) but called it "
            f"{len(check_calls)} times"
        )
