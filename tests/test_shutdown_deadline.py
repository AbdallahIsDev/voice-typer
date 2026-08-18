"""regression tests for the shutdown deadline,
parallel history_db + crash_recovery teardown, and inner-outer timeout
slack in the history DB teardown.

These tests pin three findings fixed in ``_do_cleanup``:

* **(High)** — ``_do_cleanup`` now has an overall 20s deadline
  (``deadline = time.monotonic() + 20.0``). When the remaining budget
  drops below 5s, non-critical teardowns (hotkeys, level_monitor,
  waveform, electron, event_bus, asr_models, restore_volume,
  sounddevice, devnull_files, timers_and_recording) are SKIPPED and
  only critical flushes (history_db, crash_recovery, recorder.stop,
  mutex, PID file) plus the late ``tray.stop`` bookend run. Skipped
  teardowns are logged at WARNING.

* **(Medium)** — ``_teardown_history_db`` and
  ``_teardown_crash_recovery`` run SEQUENTIALLY in the
  ``sequenced_items`` phase, after ``_teardown_recorder`` completes
  (the recorder's transcription thread must be joined before the DB
  flush, and the crash-recovery snapshot drains after that). They are
  NOT in the parallel batch — the source-text contract in
  ``tests/test_shutdown_fast_path.py::TestSequentialHistoryAndCrashRecovery``
  pins this ordering.

* **(Medium)** — ``teardowns/history_db.py`` inner timeouts
  (``flush=8.0`` + ``close=4.0`` = 12s) are now strictly less than
  the outer wrapper budget (15s), leaving 3s of slack. Previously the
  inner timeouts (10s + 5s = 15s) exactly equaled the outer budget —
  zero slack meant a slow-but-not-stuck flush could blow the outer
  deadline and abandon the close call entirely.

The tests stub every external dependency (the real ``VoiceTyperApp``,
filesystem PID/devnull paths, Win32 kernel32, the ``event_bus`` module)
so they run headless on Linux without touching real subsystems. They
do NOT import ``voice_typer.server.app`` — instead they construct a
``_FakeApp`` duck-typed stand-in that satisfies the surface
``ShutdownController._do_cleanup`` touches.
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# Direct import — does NOT pull in voice_typer.server.app, so the
# clipboard_target_safety circular-import breakage in a parallel
# agent's WIP doesn't block these tests.
from voice_typer.server.shutdown_controller import ShutdownController

# ── Override the autouse ``mock_heavy_imports`` conftest fixture ───────
#
# Mirrors the override in ``tests/test_shutdown_parallel.py``: the
# shared autouse fixture tries to ``setattr`` on
# ``voice_typer.server.app``, which triggers an import of the real
# app module. These tests use a ``_FakeApp`` and don't need that
# import — overriding the autouse fixture with a no-op avoids the
# broken import.


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture."""
    yield


# ── Fake app ───────────────────────────────────────────────────────────


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Mirrors the collaborator surface that ``ShutdownController._do_cleanup``
    touches. Every subsystem is a ``MagicMock`` so we can assert call
    counts without running real teardown code. Identical to the
    ``_FakeApp`` in ``tests/test_shutdown_parallel.py`` — duplicated
    here so this test file is self-contained.
    """

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._electron_pid: int | None = None
        self._mutex_handle = None

        self.recorder = MagicMock()
        self.recorder.recording = False  # skip recorder.stop() branch by default
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
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed.

    Pre-installs ``voice_typer.server.app`` / ``event_bus`` /
    ``level_monitor`` as MagicMocks in ``sys.modules`` so the
    ``from voice_typer.server import ...`` imports inside the teardown
    helpers succeed without pulling in the real modules.
    """
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-uu7"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)

    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)

    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)

    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


# ── overall 20s deadline ─────────────────────────────────────────


