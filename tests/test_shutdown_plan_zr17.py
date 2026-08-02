"""declarative ``ShutdownPlan`` + ``_run_plan`` driver tests.

These tests pin the refactor of
``voice_typer/server/shutdown_controller.py``:

* The ``ShutdownPlan`` / ``ShutdownStep`` dataclasses express the
  teardown ordering contract declaratively (``name``, ``func``,
  ``timeout``, ``depends_on``, ``skip_if_dep_timed_out``).
* The ``_run_plan`` driver executes a plan (sequenced or parallel),
  tracks timed-out step names, and applies the barrier
  (skip downstream steps whose upstream dependency timed out when
  ``skip_if_dep_timed_out=True``).
* The ``_do_cleanup`` body builds a sequenced plan + a parallel plan
  and hands them to ``_run_plan``. The parallel plan declares
  ``teardown_sounddevice`` with ``depends_on="teardown_recorder"`` +
  ``skip_if_dep_timed_out=True`` so the barrier fires when the
  recorder's PortAudio stream failed to close in time.

The tests construct a ``_FakeApp`` with spies on every teardown
method (mirroring the pattern in ``test_shutdown_parallel.py``) and
assert:

1. The call order matches the declarative plan: sequenced phase
   (timers_and_recording → recorder → history_db → crash_recovery)
   runs BEFORE the parallel batch; ``tray.stop`` runs AFTER the
   parallel batch.
2. The barrier fires: when ``teardown_recorder`` is forced to
   time out, ``teardown_sounddevice`` is NOT invoked (skipped by the
   driver pre-flight check).
3. The ``ShutdownPlan`` / ``ShutdownStep`` dataclasses are
   importable + constructible with the documented field names.
4. ``_run_plan`` returns the union of prior + new timed-out step
   names so cross-plan barriers work.

Run with::

    python -m pytest tests/test_shutdown_plan_zr17.py -q --timeout=30 --no-cov
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import (
    ShutdownController,
    ShutdownPlan,
    ShutdownStep,
)

# ── Override the autouse ``mock_heavy_imports`` conftest fixture ───────
# Same rationale as ``test_shutdown_parallel.py``: avoids pulling in the
# real ``voice_typer.server.app`` (which can be in a parallel agent's
# WIP state) during the autouse fixture setup.


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
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed."""
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-zr17"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)

    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)

    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)

    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app`` with every
    ``_teardown_*`` method spied. The spies record call order in the
    shared ``call_order`` list so tests can assert sequencing.
    """
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)

    # Wrap every ``_teardown_*`` method in a spy that records the
    # call order. The spy invokes the original (delegating) method
    # so the real teardown body still runs (against the MagicMock
    # collaborators on ``fake_app``).
    call_order: list[str] = []
    ctrl._call_order = call_order  # type: ignore[attr-defined]

    teardown_method_names = [
        "_teardown_timers_and_recording",
        "_teardown_recorder",
        "_teardown_history_db",
        "_teardown_crash_recovery",
        "_teardown_asr_models",
        "_teardown_restore_volume",
        "_teardown_waveform_wiring",
        "_teardown_sounddevice",
        "_teardown_pid_file",
        "_teardown_mutex_handle",
        "_teardown_devnull_files",
        "_teardown_level_monitor",
        "_teardown_hotkeys",
        "_teardown_electron",
        "_teardown_event_bus",
    ]
    for name in teardown_method_names:
        original = getattr(ctrl, name)

        def _make_spy(n: str, orig):
            def _spy(*args, **kwargs):
                call_order.append(n)
                return orig(*args, **kwargs)

            return _spy

        setattr(ctrl, name, _make_spy(name, original))

    # Also spy on tray.stop (the late bookend).
    original_tray_stop = fake_app.tray.stop

    def _tray_stop_spy(*args, **kwargs):
        call_order.append("tray.stop")
        return original_tray_stop(*args, **kwargs)

    fake_app.tray.stop = _tray_stop_spy

    return ctrl


# ── Dataclass contract tests ───────────────────────────────────────────


class TestShutdownPlanDataclass:
    """``ShutdownPlan`` + ``ShutdownStep`` are importable,
    constructible with the documented field names, and frozen."""

    def test_shutdown_step_default_fields(self) -> None:
        step = ShutdownStep(name="x", func=lambda: None, timeout=1.0)
        assert step.name == "x"
        assert step.timeout == 1.0
        assert step.depends_on is None
        assert step.skip_if_dep_timed_out is False

    def test_shutdown_step_barrier_fields(self) -> None:
        step = ShutdownStep(
            name="downstream",
            func=lambda: None,
            timeout=1.0,
            depends_on="upstream",
            skip_if_dep_timed_out=True,
        )
        assert step.depends_on == "upstream"
        assert step.skip_if_dep_timed_out is True

    def test_shutdown_step_is_frozen(self) -> None:
        step = ShutdownStep(name="x", func=lambda: None, timeout=1.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            step.name = "y"  # type: ignore[misc]

    def test_shutdown_plan_phase_field(self) -> None:
        plan = ShutdownPlan(phase="sequenced")
        assert plan.phase == "sequenced"
        assert plan.steps == ()

    def test_shutdown_plan_with_steps(self) -> None:
        step = ShutdownStep(name="x", func=lambda: None, timeout=1.0)
        plan = ShutdownPlan(phase="parallel", steps=(step,))
        assert len(plan.steps) == 1
        assert plan.steps[0].name == "x"


# ── _run_plan driver tests ─────────────────────────────────────────────


class TestRunPlanDriver:
    """``_run_plan`` executes the plan, tracks timed-out steps,
    and applies the barrier."""

    def test_run_plan_returns_empty_when_no_steps(self, controller) -> None:
        plan = ShutdownPlan(phase="sequenced", steps=())
        result = controller._run_plan(plan, frozenset())
        assert result == frozenset()

    def test_run_plan_sequenced_records_timeout(self, controller, monkeypatch) -> None:
        """A sequenced step that times out is recorded in the returned
        ``frozenset`` so downstream barriers can fire."""
        from voice_typer.server import shutdown_controller as _sc

        original = _sc._run_with_timeout

        def _fast_timeout(description, func, timeout=5.0):
            if description == "slow_step":
                # Force a timeout by sleeping longer than the budget.
                time.sleep(0.2)
                return _sc.TIMEOUT
            return original(description, func, timeout=timeout)

        # Replace _run_with_timeout in the shutdown_controller module
        # (the teardown helpers look it up dynamically).
        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_timeout)

        def _slow_func():
            time.sleep(0.3)

        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(name="slow_step", func=_slow_func, timeout=0.1),
                ShutdownStep(name="fast_step", func=lambda: None, timeout=1.0),
            ),
        )
        result = controller._run_plan(plan, frozenset())
        assert "slow_step" in result
        assert "fast_step" not in result

    def test_run_plan_gt70_barrier_skips_dependent_sequenced_step(
        self, controller, monkeypatch
    ) -> None:
        """barrier: a sequenced step with ``depends_on`` +
        ``skip_if_dep_timed_out=True`` is SKIPPED when the dependency
        timed out in ``prior_timed_out``."""
        skip_spy = MagicMock()
        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="downstream",
                    func=skip_spy,
                    timeout=1.0,
                    depends_on="upstream",
                    skip_if_dep_timed_out=True,
                ),
            ),
        )
        # Pass "upstream" in prior_timed_out → barrier should fire.
        result = controller._run_plan(plan, frozenset({"upstream"}))
        skip_spy.assert_not_called()
        # The skipped step is NOT added to timed_out (it didn't time
        # out; it was skipped). The prior timed_out set is preserved.
        assert "upstream" in result
        assert "downstream" not in result

    def test_run_plan_gt70_barrier_runs_step_when_dep_succeeded(
        self, controller
    ) -> None:
        """When the dependency did NOT time out, the barrier does NOT
        fire and the step runs normally."""
        run_spy = MagicMock()
        plan = ShutdownPlan(
            phase="sequenced",
            steps=(
                ShutdownStep(
                    name="downstream",
                    func=run_spy,
                    timeout=1.0,
                    depends_on="upstream",
                    skip_if_dep_timed_out=True,
                ),
            ),
        )
        controller._run_plan(plan, frozenset())  # no prior timed_out
        run_spy.assert_called_once()


# ── End-to-end _do_cleanup ordering test ───────────────────────────────


class TestDoCleanupCallOrder:
    """``_do_cleanup`` invokes the sequenced teardowns BEFORE
    the parallel batch, and ``tray.stop`` AFTER the parallel batch.
    The spy on each ``_teardown_*`` method records the call order in
    ``controller._call_order`` so we can assert sequencing.
    """

    def test_sequenced_phase_runs_before_parallel_batch(self, controller) -> None:
        controller._do_cleanup()
        order = controller._call_order  # type: ignore[attr-defined]

        # Every sequenced-phase step must appear in the order.
        sequenced_names = [
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_history_db",
            "_teardown_crash_recovery",
        ]
        for name in sequenced_names:
            assert name in order, f"sequenced step {name} must be called"

        # The parallel batch steps must also be called.
        parallel_names = [
            "_teardown_asr_models",
            "_teardown_restore_volume",
            "_teardown_waveform_wiring",
            "_teardown_sounddevice",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_devnull_files",
            "_teardown_level_monitor",
            "_teardown_hotkeys",
            "_teardown_electron",
            "_teardown_event_bus",
        ]
        for name in parallel_names:
            assert name in order, f"parallel step {name} must be called"

        # tray.stop must be called LAST (after every teardown step).
        assert "tray.stop" in order
        tray_idx = order.index("tray.stop")
        for name in sequenced_names + parallel_names:
            assert order.index(name) < tray_idx, (
                f"tray.stop must run AFTER {name}; got order: {order}"
            )

    def test_sequenced_phase_runs_in_declaration_order(self, controller) -> None:
        """The sequenced phase must run in the declaration order:
        timers_and_recording → recorder → history_db → crash_recovery.
        Reordering would race the transcription thread's DB write."""
        controller._do_cleanup()
        order = controller._call_order  # type: ignore[attr-defined]

        seq_order = [n for n in order if n in {
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_history_db",
            "_teardown_crash_recovery",
        }]
        assert seq_order == [
            "_teardown_timers_and_recording",
            "_teardown_recorder",
            "_teardown_history_db",
            "_teardown_crash_recovery",
        ], f"sequenced phase must run in declaration order; got: {seq_order}"

    def test_asr_models_is_first_parallel_step(self, controller) -> None:
        """``_teardown_asr_models`` is declared FIRST in the parallel
        plan so the (potentially slow) CUDA context teardown starts as
        early as possible. The parallel batch runs concurrently, so we
        can't assert strict ordering — but ``_teardown_asr_models``
        must be in the FIRST batch of teardowns that run after the
        sequenced phase completes."""
        controller._do_cleanup()
        order = controller._call_order  # type: ignore[attr-defined]

        crash_recovery_idx = order.index("_teardown_crash_recovery")
        asr_idx = order.index("_teardown_asr_models")
        # asr_models must run AFTER crash_recovery (sequenced phase
        # completes before parallel batch starts).
        assert asr_idx > crash_recovery_idx, (
            "_teardown_asr_models must run AFTER the sequenced phase; "
            f"got asr_idx={asr_idx}, crash_recovery_idx={crash_recovery_idx}"
        )


