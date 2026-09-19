"""regression: heartbeat watchdog force-exits if ``tray.stop()`` hangs."""

from __future__ import annotations

import contextlib
import threading
import time
from unittest.mock import patch

import pytest
from voice_typer.server.ipc_server import (
    _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS,
    _HEARTBEAT_TIMEOUT_SECONDS,
    IPCServer,
)

from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service


@pytest.fixture
def server() -> IPCServer:
    """Construct a real IPCServer with fake app/service for unit tests."""
    app = make_fake_app()
    service = make_fake_service()
    s = IPCServer(app, service=service)
    s._running = True
    yield s
    # ``stop()`` is idempotent and safe to call even when ``start()``
    with contextlib.suppress(Exception):
        s.stop()


def test_force_exit_thread_scheduled_after_quit(server: IPCServer) -> None:
    """When the timeout fires, a ``heartbeat-force-exit`` daemon thread is started."""
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    # Capture threads created inside _check_heartbeat_timeout.
    created_threads: list[threading.Thread] = []
    original_thread_init = threading.Thread.__init__

    def capturing_init(self, *args, **kwargs):
        original_thread_init(self, *args, **kwargs)

    def capturing_start(self):
        if self.name == "heartbeat-force-exit":
            created_threads.append(self)

    # Patch time.sleep inside the daemon thread target so it doesn't
    with (
        patch.object(threading.Thread, "__init__", capturing_init),
        patch.object(threading.Thread, "start", capturing_start),
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
    ):
        fired = server._check_heartbeat_timeout()

    assert fired is True
    server.app.quit.assert_called_once_with()
    assert len(created_threads) == 1, (
        "exactly one heartbeat-force-exit thread should be scheduled "
        "after app.quit() is called from the heartbeat watchdog"
    )
    t = created_threads[0]
    assert t.name == "heartbeat-force-exit"
    assert t.daemon is True, (
        "force-exit thread MUST be a daemon so it doesn't block the process exit it's trying to trigger"
    )


def test_force_exit_thread_NOT_scheduled_when_timeout_does_not_fire(  # noqa: N802
    server: IPCServer,
) -> None:
    """When the watchdog returns False (no timeout), no force-exit thread is started."""
    # Arm the watchdog with a heartbeat at t=100.
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    created_threads: list[threading.Thread] = []

    def capturing_start(self):
        if self.name == "heartbeat-force-exit":
            created_threads.append(self)

    # Now check at t=101 (well within the 45s timeout, should NOT fire).
    with (
        patch.object(threading.Thread, "start", capturing_start),
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=101.0,
        ),
    ):
        fired = server._check_heartbeat_timeout()

    assert fired is False
    server.app.quit.assert_not_called()
    assert len(created_threads) == 0, (
        "no force-exit thread should be scheduled when the timeout did "
        "not fire, only schedule when app.quit() is actually called"
    )


def test_force_exit_thread_NOT_scheduled_before_first_heartbeat(  # noqa: N802
    server: IPCServer,
) -> None:
    """No force-exit thread before the predecessor's first heartbeat (slow cold-start guard)."""
    assert server._last_heartbeat_at is None

    created_threads: list[threading.Thread] = []

    def capturing_start(self):
        if self.name == "heartbeat-force-exit":
            created_threads.append(self)

    with (
        patch.object(threading.Thread, "start", capturing_start),
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=10_000.0,
        ),
    ):
        fired = server._check_heartbeat_timeout()

    assert fired is False
    server.app.quit.assert_not_called()
    assert len(created_threads) == 0


def test_force_exit_calls_os_exit_with_code_1_after_grace_period(
    server: IPCServer,
) -> None:
    """The force-exit thread calls ``os._exit(1)`` after the grace period."""
    # Arm + fire the watchdog.
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    exit_calls: list[int] = []

    # Patch os._exit inside the ipc_server module (the daemon thread
    import voice_typer.server.ipc_server as ipc_mod

    fake_grace = 0.05

    with (
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
        patch(
            "voice_typer.server.ipc.lifecycle._HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            fake_grace,
        ),
        patch.object(ipc_mod.os, "_exit", lambda code: exit_calls.append(code)),
    ):
        fired = server._check_heartbeat_timeout()
        assert fired is True

        # The force-exit thread is now sleeping for fake_grace seconds
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not exit_calls:
            time.sleep(0.01)

    assert exit_calls == [1], (
        f"force-exit thread should have called os._exit(1) exactly once after the grace period; got: {exit_calls}"
    )


def test_force_exit_thread_does_not_fire_if_quit_exits_process_first(
    server: IPCServer,
) -> None:
    """If ``app.quit()`` causes the process to exit naturally, the force-exit thread is moot."""
    # The scenario where quit() "succeeds" is the same as the scenario
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    exit_calls: list[int] = []
    import voice_typer.server.ipc_server as ipc_mod

    with (
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
        patch(
            "voice_typer.server.ipc.lifecycle._HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            0.05,
        ),
        patch.object(ipc_mod.os, "_exit", lambda code: exit_calls.append(code)),
    ):
        fired = server._check_heartbeat_timeout()
        assert fired is True
        # Wait for the thread.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not exit_calls:
            time.sleep(0.01)

    assert exit_calls == [1]