class TestOverallDeadline:
    """``_do_cleanup`` enforces a 20s overall deadline. When the
    remaining budget drops below 5s, non-critical teardowns are SKIPPED
    and only critical flushes (history_db, crash_recovery,
    recorder.stop, mutex, PID file) + the late ``tray.stop`` bookend
    run."""

    def test_deadline_skips_non_critical_parallel_teardowns(self, controller, fake_app, monkeypatch):
        """When the 20s deadline is near (< 5s remaining) at the start
        of the parallel batch, NON-CRITICAL helpers
        (asr_models/restore_volume/waveform/sounddevice/devnull/level_monitor/
        hotkeys/electron/event_bus) are SKIPPED, while CRITICAL helpers
        (pid_file, mutex_handle) still run.

        We mock ``time.monotonic`` so the FIRST call (which computes
        ``_uu7_deadline = time.monotonic() + 20.0``) returns the real
        wall-clock T, and every subsequent call returns ``T + 25.0``
        (so ``_uu7_remaining()`` returns ``-5.0`` — deadline is near).
        ``_run_with_timeout`` uses ``threading.Thread.join(timeout=)``
        which is NOT affected by ``time.monotonic`` mocking, so the
        per-helper timeouts still work normally.
        """
        # Track which teardowns were called (by their canonical
        # description, WITHOUT the leading underscore — matches the
        # ``desc`` strings used in ``_do_cleanup``'s parallel_items /
        # sequenced_pre_items tuples).
        called: list[str] = []

        def _make_spy(desc):
            def _spy():
                called.append(desc)

            return _spy

        # Patch every teardown method on the controller with a spy that
        # records its description. ``_do_cleanup`` calls
        # ``self._teardown_X`` (the bound method attribute), so
        # patching the attribute on the instance is sufficient.
        all_teardowns = [
            ("_teardown_timers_and_recording", "teardown_timers_and_recording"),
            ("_teardown_recorder", "teardown_recorder"),
            ("_teardown_history_db", "teardown_history_db"),
            ("_teardown_crash_recovery", "teardown_crash_recovery"),
            ("_teardown_asr_models", "teardown_asr_models"),
            ("_teardown_restore_volume", "teardown_restore_volume"),
            ("_teardown_waveform_wiring", "teardown_waveform_wiring"),
            ("_teardown_sounddevice", "teardown_sounddevice"),
            ("_teardown_pid_file", "teardown_pid_file"),
            ("_teardown_mutex_handle", "teardown_mutex_handle"),
            ("_teardown_devnull_files", "teardown_devnull_files"),
            ("_teardown_level_monitor", "teardown_level_monitor"),
            ("_teardown_hotkeys", "teardown_hotkeys"),
            ("_teardown_electron", "teardown_electron"),
            ("_teardown_event_bus", "teardown_event_bus"),
        ]
        for attr_name, desc in all_teardowns:
            setattr(controller, attr_name, MagicMock(side_effect=_make_spy(desc)))

        # Mock time.monotonic so the deadline is "near" after the
        # initial deadline computation. The first call returns real T
        # (so _uu7_deadline = T + 20.0); subsequent calls return
        # T + 25.0 (so _uu7_remaining() = -5.0, deadline_near() = True).
        import voice_typer.server.shutdown_controller as _sc

        _real_monotonic = time.monotonic
        _first_call = [True]

        def _fake_monotonic():
            if _first_call[0]:
                _first_call[0] = False
                return _real_monotonic()
            return _real_monotonic() + 25.0

        # Patch ``time.monotonic`` on the ``time`` module that
        # ``shutdown_controller`` imports. ``shutdown_controller`` does
        # ``import time`` at module level, so ``_sc.time`` is the
        # stdlib ``time`` module — patching its ``monotonic`` attribute
        # affects every caller (acceptable for this focused test).
        monkeypatch.setattr(_sc.time, "monotonic", _fake_monotonic)

        controller._do_cleanup()

        # Critical teardowns MUST have been called.
        critical = {
            "teardown_recorder",
            "teardown_history_db",
            "teardown_crash_recovery",
            "teardown_pid_file",
            "teardown_mutex_handle",
        }
        for name in critical:
            assert name in called, (
                f"critical teardown {name!r} MUST run even when the "
                f"deadline is near — it contains a data-loss-critical flush. "
                f"Called: {called}"
            )

        # Non-critical teardowns MUST have been skipped (NOT called).
        non_critical = {
            "teardown_timers_and_recording",
            "teardown_asr_models",
            "teardown_restore_volume",
            "teardown_waveform_wiring",
            "teardown_sounddevice",
            "teardown_devnull_files",
            "teardown_level_monitor",
            "teardown_hotkeys",
            "teardown_electron",
            "teardown_event_bus",
        }
        for name in non_critical:
            assert name not in called, (
                f"non-critical teardown {name!r} MUST be skipped when "
                f"the 20s deadline is near (< 5s remaining). Called: {called}"
            )

        # The late ``tray.stop`` bookend must still run (it unblocks the
        # main thread parked in pystray's run() loop).
        fake_app.tray.stop.assert_called_once_with()

    def test_deadline_not_near_runs_all_teardowns(self, controller, fake_app):
        """When the 20s deadline is NOT near (>= 5s remaining), every
        teardown helper runs as before — no skips. This is the normal
        shutdown path; only kicks in when the deadline is tight."""
        called: list[str] = []

        def _make_spy(desc):
            def _spy():
                called.append(desc)

            return _spy

        all_teardowns = [
            ("_teardown_timers_and_recording", "teardown_timers_and_recording"),
            ("_teardown_recorder", "teardown_recorder"),
            ("_teardown_history_db", "teardown_history_db"),
            ("_teardown_crash_recovery", "teardown_crash_recovery"),
            ("_teardown_asr_models", "teardown_asr_models"),
            ("_teardown_restore_volume", "teardown_restore_volume"),
            ("_teardown_waveform_wiring", "teardown_waveform_wiring"),
            ("_teardown_sounddevice", "teardown_sounddevice"),
            ("_teardown_pid_file", "teardown_pid_file"),
            ("_teardown_mutex_handle", "teardown_mutex_handle"),
            ("_teardown_devnull_files", "teardown_devnull_files"),
            ("_teardown_level_monitor", "teardown_level_monitor"),
            ("_teardown_hotkeys", "teardown_hotkeys"),
            ("_teardown_electron", "teardown_electron"),
            ("_teardown_event_bus", "teardown_event_bus"),
        ]
        for attr_name, desc in all_teardowns:
            setattr(controller, attr_name, MagicMock(side_effect=_make_spy(desc)))

        # No time.monotonic mocking — real wall clock, well under 20s.
        controller._do_cleanup()

        # Every teardown must have been called exactly once.
        expected_called = {desc for (_attr, desc) in all_teardowns}
        for desc in expected_called:
            assert desc in called, (
                f"when the deadline is NOT near, teardown {desc!r} must run (no skip). Called: {called}"
            )

        # tray.stop bookend must have run.
        fake_app.tray.stop.assert_called_once_with()

    def test_deadline_logs_skipped_teardowns(self, controller, fake_app, monkeypatch, caplog):
        """skipped teardowns are logged at WARNING so operators
        can see the degraded-shutdown event. A single summary line
        lists every skipped teardown."""
        # Make every teardown a no-op spy.
        for name in [
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
        ]:
            setattr(controller, name, MagicMock())

        # Mock time.monotonic so deadline is near after first call.
        import voice_typer.server.shutdown_controller as _sc

        _real_monotonic = time.monotonic
        _first_call = [True]

        def _fake_monotonic():
            if _first_call[0]:
                _first_call[0] = False
                return _real_monotonic()
            return _real_monotonic() + 25.0

        monkeypatch.setattr(_sc.time, "monotonic", _fake_monotonic)

        with caplog.at_level("WARNING", logger="voice_typer.server.shutdown_controller"):
            controller._do_cleanup()

        # The summary WARNING must mention and list skipped
        # teardowns. We don't assert the exact list (order / formatting
        # may drift); we assert the summary line exists and mentions
        # at least one non-critical teardown name.
        summary_lines = [
            r.message for r in caplog.records if "skipped" in r.message.lower() and "teardown" in r.message.lower()
        ]
        assert summary_lines, (
            "expected at least one WARNING log line mentioning "
            "'skipped' and 'teardown' when teardowns were skipped due to "
            f"the 20s deadline. Records: {[r.message for r in caplog.records]}"
        )
        # At least one non-critical teardown name must appear in the
        # combined log output.
        combined = " ".join(summary_lines)
        assert any(
            name in combined
            for name in (
                "teardown_hotkeys",
                "teardown_electron",
                "teardown_event_bus",
                "teardown_asr_models",
            )
        ), f"the skipped-teardown summary must list at least one non-critical teardown name. Summary: {combined}"