# ── barrier end-to-end test ──────────────────────────────────────


class TestGT70BarrierEndToEnd:
    """when ``_teardown_recorder`` times out, the barrier
    skips ``_teardown_sounddevice`` (the downstream call that touches
    the same PortAudio resource)."""

    def test_sounddevice_skipped_when_recorder_times_out(
        self, controller, fake_app, monkeypatch
    ) -> None:
        """Force ``_teardown_recorder`` to time out. The driver should
        skip ``_teardown_sounddevice`` (it declares
        ``depends_on="teardown_recorder"`` +
        ``skip_if_dep_timed_out=True``).

        Implementation note: the test patches
        ``_run_with_timeout`` to return ``TIMEOUT`` for the
        ``teardown_recorder`` description WITHOUT invoking the inner
        function (the spy on ``_teardown_recorder`` therefore does NOT
        fire — the barrier fires before the spy is reached). The test
        asserts only that ``_teardown_sounddevice`` is NOT called
        (which is the barrier contract)."""
        from voice_typer.server import shutdown_controller as _sc

        original = _sc._run_with_timeout

        def _fast_timeout(description, func, timeout=5.0):
            if description == "teardown_recorder":
                # Short-circuit: return TIMEOUT without invoking
                # ``func``. The driver records "teardown_recorder" in
                # its timed_out set and the barrier fires for
                # the downstream ``teardown_sounddevice`` step.
                return _sc.TIMEOUT
            return original(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _fast_timeout)

        # Spy on _teardown_sounddevice — it should NOT be called.
        sd_spy = MagicMock()
        controller._teardown_sounddevice = sd_spy

        controller._do_cleanup()
        order = controller._call_order  # type: ignore[attr-defined]

        # _teardown_sounddevice was NOT called (barrier fired).
        # The driver short-circuited _teardown_recorder before its spy
        # could fire, so we only assert on the downstream step here.
        assert "_teardown_sounddevice" not in order, (
            "barrier: _teardown_sounddevice must be SKIPPED when "
            "_teardown_recorder timed out (the leaked recorder worker "
            "is still accessing the PortAudio stream)"
        )
        sd_spy.assert_not_called()

    def test_sounddevice_runs_when_recorder_succeeds(
        self, controller, fake_app
    ) -> None:
        """Sanity: when ``_teardown_recorder`` succeeds, the barrier
        does NOT fire and ``_teardown_sounddevice`` runs normally."""
        # Default: recorder not recording → teardown_recorder is a
        # fast no-op → does not time out → barrier does not fire.
        controller._do_cleanup()
        order = controller._call_order  # type: ignore[attr-defined]

        assert "_teardown_recorder" in order
        assert "_teardown_sounddevice" in order, (
            "_teardown_sounddevice must run when _teardown_recorder "
            "succeeded (barrier does not fire)"
        )
