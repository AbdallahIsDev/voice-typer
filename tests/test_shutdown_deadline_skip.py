"""Regression tests for the inter-step deadline check in ``run_plan``.

These tests pin the fix: when the 20s shutdown deadline is nearly
exhausted (< 5s remaining) at the top of a sequenced-step iteration,
NON-CRITICAL steps are SKIPPED (logged at WARNING + appended to
``controller._shutdown_skipped``) so the remaining budget goes to the
flush-bearing critical steps.

Critical steps (``teardown_recorder`` / ``teardown_history_db`` /
``teardown_crash_recovery``) are NEVER skipped by the inter-step check
— they contain data-loss-critical flushes that must run regardless of
deadline pressure.

The tests construct a ``_FakeApp`` and a ``ShutdownController`` with
``_shutdown_deadline`` set to a near-expired timestamp, then call
``run_plan`` directly with a mix of critical + non-critical steps and
assert which were called vs skipped.
"""

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

# ── Override the autouse ``mock_heavy_imports`` conftest fixture ───────


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture."""
    yield


# ── Fake app ───────────────────────────────────────────────────────────


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._electron_pid: int | None = None
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
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-shutdown-skip"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)
    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)
    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)
    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    return ShutdownController(fake_app)


# ── Tests ──────────────────────────────────────────────────────────────


class TestGQ10InterStepDeadlineSkip:
    """``run_plan`` skips non-critical sequenced steps when the deadline
    is near, but ALWAYS runs critical flush-bearing steps."""

    def test_critical_steps_set_is_correct(self) -> None:
        """The ``CRITICAL_STEPS`` frozenset must contain exactly the
        three flush-bearing sequenced teardowns."""
        assert frozenset(
            {"teardown_recorder", "teardown_history_db", "teardown_crash_recovery"}
        ) == CRITICAL_STEPS

    def test_non_critical_step_skipped_when_deadline_near(self, controller) -> None:
        """A non-critical sequenced step is SKIPPED when
        ``controller._shutdown_deadline`` is set and the remaining
        budget is < 5s."""
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
        assert called == [], (
            f"non-critical step must be skipped when deadline is near. "
            f"Called: {called}"
        )
        # The step name was appended to _shutdown_skipped.
        assert "non_critical_step" in controller._shutdown_skipped, (
            f"skipped step must be appended to _shutdown_skipped. "
            f"Got: {controller._shutdown_skipped}"
        )
        # The step is NOT in the returned timed_out set (it was skipped,
        # not timed out).
        assert "non_critical_step" not in result

    def test_critical_step_runs_even_when_deadline_near(self, controller) -> None:
        """A critical sequenced step (``teardown_recorder`` etc.) RUNS
        even when the deadline is near — it contains a data-loss-
        critical flush."""
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

        assert called == ["teardown_recorder"], (
            f"critical step must run even when deadline is near. "
            f"Called: {called}"
        )
        # Critical step is NOT in _shutdown_skipped.
        assert "teardown_recorder" not in controller._shutdown_skipped

    def test_mixed_plan_skips_non_critical_keeps_critical(self, controller) -> None:
        """A sequenced plan with both critical and non-critical steps:
        when the deadline is near, non-critical steps are skipped and
        critical steps run."""
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
            f"non-critical step must be skipped when deadline is near. "
            f"Called: {called}"
        )
        # Non-critical step in _shutdown_skipped.
        assert "teardown_timers_and_recording" in controller._shutdown_skipped

    def test_no_skip_when_deadline_not_set(self, controller) -> None:
        """When ``controller._shutdown_deadline`` is None (direct
        ``run_plan`` invocation from tests, before ``_do_cleanup``
        publishes it), the inter-step check is skipped — all steps
        run regardless of their critical/non-critical status."""
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
        """When the deadline is far away (>= 5s remaining), non-critical
        steps run normally — no skip."""
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

        assert called == ["non_critical_step"], (
            f"non-critical step must run when deadline is far. Called: {called}"
        )
        assert controller._shutdown_skipped == []

    def test_skip_logged_at_warning(self, controller, caplog) -> None:
        """The skip is logged at WARNING so operators can see the
        degraded-shutdown event."""
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

        skip_logs = [
            r for r in caplog.records
            if "skipping non-critical step" in r.message
        ]
        assert skip_logs, (
            "expected a WARNING log mentioning 'skipping non-critical step'. "
            f"Records: {[r.message for r in caplog.records]}"
        )

    def test_all_three_critical_steps_exempt(self, controller) -> None:
        """All three critical steps (``teardown_recorder``,
        ``teardown_history_db``, ``teardown_crash_recovery``) are exempt
        from the inter-step deadline skip — they all run even when the
        deadline is expired."""
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
            f"all three critical steps must run even when deadline is expired. "
            f"Called: {called}"
        )
        assert controller._shutdown_skipped == [], (
            f"no critical step should be in _shutdown_skipped. "
            f"Got: {controller._shutdown_skipped}"
        )
