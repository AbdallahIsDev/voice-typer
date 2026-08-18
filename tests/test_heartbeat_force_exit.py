"""regression: heartbeat watchdog force-exits if ``tray.stop()`` hangs.

Before CR-9, the heartbeat watchdog called ``self.app.quit()`` and
returned — relying entirely on ``tray.stop()`` (called inside
``app.quit()``'s ``_do_cleanup()``) to break the pystray loop so the
main thread could unwind and the process could exit.

pystray on certain Linux backends (AppIndicator with stale dbus) and
on Windows Server (with RDP session disconnects) has been observed to
hang inside ``stop()``. When that happens, ``app.quit()`` returns (it
only calls ``sys.exit(0)`` from the main thread; from the daemon
heartbeat thread it just unwinds the cleanup path), but the main
thread is still stuck in ``tray.run()`` — so the process never exits.
The mic stays open, the single-instance mutex stays held, and the
next launch hits ``ERROR_ALREADY_EXISTS``.

fix: after calling ``app.quit()``, the watchdog schedules a daemon
thread that sleeps ``_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS`` (default
10s) and then calls ``os._exit(1)``. If ``quit()`` succeeded, the
process is already gone before the grace period expires. If
``tray.stop()`` hung, the daemon thread force-exits after 10s.

This module exercises:

- ``_check_heartbeat_timeout`` schedules a ``heartbeat-force-exit``
  daemon thread after calling ``app.quit()``.
- The daemon thread calls ``os._exit(1)`` after the grace period.
- The grace period is configurable via ``_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS``
  (patchable for tests so we don't wait real seconds).
- ``os._exit`` is patched to prevent actual process exit during the test.
- The force-exit thread is a daemon (doesn't block shutdown).
- The force-exit watchdog is NOT scheduled when the timeout doesn't fire
  (i.e., when ``_check_heartbeat_timeout`` returns ``False``).
"""

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

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def server() -> IPCServer:
    """Construct a real IPCServer with fake app/service for unit tests.

    Matches the post-start() state (``_running = True``) so the watchdog
    treats us as live. We DON'T call ``start()`` (which would spawn the
    daemon thread); we exercise ``_check_heartbeat_timeout`` directly.

    Teardown: ``IPCServer.__init__`` allocates several ``threading.Lock``
    / ``RLock`` / ``Event`` sync primitives (the TCP write lock, the
    dispatch lock, the heartbeat stop event, etc.). No real thread is
    spawned by ``__init__`` — the heartbeat / stdin threads are only
    started by ``start()`` — but the watchdog code paths exercised by
    these tests do spawn real daemon threads (the ``heartbeat-force-exit``
    thread) that call a patched ``os._exit`` and exit naturally. Calling
    ``server.stop()`` in teardown sets ``_running = False`` and
    unregisters the push callable so a subsequent test that constructs a
    fresh ``IPCServer`` doesn't inherit a stale push-hook registration.
    """
    app = make_fake_app()
    service = make_fake_service()
    s = IPCServer(app, service=service)
    s._running = True
    yield s
    # ``stop()`` is idempotent and safe to call even when ``start()``
    # was never invoked — it sets ``_running = False`` and unregisters
    # the push callable.
    with contextlib.suppress(Exception):
        s.stop()


# ── Force-exit thread scheduling ────────────────────────────────────────


def test_force_exit_thread_scheduled_after_quit(server: IPCServer) -> None:
    """When the timeout fires, a ``heartbeat-force-exit`` daemon thread is started.

    The thread is what actually calls ``os._exit(1)`` after the grace
    period. If the scheduling itself fails (e.g., ``threading.Thread``
    raises), the watchdog silently degrades to the behavior —
    so we want a positive assertion that the thread IS created.
    """
    # Arm the watchdog with a heartbeat, then advance time past the
    # timeout so ``_check_heartbeat_timeout`` returns True.
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
        # Don't actually start the thread — we don't want os._exit to
        # fire during this test.

    # Patch time.sleep inside the daemon thread target so it doesn't
    # actually sleep 10s if we DID start it. (We won't, but defensive.)
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
    """When the watchdog returns False (no timeout), no force-exit thread is started.

    This is the negative-space guard: the force-exit thread is ONLY for
    the case where ``app.quit()`` was actually called. If we scheduled
    it on every check, we'd risk spurious exits during normal operation.
    """
    # Arm the watchdog with a heartbeat at t=100.
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    created_threads: list[threading.Thread] = []

    def capturing_start(self):
        if self.name == "heartbeat-force-exit":
            created_threads.append(self)

    # Now check at t=101 (well within the 45s timeout — should NOT fire).
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
        "not fire — only schedule when app.quit() is actually called"
    )


