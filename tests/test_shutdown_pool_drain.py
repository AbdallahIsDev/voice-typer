"""YJ-20 regression: WS/TCP dispatch pool ``shutdown(wait=True)`` must be"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController

from tests.fixtures.wait_helpers import wait_until


class TestWsDispatchPoolDrain:
    """YJ-20: the WS pool drain pattern must bound the in-flight handler"""

    def test_pool_drain_completes_within_6s_after_sleepy_task(self):
        """Construct a ``ThreadPoolExecutor``, submit a 2s-sleeping task"""
        pool = ThreadPoolExecutor(max_workers=1)

        def sleepy_handler() -> str:
            time.sleep(2.0)
            return "done"

        # Submit the handler, it starts running immediately (max_workers=1).

        future = pool.submit(sleepy_handler)

        # Wait until the worker has actually STARTED the task, otherwise
        assert wait_until(future.running, timeout=2.0), "handler never started running"

        start = time.monotonic()

        # Mirror the production pattern from shutdown_controller._do_cleanup.
        pool.shutdown(wait=False, cancel_futures=True)
        join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
        join_thread.start()
        join_thread.join(timeout=5.0)

        elapsed = time.monotonic() - start

        # The 2s handler should drain well within the 5s deadline.
        assert not join_thread.is_alive(), (
            f"YJ-20: ws_dispatch_pool drain did not complete in 5s (elapsed={elapsed:.2f}s), join_thread still alive"
        )
        assert elapsed < 6.0, f"YJ-20: pool drain took {elapsed:.2f}s, must be < 6s (5s deadline + 1s slack)"

        # The handler's result is still delivered (the in-flight task
        assert future.result(timeout=0.1) == "done"

    def test_pool_drain_logs_warning_when_handler_exceeds_deadline(self, caplog):
        """production code logs a warning. This test mirrors the warning-"""
        import logging

        pool = ThreadPoolExecutor(max_workers=1)

        def very_sleepy_handler() -> str:
            time.sleep(10.0)
            return "done"

        sleepy_future = pool.submit(very_sleepy_handler)
        # Wait until the worker has actually started the task.
        assert wait_until(sleepy_future.running, timeout=2.0), "handler never started running"

        start = time.monotonic()

        pool.shutdown(wait=False, cancel_futures=True)
        join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
        join_thread.start()
        join_thread.join(timeout=1.0)  # Use a SHORT deadline so the test is fast.

        elapsed = time.monotonic() - start

        # The join thread is still alive, the 10s handler hasn't finished.
        assert join_thread.is_alive(), (
            f"YJ-20: join_thread should still be alive after 1s deadline (elapsed={elapsed:.2f}s), handler sleeps 10s"
        )

        # Mirror the production warning emission.
        with caplog.at_level(logging.WARNING):
            if join_thread.is_alive():
                logging.getLogger("voice_typer.server.shutdown_controller").warning(
                    "[SHUTDOWN] ws_dispatch_pool did not drain in 5s, proceeding anyway"
                )

        assert any("ws_dispatch_pool did not drain" in r.message for r in caplog.records), (
            "YJ-20: warning must be emitted when the drain deadline is exceeded"
        )

        # NOTE: we deliberately leak the very_sleepy_handler's worker

    def test_pool_drain_no_in_flight_tasks_completes_immediately(self):
        """When there are no in-flight tasks, the drain completes"""
        pool = ThreadPoolExecutor(max_workers=1)
        # No tasks submitted, pool is idle.

        start = time.monotonic()

        pool.shutdown(wait=False, cancel_futures=True)
        join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
        join_thread.start()
        join_thread.join(timeout=5.0)

        elapsed = time.monotonic() - start

        assert not join_thread.is_alive()
        assert elapsed < 1.0, f"YJ-20: idle pool drain should complete in < 1s; took {elapsed:.2f}s"

    def test_pool_drain_cancels_queued_futures(self):
        """``cancel_futures=True`` cancels QUEUED (not-yet-started) tasks."""
        from concurrent.futures import CancelledError

        pool = ThreadPoolExecutor(max_workers=1)

        def blocker() -> str:
            time.sleep(2.0)
            return "first"

        # First task occupies the single worker.
        first = pool.submit(blocker)
        # Second task is QUEUED.
        second = pool.submit(lambda: "second")
        assert wait_until(first.running, timeout=2.0), "first task never started running"

        pool.shutdown(wait=False, cancel_futures=True)

        # The QUEUED future is cancelled.
        with pytest.raises(CancelledError):
            second.result(timeout=0.1)

        # The RUNNING first task is NOT cancelled, it completes.
        assert first.result(timeout=3.0) == "first"


# (Issue 1): production-path test ─────────────────────


class _FakeAppForDoCleanup:
    """production-path YJ-20 test."""

    def __init__(self) -> None:
        # Shutdown state (mirrors VoiceTyperApp.__init__)
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._host_pid: int | None = None
        self._mutex_handle = None

        # Subsystem collaborators (MagicMock so any attribute/method call
        self.recorder = MagicMock()
        self.recorder.recording = True
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

        self._ipc_server = None

        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()

        self._do_cleanup = MagicMock()


@pytest.fixture
def _stub_shutdown_environment(tmp_config_dir, monkeypatch):
    """Stub the module-level helpers ``_do_cleanup`` touches so it"""
    # Some of these helpers (``_close_devnull_files`` /
    monkeypatch.setattr("voice_typer.server.backend_pid._clear_backend_pid_file", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None, raising=False)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None, raising=False)
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False, raising=False)


class TestDoCleanupDrainsWsPoolViaProductionPath:
    """YJ-FIX-C1-rework (Issue 1): exercise the PRODUCTION"""

    def test_do_cleanup_drains_ws_pool_via_production_path(self, caplog, _stub_shutdown_environment):
        """YJ-20 production-path regression."""
        # (a) Real ThreadPoolExecutor with an in-flight 6s-sleeping task.
        ws_pool = ThreadPoolExecutor(max_workers=1)

        def sleepy_handler() -> None:
            time.sleep(6.0)

        sleepy_future = ws_pool.submit(sleepy_handler)
        # Wait until the worker actually starts the task (otherwise it'd be
        assert wait_until(sleepy_future.running, timeout=2.0), "handler never started running"

        # Build a fake_app with the real WS pool, all other subsystems mocked.
        fake_app = _FakeAppForDoCleanup()
        fake_app._ipc_server = MagicMock()
        fake_app._ipc_server._ws_dispatch_pool = ws_pool

        controller = ShutdownController(fake_app)

        # (c)(3) Track WHEN subsequent teardown steps fire. Each tracker
        call_times: dict[str, float] = {}

        def make_tracker(name: str):
            def tracker(*args, **kwargs):
                call_times[name] = time.monotonic()

            return tracker

        # Replace the MagicMock methods with trackers. Returning None is
        fake_app.recorder.stop = make_tracker("recorder.stop")
        fake_app.history_db.flush = make_tracker("history_db.flush")
        fake_app._crash_recovery.flush = make_tracker("crash_recovery.flush")

        do_cleanup_start = time.monotonic()

        # (c)(2) Capture WARNING+ logs from the shutdown_controller logger.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.shutdown_controller"):
            controller._do_cleanup()

        do_cleanup_elapsed = time.monotonic() - do_cleanup_start

        assert do_cleanup_elapsed < 7.0, (
            f"YJ-20: _do_cleanup took {do_cleanup_elapsed:.2f}s, must be "
            f"≤ 7s (5s drain deadline + 2s slack for mocked subsystem "
            f"teardown). Note: this assertion alone does NOT catch the "
            f"mutation (a mutated _do_cleanup is even faster); the "
            f"WARNING and ordering assertions below catch it."
        )

        # (c)(2) Production WARNING log IS emitted via caplog (NOT
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "voice_typer.server.shutdown_controller"
            and "ws_dispatch_pool did not drain" in r.message
        ]
        assert len(warning_records) == 1, (
            f"YJ-20: expected exactly one WARNING log record from "
            f"voice_typer.server.shutdown_controller for the pool drain "
            f"timeout; got {len(warning_records)}. This assertion catches "
            f"mutations that remove the daemon-thread+join block "
            f"(shutdown_controller.py lines 402-410), without that block, "
            f"no WARNING is emitted. "
            f"All WARNING records seen: "
            f"{[(r.name, r.message) for r in caplog.records if r.levelno == logging.WARNING]}"
        )

        # (c)(3) Subsequent teardown steps still run AFTER pool drain.
        pool_drain_end_approx = do_cleanup_start + 5.0
        for step in ("recorder.stop", "crash_recovery.flush", "history_db.flush"):
            assert step in call_times, (
                f"YJ-20: subsequent teardown step {step} was NOT called, "
                f"_do_cleanup must continue past the pool drain and invoke "
                f"every downstream subsystem cleanup."
            )
            elapsed_from_start = call_times[step] - do_cleanup_start
            assert call_times[step] >= pool_drain_end_approx - 0.5, (
                f"YJ-20: {step} fired at {elapsed_from_start:.2f}s after "
                f"_do_cleanup start, must be AFTER the pool drain "
                f"(~5s after start). This assertion catches mutations "
                f"that remove the daemon-thread+join block, without "
                f"it, {step} fires at ~0s (before the 5s drain deadline)."
            )

        # Defense-in-depth: assert the three trackers were each called
        for step in ("recorder.stop", "crash_recovery.flush", "history_db.flush"):
            assert step in call_times, f"YJ-20: {step} must be called exactly once by _do_cleanup"

        # NOTE: the sleepy_handler's worker thread is still alive after