def test_force_exit_thread_logs_error_before_exiting(server: IPCServer) -> None:
    """The force-exit thread logs an error message before calling ``os._exit``."""
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    import voice_typer.server.ipc_server as ipc_mod

    log_calls: list[str] = []

    with (
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
        patch(
            "voice_typer.server.ipc.lifecycle._HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            0.05,
        ),
        patch.object(ipc_mod.os, "_exit", lambda code: None),
        patch.object(ipc_mod.log, "error", lambda msg, *args, **kw: log_calls.append(msg)),
    ):
        fired = server._check_heartbeat_timeout()
        assert fired is True
        # Wait for the thread.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not log_calls:
            time.sleep(0.01)

    assert any("force-exiting" in m for m in log_calls), (
        f"force-exit thread should log an error mentioning 'force-exiting' "
        f"before calling os._exit; got log calls: {log_calls}"
    )


def test_force_exit_thread_scheduling_failure_is_swallowed(
    server: IPCServer,
) -> None:
    """If ``threading.Thread`` itself raises, the watchdog must not propagate."""
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    class _BoomThread:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("threading.Thread is broken")

        def start(self):
            raise RuntimeError("never reached")

    with (
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
        patch("threading.Thread", _BoomThread),
    ):
        # Must NOT raise.
        fired = server._check_heartbeat_timeout()

    assert fired is True
    server.app.quit.assert_called_once_with()


def test_default_grace_period_is_10_seconds() -> None:
    """The default grace period is 10 seconds (per the spec)."""
    assert _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS == 10.0, (
        "default grace period must be 10s per spec, if this is "
        "changed, update the comment in ipc_server.py and the "
        "rationale in review.md"
    )


def test_force_exit_does_not_fire_when_cleanup_completed_before_grace(
    server: IPCServer,
) -> None:
    """FR-11: when ``_do_cleanup()`` completes within the grace window"""
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    exit_calls: list[int] = []
    import voice_typer.server.ipc_server as ipc_mod

    # Simulate cleanup finishing before the grace elapses.
    server._shutdown_completed_event.set()

    with (
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
        patch(
            "voice_typer.server.ipc.lifecycle._HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            0.05,
        ),
        patch.object(ipc_mod.os, "_exit", lambda code: exit_calls.append(code)),
    ):
        fired = server._check_heartbeat_timeout()
        assert fired is True

        for _ in range(20):
            if exit_calls:
                break
            time.sleep(0.01)

    assert exit_calls == [], (
        f"FR-11: force-exit thread must NOT call os._exit when cleanup "
        f"completed within the grace window; got: {exit_calls}"
    )


def test_force_exit_still_fires_when_cleanup_stuck(server: IPCServer) -> None:
    """FR-11: when cleanup is stuck (the shutdown-completion event is"""
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    exit_calls: list[int] = []
    import voice_typer.server.ipc_server as ipc_mod

    # The completion event is deliberately NOT set (cleanup hung).
    assert not server._shutdown_completed_event.is_set()

    with (
        patch(
            "voice_typer.server.ipc_server.time.monotonic",
            return_value=100.0 + _HEARTBEAT_TIMEOUT_SECONDS + 5.0,
        ),
        patch(
            "voice_typer.server.ipc.lifecycle._HEARTBEAT_FORCE_EXIT_GRACE_SECONDS",
            0.05,
        ),
        patch.object(ipc_mod.os, "_exit", lambda code: exit_calls.append(code)),
    ):
        fired = server._check_heartbeat_timeout()
        assert fired is True

        # (time.monotonic is globally patched to a constant inside this
        for _ in range(200):
            if exit_calls:
                break
            time.sleep(0.01)

    assert exit_calls == [1], (
        f"FR-11: stuck cleanup must still trigger os._exit(1) after the grace period; got: {exit_calls}"
    )


def test_do_cleanup_sets_shutdown_completed_event(monkeypatch) -> None:
    """FR-11: the shared cleanup path sets the shutdown-completion event"""
    from unittest.mock import MagicMock

    from voice_typer.server.ipc_server import IPCServer
    from voice_typer.server.shutdown_controller import ShutdownController

    server = IPCServer(MagicMock(), service=MagicMock())
    assert not server._shutdown_completed_event.is_set()

    app = MagicMock()
    app._cleanup_done = False
    app._ipc_server = server
    controller = ShutdownController(app)

    monkeypatch.setattr(controller, "_drain_ws_dispatch_pool", lambda app: None)
    monkeypatch.setattr(controller, "_build_sequenced_plan", lambda *a, **k: [])
    monkeypatch.setattr(controller, "_run_plan", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_build_parallel_plan", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_late_bookend_tray_stop", lambda app: None)

    controller._do_cleanup()

    assert server._shutdown_completed_event.is_set(), (
        "FR-11: _do_cleanup must set the shutdown-completion event when it "
        "finishes, otherwise the heartbeat force-exit watchdog stays armed "
        "and kills a healthy-but-slow quit mid-cleanup"
    )