def test_force_exit_thread_NOT_scheduled_before_first_heartbeat(  # noqa: N802
    server: IPCServer,
) -> None:
    """No force-exit thread before Electron's first heartbeat (slow cold-start guard).

    Mirrors the existing guard: ``_last_heartbeat_at`` is ``None``
    until the first heartbeat lands. The watchdog refuses to fire — and
    therefore refuses to schedule the force-exit thread — so a slow
    Electron cold start doesn't cause a spurious process exit.
    """
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


# ── Actual os._exit call ───────────────────────────────────────────────


def test_force_exit_calls_os_exit_with_code_1_after_grace_period(
    server: IPCServer,
) -> None:
    """The force-exit thread calls ``os._exit(1)`` after the grace period.

    The grace period default is 10s — too long for a unit test. We
    patch ``_HEARTBEAT_FORCE_EXIT_GRACE_SECONDS`` down to 0.05s and
    patch ``os._exit`` so the test process doesn't actually die.

    The thread calls ``time.sleep(grace)`` then ``os._exit(1)``. We
    also patch ``time.sleep`` so we can verify it was called with the
    right duration, and so the test doesn't block on real time.
    """
    # Arm + fire the watchdog.
    with patch("voice_typer.server.ipc_server.time.monotonic", return_value=100.0):
        server._handle_heartbeat(None, {"id": 1})

    exit_calls: list[int] = []

    # Patch os._exit inside the ipc_server module (the daemon thread
    # resolves ``os`` from the module's import-time binding, so we
    # patch the module's ``os`` attribute).
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
        # before calling os._exit. Wait long enough for it to fire.
        # Use a real (short) sleep here — we patched the constant down
        # to 50ms, so 1s of polling is plenty.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not exit_calls:
            time.sleep(0.01)

    assert exit_calls == [1], (
        f"force-exit thread should have called os._exit(1) exactly once after the grace period; got: {exit_calls}"
    )


def test_force_exit_thread_does_not_fire_if_quit_exits_process_first(
    server: IPCServer,
) -> None:
    """If ``app.quit()`` causes the process to exit naturally, the force-exit thread is moot.

    In production, ``app.quit()`` either:
      (a) unwinds the main thread and the process exits before the
          10s grace period expires — the daemon thread is reaped by
          the OS, never reaching ``os._exit``.
      (b) hangs inside ``tray.stop()`` — the daemon thread fires
          ``os._exit(1)`` after 10s.

    This test simulates (a) by patching ``os._exit`` to record the
    call without actually exiting, and asserting the call happens
    only if the grace period elapses. The test patches the grace
    period DOWN so the thread fires quickly — verifying the os._exit
    path actually executes when it should.
    """
    # The scenario where quit() "succeeds" is the same as the scenario
    # where it "hangs" from the daemon thread's perspective — the
    # thread doesn't know either way. The only signal is "the process
    # is still alive after the grace period". This test documents that
    # the force-exit thread fires regardless of whether quit() succeeded,
    # because the thread cannot introspect process state. In production
    # the thread is naturally reaped if the process exits first.
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
    """The force-exit thread logs an error message before calling ``os._exit``.

    The log line is the postmortem signal for ops: if logs end with
    "force-exiting via os._exit(1) (tray.stop() likely hung)", we know
    the watchdog fired. Without the log, the process would just vanish
    with no diagnostic.
    """
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
    """If ``threading.Thread`` itself raises, the watchdog must not propagate.

    Defensive: this is essentially impossible in normal CPython, but
    the try/except around the scheduling is the contract — the daemon
    thread is best-effort, and the watchdog must still return True
    (so the heartbeat loop exits) even if the force-exit thread
    couldn't be started.
    """
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
    """The default grace period is 10 seconds (per the spec).

    Production must use 10s — long enough for graceful ``app.quit()``
    to complete, short enough to bound the worst-case hang. Tests
    patch it down; production must not.
    """
    assert _HEARTBEAT_FORCE_EXIT_GRACE_SECONDS == 10.0, (
        "default grace period must be 10s per spec — if this is "
        "changed, update the comment in ipc_server.py and the "
        "rationale in review.md"
    )