# ── sequential history_db + crash_recovery ──────────────────────


class TestSequentialHistoryAndCrashRecovery:
    """``_teardown_history_db`` and ``_teardown_crash_recovery``
    run SEQUENTIALLY in the ``sequenced_items`` phase after
    ``_teardown_recorder`` completes. They are NOT in the parallel
    batch: the recorder's transcription thread must be joined before
    the DB flush, and the crash-recovery snapshot drains after that
    (see the sequenced-phase rationale in ``shutdown_controller.py``
    and the source-text contract in
    ``tests/test_shutdown_fast_path.py``)."""

    def test_history_db_and_crash_recovery_run_sequentially(self, controller, fake_app):
        """Both teardowns must run SEQUENTIALLY (non-overlapping in
        wall-clock time). We instrument each to record its start and
        end timestamps with a small sleep; if they ran concurrently,
        the later start would be BEFORE the earlier end.

        Threshold: each sleeps 0.3s. Sequential total: ~0.6s. Concurrent
        total: ~0.3s. We assert the later start is AFTER the earlier
        end (proving non-overlap) and that the total is closer to 0.6s
        than 0.3s.
        """
        hist_db_times: dict[str, float] = {}
        crash_recovery_times: dict[str, float] = {}

        def _slow_history_db():
            hist_db_times["start"] = time.monotonic()
            time.sleep(0.3)
            hist_db_times["end"] = time.monotonic()

        def _slow_crash_recovery():
            crash_recovery_times["start"] = time.monotonic()
            time.sleep(0.3)
            crash_recovery_times["end"] = time.monotonic()

        # Patch the teardown methods on the controller instance.
        # ``_do_cleanup`` calls ``self._teardown_history_db`` etc., so
        # patching the bound attribute is sufficient.
        controller._teardown_history_db = MagicMock(side_effect=_slow_history_db)
        controller._teardown_crash_recovery = MagicMock(side_effect=_slow_crash_recovery)

        # Make the other teardowns fast no-ops so they don't add noise.
        for name in [
            "_teardown_timers_and_recording",
            "_teardown_recorder",
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
        ]:
            setattr(controller, name, MagicMock())

        start = time.monotonic()
        controller._do_cleanup()
        elapsed = time.monotonic() - start

        # Both must have run.
        assert "start" in hist_db_times, "teardown_history_db must have run"
        assert "start" in crash_recovery_times, "teardown_crash_recovery must have run"

        # Sequential check: the LATER start must be AFTER the EARLIER
        # end (no overlap). The sequenced phase joins each worker
        # (``_run_with_timeout``) before starting the next step.
        starts = [hist_db_times["start"], crash_recovery_times["start"]]
        ends = [hist_db_times["end"], crash_recovery_times["end"]]
        later_start = max(starts)
        earlier_end = min(ends)
        assert later_start >= earlier_end, (
            f"teardown_history_db and teardown_crash_recovery MUST "
            f"run sequentially (non-overlapping in time). "
            f"history_db: [{hist_db_times['start']:.4f}, {hist_db_times['end']:.4f}], "
            f"crash_recovery: [{crash_recovery_times['start']:.4f}, {crash_recovery_times['end']:.4f}]. "
            f"Later start ({later_start:.4f}) must be >= earlier end ({earlier_end:.4f})."
        )

        # Total wall-clock must be closer to 0.6s (sequential) than 0.3s
        # (concurrent). Allow generous slack for the other teardowns +
        # bookends + thread pool scheduling.
        assert elapsed >= 0.55, (
            f"sequential hist_db + crash_recovery took {elapsed:.3f}s "
            f"total — expected >= 0.55s (sequential ~0.6s + slack). "
            f"Concurrent would be ~0.3s."
        )

    def test_recorder_runs_before_history_db(self, controller, fake_app):
        """the ``recorder → history_db → crash_recovery`` ordering is
        preserved by the sequenced phase: ``_teardown_recorder`` must
        complete BEFORE ``_teardown_history_db`` starts (so the
        transcription thread's final ``add_transcription()`` is
        enqueued before the DB flush).

        The ordering is enforced by the ``sequenced_items`` list: each
        step runs to completion (``_run_with_timeout`` joins the worker)
        before the next starts. The ``_recorder_teardown_done`` Event
        (set by ``_teardown_recorder`` before returning) provides the
        happens-before guarantee for downstream consumers.
        """
        order: list[str] = []

        def _spy_recorder():
            order.append("recorder_start")
            time.sleep(0.05)
            order.append("recorder_end")

        def _spy_history_db():
            order.append("history_db_start")

        def _spy_crash_recovery():
            order.append("crash_recovery_start")

        controller._teardown_recorder = MagicMock(side_effect=_spy_recorder)
        controller._teardown_history_db = MagicMock(side_effect=_spy_history_db)
        controller._teardown_crash_recovery = MagicMock(side_effect=_spy_crash_recovery)
        for name in [
            "_teardown_timers_and_recording",
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
        ]:
            setattr(controller, name, MagicMock())

        controller._do_cleanup()

        # recorder_end must come BEFORE history_db_start.
        assert "recorder_end" in order, f"recorder_end missing from order: {order}"
        assert "history_db_start" in order, f"history_db_start missing from order: {order}"
        rec_end_idx = order.index("recorder_end")
        hist_start_idx = order.index("history_db_start")
        assert rec_end_idx < hist_start_idx, (
            f"recorder must complete BEFORE history_db starts. "
            f"Order: {order}. recorder_end at {rec_end_idx}, "
            f"history_db_start at {hist_start_idx}."
        )


