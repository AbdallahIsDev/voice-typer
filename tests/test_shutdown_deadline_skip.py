"""Regression tests for the inter-step deadline check in ``run_plan``."""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown.plan import CRITICAL_STEPS, run_plan
from voice_typer.server.shutdown_controller import (
    ShutdownController,
    ShutdownPlan,
    ShutdownStep,
)


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture."""
    yield


class _FakeApp:
    """Minimal duck-typed stand-in for ``LausuApp``."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
        self._mutex_handle = None
        self.recorder = MagicMock()
        self.recorder.recording = False
        self.recording = MagicMock()
        self.recording._transcription_thread = None
        self.hotkeys = MagicMock()
        self.hotkeys._hotkey_backend = MagicMock()
        self.hotkeys._esc_backend = MagicMock()
        self.hotkeys._repaste_backend = MagicMock()
        self.history_db = MagicMock()
        self._crash_recovery = MagicMock()
        self.tray = MagicMock()
        self._thread_registry = MagicMock()
        self.waveform_wiring = MagicMock()
        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()


@pytest.fixture
def fake_app(monkeypatch):
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/lausu-test-shutdown-skip"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)

    # The PID-file teardown resolves ``_clear_backend_pid_file`` through
    fake_backend_pid = MagicMock()
    fake_backend_pid._clear_backend_pid_file = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.backend_pid", fake_backend_pid)
    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)
    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    return ShutdownController(fake_app)


