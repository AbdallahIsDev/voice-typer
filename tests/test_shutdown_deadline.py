"""regression tests for the shutdown deadline,"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture."""
    yield


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``."""

    def __init__(self) -> None:
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
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
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed."""
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-uu7"
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
    """A ``ShutdownController`` wrapping ``fake_app``."""
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


class TestOverallDeadline:
    """remaining budget drops below 5s, non-critical teardowns are SKIPPED"""

    def test_deadline_skips_non_critical_parallel_teardowns(self, controller, fake_app, monkeypatch):
        """When the 20s deadline is near (< 5s remaining) at the start"""
        # Track which teardowns were called (by their canonical
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
            ("_teardown_host_child", "teardown_host_child"),
            ("_teardown_event_bus", "teardown_event_bus"),
        ]
        for attr_name, desc in all_teardowns:
            setattr(controller, attr_name, MagicMock(side_effect=_make_spy(desc)))

        import voice_typer.server.shutdown_controller as _sc

        _real_monotonic = time.monotonic
        _first_call = [True]

        def _fake_monotonic():
            if _first_call[0]:
                _first_call[0] = False
                return _real_monotonic()
            return _real_monotonic() + 25.0

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
                f"deadline is near, it contains a data-loss-critical flush. "
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
            "teardown_host_child",
            "teardown_event_bus",
        }
        for name in non_critical:
            assert name not in called, (
                f"non-critical teardown {name!r} MUST be skipped when "
                f"the 20s deadline is near (< 5s remaining). Called: {called}"
            )

        fake_app.tray.stop.assert_called_once_with()

    def test_deadline_not_near_runs_all_teardowns(self, controller, fake_app):
        """When the 20s deadline is NOT near (>= 5s remaining), every"""
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
            ("_teardown_host_child", "teardown_host_child"),
            ("_teardown_event_bus", "teardown_event_bus"),
        ]
        for attr_name, desc in all_teardowns:
            setattr(controller, attr_name, MagicMock(side_effect=_make_spy(desc)))

        # No time.monotonic mocking, real wall clock, well under 20s.
        controller._do_cleanup()

        # Every teardown must have been called exactly once.
        expected_called = {desc for (_attr, desc) in all_teardowns}
        for desc in expected_called:
            assert desc in called, (
                f"when the deadline is NOT near, teardown {desc!r} must run (no skip). Called: {called}"
            )

        fake_app.tray.stop.assert_called_once_with()

    def test_deadline_logs_skipped_teardowns(self, controller, fake_app, monkeypatch, caplog):
        """skipped teardowns are logged at WARNING so operators"""
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
            "_teardown_host_child",
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
        summary_lines = [
            r.message for r in caplog.records if "skipped" in r.message.lower() and "teardown" in r.message.lower()
        ]
        assert summary_lines, (
            "expected at least one WARNING log line mentioning "
            "'skipped' and 'teardown' when teardowns were skipped due to "
            f"the 20s deadline. Records: {[r.message for r in caplog.records]}"
        )
        combined = " ".join(summary_lines)
        assert any(
            name in combined
            for name in (
                "teardown_hotkeys",
                "teardown_host_child",
                "teardown_event_bus",
                "teardown_asr_models",
            )
        ), f"the skipped-teardown summary must list at least one non-critical teardown name. Summary: {combined}"


class TestSequentialHistoryAndCrashRecovery:
    """``_teardown_history_db`` and ``_teardown_crash_recovery``"""

    def test_history_db_and_crash_recovery_run_sequentially(self, controller, fake_app):
        """end timestamps with a small sleep; if they ran concurrently,"""
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
            "_teardown_host_child",
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
        assert elapsed >= 0.55, (
            f"sequential hist_db + crash_recovery took {elapsed:.3f}s "
            f"total, expected >= 0.55s (sequential ~0.6s + slack). "
            f"Concurrent would be ~0.3s."
        )

    def test_recorder_runs_before_history_db(self, controller, fake_app):
        """the ``recorder → history_db → crash_recovery`` ordering is"""
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
            "_teardown_host_child",
            "_teardown_event_bus",
        ]:
            setattr(controller, name, MagicMock())

        controller._do_cleanup()

        assert "recorder_end" in order, f"recorder_end missing from order: {order}"
        assert "history_db_start" in order, f"history_db_start missing from order: {order}"
        rec_end_idx = order.index("recorder_end")
        hist_start_idx = order.index("history_db_start")
        assert rec_end_idx < hist_start_idx, (
            f"recorder must complete BEFORE history_db starts. "
            f"Order: {order}. recorder_end at {rec_end_idx}, "
            f"history_db_start at {hist_start_idx}."
        )


class TestInnerOuterTimeoutSlack:
    """``teardowns/history_db.py`` inner timeouts"""

    def test_inner_timeouts_sum_to_less_than_outer_budget(self):
        """The inner ``_run_with_timeout`` timeouts for flush + close"""
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
        """Functional check: even if ``history_db.flush`` takes the full"""
        flush_called = []
        close_called = []

        def _flush():
            flush_called.append(time.monotonic())

        def _close():
            close_called.append(time.monotonic())

        fake_app.history_db.flush = _flush
        fake_app.history_db.close = _close

        # Run the actual teardown_history_db (not a mock) so we
        from voice_typer.server.shutdown.teardowns.history_db import teardown_history_db

        teardown_history_db(controller)

        assert len(flush_called) == 1, f"flush must be called once; got {flush_called}"
        assert len(close_called) == 1, f"close must be called once; got {close_called}"
        assert close_called[0] >= flush_called[0], (
            f"close must run AFTER flush within teardown_history_db. "
            f"flush at {flush_called[0]:.4f}, close at {close_called[0]:.4f}."
        )
