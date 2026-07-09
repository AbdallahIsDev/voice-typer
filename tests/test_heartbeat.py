"""RW-10: regression tests for the Electron-alive heartbeat watchdog.

If Electron crashes or is force-killed, the Python backend would
otherwise keep running with the mic stream open, hotkeys registered,
volume ducked, and the single-instance mutex held.  The next launch
hits ``ERROR_ALREADY_EXISTS`` and surfaces "Only one instance can
run", forcing the user to manually kill ``python.exe``.

The fix (RW-10) adds:

  1. A ``heartbeat`` IPC handler in ``ipc_server.py`` that updates
     ``self._last_heartbeat_at = time.monotonic()``.
  2. A daemon thread (``_heartbeat_loop``) that wakes every 5 seconds
     and calls ``_check_heartbeat_timeout``.  If more than 15 seconds
     (3 missed heartbeats) have elapsed since the last heartbeat,
     the watchdog calls ``self.app.quit()`` — which runs the shared
     ``_do_cleanup()`` path from RW-3 (restores volume, flushes
     recovery, releases the mutex, closes PortAudio).
  3. A guard so the watchdog does NOT fire before the first heartbeat
     has been received (so a slow Electron cold start doesn't trigger
     a false-positive exit).

These tests exercise:

  - The ``heartbeat`` IPC handler updates ``_last_heartbeat_at``.
  - The ``heartbeat`` command is registered in the dispatch table.
  - ``_check_heartbeat_timeout`` returns False / does NOT call
    ``app.quit()`` when no heartbeat has been received yet.
  - ``_check_heartbeat_timeout`` returns False within the 15s grace
    period after a heartbeat.
  - ``_check_heartbeat_timeout`` returns True and calls ``app.quit()``
    when the heartbeat is overdue (mocked ``time.monotonic`` to
    advance past the 15s timeout).
  - The ``_heartbeat_loop`` thread is started by ``start()`` as a
    daemon (so it doesn't block shutdown).
  - ``stop()`` signals the watchdog to exit (best-effort wakeup).
  - An end-to-end integration test that uses a real
    ``socket.socketpair`` to send a ``heartbeat`` command over the TCP
    transport and verifies the timestamp is updated.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from voice_typer.server.ipc_server import (
    _HEARTBEAT_INTERVAL_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
    IPCServer,
)
from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def server() -> typing.Iterator[IPCServer]:
    """Construct a real IPCServer with fake app/service for unit tests.

    The fake app is a ``MagicMock``, so ``app.quit()`` is a no-op call
    that we can assert on with ``app.quit.assert_called_once()``.  We
    set ``_running = True`` so the watchdog treats us as a live server
    (matching the post-start() state).

    Yields the server, then calls ``stop()`` in teardown to ensure the
    heartbeat thread is stopped and the push function is unregistered
    from the global ``_push_event_registry``. Without this teardown,
    a push function left in the registry would interfere with
    ``test_server.py::TestPushEventNow`` tests that assert on the
    registry state.
    """
    app = make_fake_app()
    service = make_fake_service()
    s = IPCServer(app, service=service)
    # Match the post-start() state so the watchdog treats us as live.
    s._running = True
    try:
        yield s
    finally:
        # Defensive teardown: stop the heartbeat thread + unregister
        # the push function even if the test called start() without
        # a matching stop().
        try:
            s.stop()
        except Exception:
            pass


# ── Handler tests ───────────────────────────────────────────────────────


class TestHeartbeatHandler:
    """RW-10: the ``heartbeat`` IPC handler updates the timestamp."""

    def test_handler_updates_last_heartbeat_at(self, server: IPCServer) -> None:
        """Calling ``_handle_heartbeat`` records ``time.monotonic()``.

        Before the first call, ``_last_heartbeat_at`` is ``None`` (the
        watchdog won't fire).  After the call, it's a float — arming
        the watchdog.
        """
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
        assert (
            IPCServer._COMMAND_REGISTRY["heartbeat"] == "_handle_heartbeat"
        )

    def test_dispatch_routes_heartbeat_to_handler(self, server: IPCServer) -> None:
        """``_dispatch({"type": "heartbeat"})`` invokes the handler.

        This is the path that production uses — Electron's
        ``sendToPython({type: "heartbeat"})`` lands in ``_dispatch``
        via the TCP read loop.  Verifies the registry wiring.
        """
        assert server._last_heartbeat_at is None

        result = server._dispatch({"type": "heartbeat", "id": 42})

        assert result is not None
        assert result["type"] == "heartbeat_ack"
        assert result["id"] == 42
        assert server._last_heartbeat_at is not None

    def test_repeated_heartbeat_calls_update_timestamp(self, server: IPCServer) -> None:
        """Each heartbeat call updates ``_last_heartbeat_at``.

        The watchdog compares against the most recent heartbeat, so
        repeated calls must overwrite (not accumulate).
        """
        # First heartbeat at t=100.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=100.0
        ):
            server._handle_heartbeat(None, {"id": 1})
        first = server._last_heartbeat_at
        assert first == 100.0

        # Second heartbeat at t=105.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=105.0
        ):
            server._handle_heartbeat(None, {"id": 2})
        second = server._last_heartbeat_at
        assert second == 105.0
        assert second > first


# ── Watchdog timeout logic ──────────────────────────────────────────────


class TestHeartbeatWatchdog:
    """RW-10: the watchdog fires only when the heartbeat is overdue."""

    def test_does_not_fire_before_first_heartbeat(self, server: IPCServer) -> None:
        """The watchdog must NOT fire before Electron's first heartbeat.

        This is the critical guard: a slow Electron cold start (10+
        seconds for the torch import on first launch) must not cause
        the backend to exit prematurely.  ``_last_heartbeat_at`` is
        ``None`` until the first heartbeat lands, and the watchdog
        checks for this explicitly.
        """
        assert server._last_heartbeat_at is None

        # Even if a huge amount of time has passed (simulated), the
        # watchdog must refuse to fire.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=10_000.0
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is False
        server.app.quit.assert_not_called()

    def test_does_not_fire_within_grace_period(self, server: IPCServer) -> None:
        """Within the 15s grace period, the watchdog must not fire.

        A heartbeat received recently means Electron is alive — even
        if we're 14.9s past it, we're still inside the 15s timeout
        (3 missed heartbeats).
        """
        # Heartbeat at t=100.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=100.0
        ):
            server._handle_heartbeat(None, {"id": 1})

        # 14.9s later — just inside the 15s timeout.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS - 0.1,
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is False
        server.app.quit.assert_not_called()

    def test_fires_after_timeout(self, server: IPCServer) -> None:
        """The watchdog fires ``app.quit()`` after the 15s timeout.

        Mocks ``time.monotonic`` so we can simulate the timeout
        without waiting 15 real seconds.  This is the core regression
        test: a crashed Electron (no more heartbeats) must cause the
        backend to clean up and exit via the normal ``app.quit()``
        path.
        """
        # First heartbeat at t=100.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=100.0
        ):
            server._handle_heartbeat(None, {"id": 1})

        # Simulate that 20 seconds have passed (5s past the 15s
        # timeout = 3 missed heartbeats + 1 grace heartbeat).
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=120.0
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is True
        server.app.quit.assert_called_once_with()

    def test_fires_exactly_at_timeout_boundary(self, server: IPCServer) -> None:
        """The watchdog fires as soon as the timeout is exceeded.

        Tests the ``>`` (strictly-greater-than) comparison: at exactly
        ``timeout + epsilon``, the watchdog fires.  This guards
        against off-by-one errors in the comparison.
        """
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=100.0
        ):
            server._handle_heartbeat(None, {"id": 1})

        # Exactly at the timeout boundary (15.0s later) — strictly
        # greater-than means we need to be even a hair past it.
        with patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 0.001,
        ):
            fired = server._check_heartbeat_timeout()

        assert fired is True
        server.app.quit.assert_called_once_with()

    def test_quit_exception_is_swallowed(self, server: IPCServer) -> None:
        """If ``app.quit()`` raises, the watchdog must not propagate.

        The daemon thread has no caller to catch the exception; if it
        propagated, it would kill the thread silently and the process
        would never exit.  The ``try/except`` around ``app.quit()``
        logs the exception but still returns ``True``.
        """
        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=100.0
        ):
            server._handle_heartbeat(None, {"id": 1})

        # Make app.quit() raise.
        server.app.quit.side_effect = RuntimeError("quit failed")

        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=120.0
        ):
            # Must NOT raise.
            fired = server._check_heartbeat_timeout()

        assert fired is True
        server.app.quit.assert_called_once_with()

    def test_does_not_fire_when_app_already_shutting_down(
        self, server: IPCServer
    ) -> None:
        """The watchdog should not trigger a redundant quit.

        If ``app.quit()`` was already called (e.g. by the tray Quit
        menu item), ``app._shutting_down`` is ``True`` and the real
        ``VoiceTyperApp.quit()`` is a no-op.  With the fake app we
        simulate this by checking that the watchdog still calls
        ``app.quit()`` — the real ``quit()`` is itself idempotent
        (early-returns on ``_shutting_down``), so the call is harmless.

        This test documents that the watchdog does NOT pre-check
        ``_shutting_down`` — it relies on ``app.quit()``'s own
        idempotency guard.  This is intentional: the watchdog must
        trigger cleanup when it fires, and a redundant call is
        cheaper than a missed cleanup.
        """
        # Simulate the app already being in shutdown.
        server.app._shutting_down = True

        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=100.0
        ):
            server._handle_heartbeat(None, {"id": 1})

        with patch(
            "voice_typer.server.ipc_server.time.monotonic", return_value=120.0
        ):
            fired = server._check_heartbeat_timeout()

        # Watchdog still fires — relies on app.quit() being idempotent.
        assert fired is True
        server.app.quit.assert_called_once_with()


# ── Daemon thread lifecycle ─────────────────────────────────────────────


class TestHeartbeatThreadLifecycle:
    """RW-10: the watchdog thread must be a daemon and exit on stop()."""

    def test_start_spawns_daemon_thread(self) -> None:
        """``start()`` must spawn the heartbeat thread as a daemon.

        Daemon threads don't block process exit — critical because
        the watchdog sleeps for 5 seconds between checks; if it were
        a non-daemon thread, the process would hang on shutdown until
        the next tick.
        """
        app = make_fake_app()
        service = make_fake_service()
        s = IPCServer(app, service=service)

        # Capture the thread object so we can assert on its daemon
        # flag after start() returns.  We patch threading.Thread with
        # a subclass that records instances by name.
        original_thread = threading.Thread
        captured: list[threading.Thread] = []

        class CapturingThread(original_thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                if kwargs.get("name") == "heartbeat-watchdog":
                    captured.append(self)

        with patch(
            "voice_typer.server.ipc_server.threading.Thread", CapturingThread
        ):
            s.start()

        try:
            assert len(captured) == 1, (
                "start() must spawn exactly one heartbeat-watchdog thread"
            )
            assert captured[0].daemon is True, (
                "heartbeat-watchdog thread MUST be a daemon so it doesn't "
                "block shutdown"
            )
            assert captured[0].is_alive(), (
                "heartbeat-watchdog thread should be running after start()"
            )
        finally:
            s.stop()
            # Give the thread a moment to exit (it sleeps on the
            # stop event with a 5s timeout; setting the event wakes
            # it immediately).
            captured[0].join(timeout=2.0)

    def test_stop_signals_watchdog_to_exit(self) -> None:
        """``stop()`` sets the stop event so the watchdog exits promptly.

        Without this, the watchdog would linger up to 5 seconds past
        shutdown.  It's a daemon thread, so it wouldn't block process
        exit — but explicit shutdown is cleaner for test start/stop
        cycles.
        """
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
        assert not heartbeat_thread.is_alive(), (
            "heartbeat-watchdog thread did not exit after stop()"
        )

    def test_watchdog_loop_calls_check_heartbeat_timeout(self) -> None:
        """The loop body delegates to ``_check_heartbeat_timeout``.

        Verifies the wiring: the loop calls the check method on each
        tick.  Uses a real (slow) interval — patched to be fast for
        the test — and asserts the check is invoked at least once
        before stop().
        """
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
        with patch(
            "voice_typer.server.ipc_server._HEARTBEAT_INTERVAL_SECONDS", 0.05
        ), patch.object(s, "_check_heartbeat_timeout", spy):
            s.start()
            # Wait long enough for at least 2 ticks.
            time.sleep(0.2)
            s.stop()

        assert len(check_calls) >= 2, (
            f"watchdog loop should have called _check_heartbeat_timeout "
            f"at least twice in 200ms (got {len(check_calls)} calls)"
        )

    def test_watchdog_loop_exits_after_quit_triggered(self) -> None:
        """When ``_check_heartbeat_timeout`` returns True, the loop exits.

        Simulates a timeout firing: the spy returns True on the first
        call, and the loop must exit immediately (return) rather than
        continuing to tick.
        """
        app = make_fake_app()
        service = make_fake_service()
        s = IPCServer(app, service=service)

        check_calls: list[bool] = []

        def always_fires() -> bool:
            check_calls.append(True)
            return True  # simulate timeout fired

        with patch(
            "voice_typer.server.ipc_server._HEARTBEAT_INTERVAL_SECONDS", 0.05
        ), patch.object(s, "_check_heartbeat_timeout", always_fires):
            s.start()
            # Wait long enough for the first tick to fire.
            time.sleep(0.2)

        # The loop should have called _check_heartbeat_timeout exactly
        # once (returned True → loop exited).  We don't call stop()
        # because the loop already exited; but we set the stop event
        # just in case the loop is still alive (defensive).
        s._heartbeat_stop_event.set()
        if s._heartbeat_thread is not None:
            s._heartbeat_thread.join(timeout=2.0)

        # TEST-ISOLATION-FIX: call stop() to unregister the push function
        # from the global _push_event_registry. Without this, the push
        # function remains registered and interferes with
        # test_server.py::TestPushEventNow tests that assert on the
        # registry state.
        try:
            s.stop()
        except Exception:
            pass

        assert len(check_calls) == 1, (
            f"watchdog loop should have called _check_heartbeat_timeout "
            f"exactly once (returned True → exit) but called it "
            f"{len(check_calls)} times"
        )


# ── Integration test: real TCP socketpair ──────────────────────────────


def test_heartbeat_over_real_tcp_socket_updates_timestamp() -> None:
    """End-to-end: a ``heartbeat`` command sent over TCP updates the timestamp.

    Spins up the real ``_handle_tcp_connection`` path with a real
    ``socket.socketpair`` so we exercise the same JSON-line dispatch
    path that production uses.  Electron's ``sendToPython({type:
    "heartbeat"})`` lands here.
    """
    app = make_fake_app()
    service = make_fake_service()
    server = IPCServer(app, service=service)
    server._running = True

    assert server._last_heartbeat_at is None

    client_sock, server_sock = socket.socketpair()

    # Run the connection handler in a thread — it blocks on readline()
    # until the client closes.
    handler_thread = threading.Thread(
        target=server._handle_tcp_connection,
        args=(server_sock, ("127.0.0.1", 0), ""),
        daemon=True,
    )
    handler_thread.start()

    # Send a heartbeat command.  The dispatcher routes it to
    # _handle_heartbeat, which updates _last_heartbeat_at.
    client_sock.sendall(b'{"type":"heartbeat","id":1}\n')

    # Wait for the timestamp to be updated (the handler runs on the
    # TCP thread; we poll from the main thread).
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if server._last_heartbeat_at is not None:
            break
        time.sleep(0.02)

    assert server._last_heartbeat_at is not None, (
        "heartbeat command over TCP did not update _last_heartbeat_at"
    )

    # Read the response from the client side — should be a
    # heartbeat_ack with id=1.
    client_sock.settimeout(2.0)
    response_line = b""
    while b"\n" not in response_line:
        chunk = client_sock.recv(4096)
        if not chunk:
            break
        response_line += chunk
    response = json.loads(response_line.decode("utf-8").strip())
    assert response["type"] == "heartbeat_ack"
    assert response["id"] == 1

    # Close the client side — server's readline() returns "" (EOF).
    client_sock.close()
    handler_thread.join(timeout=5.0)
    assert not handler_thread.is_alive()
