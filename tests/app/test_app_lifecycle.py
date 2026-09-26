"""
``voice_typer/server/app_lifecycle.py``, the ``LifecycleController``
``LausuApp``-level integration; they do NOT pin the controller's
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from voice_typer.server.app_lifecycle import LifecycleController


class _StubApp:
    """Minimal duck-typed stub satisfying ``LifecycleController``'s"""

    def __init__(self) -> None:
        # shutdown signaling
        self._shutting_down: bool = False
        self._shutting_down_event: threading.Event = threading.Event()
        # IPC server (None = no IPC attached; PERF-005 short-circuit path)
        self._ipc_server: Any = None
        self.config = MagicMock()
        self.config.save = MagicMock(return_value=True)
        self._thread_registry = MagicMock()
        # ShutdownController._do_cleanup; tests spy on this attribute.
        self._do_cleanup = MagicMock()
        self.quit = MagicMock(side_effect=lambda: (_ for _ in ()).throw(SystemExit(0)))
        self.shutdown = MagicMock()
        self._shutdown_watchdog_timeout_s: float = 0.05
        self.recorder = MagicMock()
        self.recorder.recording = False


@pytest.fixture
def stub_app() -> _StubApp:
    return _StubApp()


@pytest.fixture
def lifecycle(stub_app: _StubApp) -> LifecycleController:
    return LifecycleController(stub_app)


def _stub_restart_side_effects(stub_app: _StubApp, monkeypatch) -> None:
    """test sandbox (event_bus.publish, sys.exit)."""
    monkeypatch.setattr(
        "voice_typer.server.event_bus.publish",
        lambda msg: None,
    )
    # Patch global sys.exit so it raises SystemExit (which restart_app
    monkeypatch.setattr(sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))