class TestInterStepDeadlineSkip:
    """``run_plan`` skips non-critical sequenced steps when the deadline"""

    def test_critical_steps_set_is_correct(self) -> None:
        """The ``CRITICAL_STEPS`` frozenset must contain exactly the"""
        assert frozenset({"teardown_recorder", "teardown_history_db", "teardown_crash_recovery"}) == CRITICAL_STEPS

    def test_non_critical_step_skipped_when_deadline_near(self, controller) -> None:
        """A non-critical sequenced step is SKIPPED when"""
        # Set a deadline that's already expired (0s remaining).
        controller._shutdown_deadline = time.monotonic() - 1.0
        controller._shutdown_skipped = []

        called: list[str] = []

        def _spy():
            called.append("non_critical_step")

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="non_critical_step",
                    func=_spy,
                    timeout=1.0,
                ),
            ),
        )
        result = run_plan(controller, plan, frozenset())

        # The step was NOT called (skipped).
        assert called == [], f"non-critical step must be skipped when deadline is near. Called: {called}"
        # The step name was appended to _shutdown_skipped.
        assert "non_critical_step" in controller._shutdown_skipped, (
            f"skipped step must be appended to _shutdown_skipped. Got: {controller._shutdown_skipped}"
        )
        # The step is NOT in the returned timed_out set (it was skipped,
        assert "non_critical_step" not in result

    def test_critical_step_runs_even_when_deadline_near(self, controller) -> None:
        """A critical sequenced step (``teardown_recorder`` etc.) RUNS"""
        controller._shutdown_deadline = time.monotonic() - 1.0
        controller._shutdown_skipped = []

        called: list[str] = []

        def _spy():
            called.append("teardown_recorder")

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="teardown_recorder",
                    func=_spy,
                    timeout=1.0,
                ),
            ),
        )
        run_plan(controller, plan, frozenset())

        assert called == ["teardown_recorder"], f"critical step must run even when deadline is near. Called: {called}"
        # Critical step is NOT in _shutdown_skipped.
        assert "teardown_recorder" not in controller._shutdown_skipped

    def test_mixed_plan_skips_non_critical_keeps_critical(self, controller) -> None:
        """A sequenced plan with both critical and non-critical steps:"""
        controller._shutdown_deadline = time.monotonic() - 0.5
        controller._shutdown_skipped = []

        called: list[str] = []

        def _make_spy(name):
            def _spy():
                called.append(name)

            return _spy

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="teardown_timers_and_recording",
                    func=_make_spy("teardown_timers_and_recording"),
                    timeout=1.0,
                ),
                ShutdownStep(
                    name="teardown_recorder",
                    func=_make_spy("teardown_recorder"),
                    timeout=1.0,
                ),
                ShutdownStep(
                    name="teardown_history_db",
                    func=_make_spy("teardown_history_db"),
                    timeout=1.0,
                ),
                ShutdownStep(
                    name="teardown_crash_recovery",
                    func=_make_spy("teardown_crash_recovery"),
                    timeout=1.0,
                ),
            ),
        )
        run_plan(controller, plan, frozenset())

        # Critical steps ran.
        assert "teardown_recorder" in called
        assert "teardown_history_db" in called
        assert "teardown_crash_recovery" in called
        # Non-critical step was skipped.
        assert "teardown_timers_and_recording" not in called, (
            f"non-critical step must be skipped when deadline is near. Called: {called}"
        )
        # Non-critical step in _shutdown_skipped.
        assert "teardown_timers_and_recording" in controller._shutdown_skipped

    def test_no_skip_when_deadline_not_set(self, controller) -> None:
        """When ``controller._shutdown_deadline`` is None (direct"""
        controller._shutdown_deadline = None
        controller._shutdown_skipped = None

        called: list[str] = []

        def _make_spy(name):
            def _spy():
                called.append(name)

            return _spy

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="non_critical_a",
                    func=_make_spy("non_critical_a"),
                    timeout=1.0,
                ),
                ShutdownStep(
                    name="teardown_recorder",
                    func=_make_spy("teardown_recorder"),
                    timeout=1.0,
                ),
            ),
        )
        run_plan(controller, plan, frozenset())

        # Both steps ran (no deadline → no skip).
        assert "non_critical_a" in called
        assert "teardown_recorder" in called

    def test_no_skip_when_deadline_far(self, controller) -> None:
        """When the deadline is far away (>= 5s remaining), non-critical"""
        controller._shutdown_deadline = time.monotonic() + 20.0
        controller._shutdown_skipped = []

        called: list[str] = []

        def _spy():
            called.append("non_critical_step")

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="non_critical_step",
                    func=_spy,
                    timeout=1.0,
                ),
            ),
        )
        run_plan(controller, plan, frozenset())

        assert called == ["non_critical_step"], f"non-critical step must run when deadline is far. Called: {called}"
        assert controller._shutdown_skipped == []

    def test_skip_logged_at_warning(self, controller, caplog) -> None:
        """The skip is logged at WARNING so operators can see the"""
        controller._shutdown_deadline = time.monotonic() - 1.0
        controller._shutdown_skipped = []

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="non_critical_step",
                    func=lambda: None,
                    timeout=1.0,
                ),
            ),
        )
        with caplog.at_level("WARNING", logger="voice_typer.server.shutdown.plan"):
            run_plan(controller, plan, frozenset())

        skip_logs = [r for r in caplog.records if "skipping non-critical step" in r.message]
        assert skip_logs, (
            "expected a WARNING log mentioning 'skipping non-critical step'. "
            f"Records: {[r.message for r in caplog.records]}"
        )

    def test_all_three_critical_steps_exempt(self, controller) -> None:
        """All three critical steps (``teardown_recorder``,"""
        controller._shutdown_deadline = time.monotonic() - 2.0
        controller._shutdown_skipped = []

        called: list[str] = []

        def _make_spy(name):
            def _spy():
                called.append(name)

            return _spy

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="teardown_recorder",
                    func=_make_spy("teardown_recorder"),
                    timeout=1.0,
                ),
                ShutdownStep(
                    name="teardown_history_db",
                    func=_make_spy("teardown_history_db"),
                    timeout=1.0,
                ),
                ShutdownStep(
                    name="teardown_crash_recovery",
                    func=_make_spy("teardown_crash_recovery"),
                    timeout=1.0,
                ),
            ),
        )
        run_plan(controller, plan, frozenset())

        assert set(called) == {"teardown_recorder", "teardown_history_db", "teardown_crash_recovery"}, (
            f"all three critical steps must run even when deadline is expired. Called: {called}"
        )
        assert controller._shutdown_skipped == [], (
            f"no critical step should be in _shutdown_skipped. Got: {controller._shutdown_skipped}"
        )