# ── inner-outer timeout slack ───────────────────────────────────


class TestInnerOuterTimeoutSlack:
    """``teardowns/history_db.py`` inner timeouts
    (``flush=8.0`` + ``close=4.0`` = 12s) are strictly less than the
    outer wrapper budget (15s), leaving 3s of slack."""

    def test_inner_timeouts_sum_to_less_than_outer_budget(self):
        """The inner ``_run_with_timeout`` timeouts for flush + close
        must sum to strictly less than 15.0s (the outer wrapper budget
        that ``_do_cleanup`` allocates for ``teardown_history_db``).

        Previously: 10.0 + 5.0 = 15.0 (zero slack — a slow flush could
        blow the outer deadline and abandon the close call entirely).
        After the refactor: 8.0 + 4.0 = 12.0 (3s slack).

        We read the source of ``teardown_history_db`` and assert the
        timeouts are present. This is a static source-level check — it
        pins the contract without depending on runtime behaviour.
        """
        import inspect

        from voice_typer.server.shutdown.teardowns import history_db as hist_module

        source = inspect.getsource(hist_module.teardown_history_db)
        # The flush timeout must be 8.0 (was 10.0 previously).
        assert "timeout=8.0" in source, f"history_db.flush must use timeout=8.0 (was 10.0 pre-fix). Source:\n{source}"
        # The close timeout must be 4.0 (was 5.0 previously).
        assert "timeout=4.0" in source, f"history_db.close must use timeout=4.0 (was 5.0 pre-fix). Source:\n{source}"
        # The OLD timeouts must NOT be present (regression guard).
        assert "timeout=10.0" not in source, f"history_db.flush must NOT use timeout=10.0 anymore. Source:\n{source}"
        assert "timeout=5.0" not in source, f"history_db.close must NOT use timeout=5.0 anymore. Source:\n{source}"

    def test_inner_timeouts_provide_slack_under_outer_budget(self, controller, fake_app):
        """Functional check: even if ``history_db.flush`` takes the full
        inner timeout (8s), ``history_db.close`` still has time to run
        within the outer 15s budget. We simulate by making flush + close
        each take ~0.1s (fast — we're verifying the structure, not the
        timing) and asserting both are called.

        The real guarantee is the source-level check above; this test
        just confirms the teardown runs both calls without skipping
        close due to a deadline violation."""
        flush_called = []
        close_called = []

        def _flush():
            flush_called.append(time.monotonic())

        def _close():
            close_called.append(time.monotonic())

        fake_app.history_db.flush = _flush
        fake_app.history_db.close = _close

        # Run the actual teardown_history_db (not a mock) so we
        # exercise the real _run_with_timeout wrappers.
        from voice_typer.server.shutdown.teardowns.history_db import teardown_history_db

        teardown_history_db(controller)

        assert len(flush_called) == 1, f"flush must be called once; got {flush_called}"
        assert len(close_called) == 1, f"close must be called once; got {close_called}"
        # close must run AFTER flush (sequenced within the teardown).
        assert close_called[0] >= flush_called[0], (
            f"close must run AFTER flush within teardown_history_db. "
            f"flush at {flush_called[0]:.4f}, close at {close_called[0]:.4f}."
        )