class TestWaitForRelaunchAckNoServer:
    """PERF-005: when no IPC server is attached (``app._ipc_server is"""

    def test_returns_false_immediately_when_no_ipc_server(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        stub_app._ipc_server = None

        sleep_calls: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

        start = time.monotonic()
        result = lifecycle._wait_for_relaunch_ack(timeout=5.0)
        elapsed = time.monotonic() - start

        assert result is False, (
            "PERF-005: _wait_for_relaunch_ack must return False when no "
            "IPC server is attached (no one is listening for the ack)."
        )
        # Must short-circuit in <0.5s even though the timeout was 5.0s —
        assert elapsed < 0.5, (
            f"PERF-005: _wait_for_relaunch_ack must short-circuit (0ms) "
            f"when no IPC server is attached; took {elapsed:.3f}s."
        )
        assert sleep_calls == [], (
            f"PERF-005: _wait_for_relaunch_ack must NOT call time.sleep "
            f"when no IPC server is attached; got sleep_calls={sleep_calls}"
        )


class TestWaitForRelaunchAckTimeout:
    """
    helper MUST wait at most ``timeout`` seconds and then return
    This pins the bounded-wait contract: a dead host (Tauri
    """

    def test_times_out_after_n_seconds_when_ack_never_arrives(
        self, lifecycle: LifecycleController, stub_app: _StubApp
    ) -> None:
        # Attach a fake IPC server exposing a real (never-set) ack event.
        never_set_event = threading.Event()

        class _FakeServer:
            def wait_for_relaunch_ack(self, timeout: float) -> bool:
                return never_set_event.wait(timeout=timeout)

        stub_app._ipc_server = _FakeServer()

        timeout = 0.2
        start = time.monotonic()
        result = lifecycle._wait_for_relaunch_ack(timeout=timeout)
        elapsed = time.monotonic() - start

        assert result is False, "Expected False when the ack event is never signalled."
        # Must wait at least ~timeout seconds (not short-circuit to 0)
        assert elapsed >= timeout - 0.05, (
            f"_wait_for_relaunch_ack returned too quickly: elapsed={elapsed:.3f}s, "
            f"expected to wait at least {timeout}s for the ack."
        )
        assert elapsed < timeout + 1.0, (
            f"_wait_for_relaunch_ack blocked too long: elapsed={elapsed:.3f}s, expected to time out near {timeout}s."
        )

    def test_times_out_via_legacy_ack_event_attr(self, lifecycle: LifecycleController, stub_app: _StubApp) -> None:
        """When the IPC server lacks the public"""
        never_set_event = threading.Event()

        class _LegacyServer:
            # No public wait_for_relaunch_ack method.
            _relaunch_ack_event = never_set_event

        stub_app._ipc_server = _LegacyServer()

        timeout = 0.15
        start = time.monotonic()
        result = lifecycle._wait_for_relaunch_ack(timeout=timeout)
        elapsed = time.monotonic() - start

        assert result is False
        # Tolerant lower bound: ``Event.wait`` may return a few ms early
        assert elapsed >= timeout - 0.05, f"Legacy fallback path returned too quickly: {elapsed:.3f}s"


class TestRestartAppNonMainThread:
    """When ``restart_app`` runs on a NON-main thread (the common case"""

    def test_restart_app_on_non_main_thread_skips_sys_exit(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        _stub_restart_side_effects(stub_app, monkeypatch)

        sys_exit_calls: list[int] = []
        monkeypatch.setattr(sys, "exit", lambda code=0: sys_exit_calls.append(code))

        # Run restart_app on a worker thread (NOT the main thread).
        errors: list[BaseException] = []

        def _run() -> None:
            try:
                lifecycle.restart_app()
            except BaseException as exc:  # noqa: BLE001, capture for assertion
                errors.append(exc)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=5.0)

        assert not errors, f"restart_app on non-main thread must not raise; got: {errors!r}"
        assert sys_exit_calls == [], (
            f"restart_app on non-main thread must NOT call sys.exit (it "
            f"would raise SystemExit in the worker thread only, leaving "
            f"the process lingering). Got sys_exit_calls={sys_exit_calls}"
        )

    def test_restart_app_on_non_main_thread_arms_watchdog(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        _stub_restart_side_effects(stub_app, monkeypatch)
        monkeypatch.setattr(sys, "exit", lambda code=0: None)

        watchdog_calls: list[float] = []
        stub_app.shutdown._arm_shutdown_watchdog = lambda timeout_s: watchdog_calls.append(timeout_s)

        def _run() -> None:
            lifecycle.restart_app()

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=5.0)

        assert watchdog_calls == [stub_app._shutdown_watchdog_timeout_s], (
            "restart_app on non-main thread must arm the shutdown watchdog "
            "with app._shutdown_watchdog_timeout_s so a hung pystray loop "
            f"is broken. Got watchdog_calls={watchdog_calls}"
        )


class TestQuitAppPushesEventBeforeDelegate:
    """F-06: ``quit_app`` MUST push the ``quit_app`` event over the IPC"""

    def test_quit_app_pushes_event_before_quit_delegate(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        pushed: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )

        # Track the order: push happens before quit().
        call_order: list[str] = []
        stub_app.quit = lambda: call_order.append("quit")

        lifecycle.quit_app()

        # The quit_app event MUST be pushed.
        assert any(msg.get("type") == "quit_app" for msg in pushed), (
            f"quit_app must push a quit_app event; got pushes: {pushed!r}"
        )
        # The push MUST happen before the quit delegate.
        assert call_order == ["quit"], (
            f"quit_app must call app.quit() AFTER pushing the quit_app event (F-06). call_order={call_order}"
        )

    def test_quit_app_double_call_still_pushes_event(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        """F-06: a double-quit (user clicks Quit twice, or SIGTERM"""
        pushed: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )

        # The real LausuApp.quit() sets _shutting_down_event (via
        quit_calls: list[bool] = []

        def _fake_quit() -> None:
            quit_calls.append(True)
            stub_app._shutting_down_event.set()

        stub_app.quit = _fake_quit

        # First call delegates to quit (sets the event).
        lifecycle.quit_app()
        # Second call: _shutting_down_event is now set; the push must
        lifecycle.quit_app()

        quit_pushes = [m for m in pushed if m.get("type") == "quit_app"]
        assert len(quit_pushes) == 2, (
            f"Double-quit must push quit_app twice (once per call) so a "
            f"lost first push is recovered by the second. Got pushes: {quit_pushes!r}"
        )
        # The delegate must be called exactly ONCE (guarded on second call).
        assert len(quit_calls) == 1, f"Double-quit must guard the delegate (call once); got quit_calls={quit_calls}"


class TestRestartAppCleanupRaises:
    """When ``app._do_cleanup()`` raises mid-restart, the restart"""

    def test_relaunch_event_pushed_before_cleanup_raises(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        _stub_restart_side_effects(stub_app, monkeypatch)

        pushed: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg),
        )
        # _do_cleanup raises mid-restart.
        stub_app._do_cleanup = MagicMock(side_effect=RuntimeError("portaudio teardown blew up"))

        # The exception propagates, the caller (tray _wrap) is
        with pytest.raises(RuntimeError, match="portaudio teardown blew up"):
            lifecycle.restart_app()

        # PRE-cleanup side effects MUST have completed before the raise.
        relaunch_pushes = [m for m in pushed if m.get("type") == "relaunch_app"]
        assert relaunch_pushes, (
            "restart_app must push the relaunch_app event BEFORE calling "
            "_do_cleanup, the user's Restart click must still trigger "
            "host relaunch even when cleanup blows up."
        )
        assert stub_app._shutting_down is True, (
            "_shutting_down must be set before _do_cleanup so the dispatch "
            "gate rejects new IPC requests during the unwind."
        )
        assert stub_app._shutting_down_event.is_set(), (
            "_shutting_down_event must be set before _do_cleanup for cross-thread memory ordering."
        )

    def test_thread_registry_shutdown_failure_does_not_abort_restart(
        self, lifecycle: LifecycleController, stub_app: _StubApp, monkeypatch
    ) -> None:
        """try/except (unlike ``_do_cleanup``), a failure there must"""
        _stub_restart_side_effects(stub_app, monkeypatch)
        monkeypatch.setattr(sys, "exit", lambda code=0: (_ for _ in ()).throw(SystemExit(code)))

        stub_app._thread_registry.shutdown_all = MagicMock(side_effect=RuntimeError("thread join timed out"))
        do_cleanup_calls: list[bool] = []
        stub_app._do_cleanup = MagicMock(side_effect=lambda: do_cleanup_calls.append(True))

        with contextlib.suppress(SystemExit):
            lifecycle.restart_app()

        assert do_cleanup_calls == [True], (
            "restart_app must proceed to _do_cleanup even when "
            "thread_registry.shutdown_all() raises (it's wrapped in "
            "try/except, a failure there is logged, not propagated)."
        )
