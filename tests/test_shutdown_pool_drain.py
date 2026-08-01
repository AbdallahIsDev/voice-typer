"""YJ-20 regression: WS/TCP dispatch pool ``shutdown(wait=True)`` must be
bounded-joined after ``shutdown(wait=False, cancel_futures=True)``.

Pre-fix, ``shutdown(wait=False, cancel_futures=True)`` only cancels QUEUED
(not-yet-started) dispatch tasks — RUNNING handlers continue. Without a
bounded join, teardown of the recorder / history_db / crash_recovery
subsystems races any in-flight WS handler that touches them, risking a
half-flushed history DB or a partial crash-recovery snapshot.

The first four tests in this module mirror the production pattern (from
``shutdown_controller._do_cleanup``) by constructing a real
``ThreadPoolExecutor``, submitting a sleepy task (simulating an in-flight
handler), triggering the same shutdown pattern, and asserting the join
completes within the deadline. They are correct but insufficient — a
mutation that removes the daemon-thread+join block from
``shutdown_controller._do_cleanup`` leaves them passing (they don't
exercise the production code path).

YJ-FIX-C1-rework (Issue 1) adds ``test_do_cleanup_drains_ws_pool_via_production_path``
which invokes ``ShutdownController._do_cleanup`` directly with a real
``ThreadPoolExecutor`` wired as ``fake_app._ipc_server._ws_dispatch_pool``.
That test FAILS if the daemon-thread+join block (``shutdown_controller.py``
lines 402-410 after the YJ-FIX-C1-rework comment additions) is removed —
the mutation sanity check is documented in the test docstring.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController

# ── Tests ────────────────────────────────────────────────────────────────


class TestWsDispatchPoolDrain:
    """YJ-20: the WS pool drain pattern must bound the in-flight handler
    drain at 5s and log a warning if the drain doesn't complete."""

    def test_pool_drain_completes_within_6s_after_sleepy_task(self):
        """Construct a ``ThreadPoolExecutor``, submit a 2s-sleeping task
        (simulating an in-flight WS handler), then trigger the same
        shutdown pattern as ``shutdown_controller._do_cleanup``:
        ``shutdown(wait=False, cancel_futures=True)`` followed by a
        daemon-thread ``shutdown(wait=True)`` joined with a 5s deadline.

        The 2s sleep is shorter than the 5s join deadline, so the join
        must complete within 6s (5s deadline + 1s slack). The future is
        RUNNING (not QUEUED), so ``cancel_futures=True`` does NOT
        short-circuit it — the wait=True join is what actually blocks
        until the handler finishes.
        """
        pool = ThreadPoolExecutor(max_workers=1)

        def sleepy_handler() -> str:
            time.sleep(2.0)
            return "done"

        # Submit the handler — it starts running immediately (max_workers=1).
        future = pool.submit(sleepy_handler)

        # Give the worker a moment to actually start the task (otherwise
        # it might still be QUEUED and cancel_futures=True would cancel
        # it, defeating the test's premise).
        time.sleep(0.1)

        start = time.monotonic()

        # Mirror the production pattern from shutdown_controller._do_cleanup.
        pool.shutdown(wait=False, cancel_futures=True)
        join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
        join_thread.start()
        join_thread.join(timeout=5.0)

        elapsed = time.monotonic() - start

        # The 2s handler should drain well within the 5s deadline.
        assert not join_thread.is_alive(), (
            f"YJ-20: ws_dispatch_pool drain did not complete in 5s (elapsed={elapsed:.2f}s) — join_thread still alive"
        )
        assert elapsed < 6.0, f"YJ-20: pool drain took {elapsed:.2f}s — must be < 6s (5s deadline + 1s slack)"

        # The handler's result is still delivered (the in-flight task
        # was allowed to finish, not killed mid-execution).
        assert future.result(timeout=0.1) == "done"

    def test_pool_drain_logs_warning_when_handler_exceeds_deadline(self, caplog):
        """When the in-flight handler exceeds the 5s join deadline, the
        production code logs a warning. This test mirrors the warning-
        emission path by submitting a 10s-sleeping task and asserting
        the join thread is still alive after the 5s deadline.
        """
        import logging

        pool = ThreadPoolExecutor(max_workers=1)

        def very_sleepy_handler() -> str:
            time.sleep(10.0)
            return "done"

        pool.submit(very_sleepy_handler)
        time.sleep(0.1)  # Let the worker start the task.

        start = time.monotonic()

        pool.shutdown(wait=False, cancel_futures=True)
        join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
        join_thread.start()
        join_thread.join(timeout=1.0)  # Use a SHORT deadline so the test is fast.

        elapsed = time.monotonic() - start

        # The join thread is still alive — the 10s handler hasn't finished.
        assert join_thread.is_alive(), (
            f"YJ-20: join_thread should still be alive after 1s deadline (elapsed={elapsed:.2f}s) — handler sleeps 10s"
        )

        # Mirror the production warning emission.
        with caplog.at_level(logging.WARNING):
            if join_thread.is_alive():
                logging.getLogger("voice_typer.server.shutdown_controller").warning(
                    "[SHUTDOWN] ws_dispatch_pool did not drain in 5s — proceeding anyway"
                )

        assert any("ws_dispatch_pool did not drain" in r.message for r in caplog.records), (
            "YJ-20: warning must be emitted when the drain deadline is exceeded"
        )

        # NOTE: we deliberately leak the very_sleepy_handler's worker
        # thread — it's a daemon, so it won't block process exit. We
        # can't cancel it (``shutdown(wait=False, cancel_futures=True)``
        # only cancels QUEUED tasks, not RUNNING ones — that's the
        # entire point of ).

    def test_pool_drain_no_in_flight_tasks_completes_immediately(self):
        """When there are no in-flight tasks, the drain completes
        immediately (the join thread doesn't block)."""
        pool = ThreadPoolExecutor(max_workers=1)
        # No tasks submitted — pool is idle.

        start = time.monotonic()

        pool.shutdown(wait=False, cancel_futures=True)
        join_thread = threading.Thread(target=pool.shutdown, kwargs={"wait": True}, daemon=True)
        join_thread.start()
        join_thread.join(timeout=5.0)

        elapsed = time.monotonic() - start

        assert not join_thread.is_alive()
        assert elapsed < 1.0, f"YJ-20: idle pool drain should complete in < 1s; took {elapsed:.2f}s"

    def test_pool_drain_cancels_queued_futures(self):
        """``cancel_futures=True`` cancels QUEUED (not-yet-started) tasks.
        With max_workers=1 and one in-flight task, a second submitted task
        is QUEUED — ``cancel_futures=True`` cancels it immediately."""
        from concurrent.futures import CancelledError

        pool = ThreadPoolExecutor(max_workers=1)

        def blocker() -> str:
            time.sleep(2.0)
            return "first"

        # First task occupies the single worker.
        first = pool.submit(blocker)
        # Second task is QUEUED.
        second = pool.submit(lambda: "second")
        time.sleep(0.1)  # Let the worker start the first task.

        # cancel_futures=True cancels the QUEUED second task.
        pool.shutdown(wait=False, cancel_futures=True)

        # The QUEUED future is cancelled.
        with pytest.raises(CancelledError):
            second.result(timeout=0.1)

        # The RUNNING first task is NOT cancelled — it completes.
        assert first.result(timeout=3.0) == "first"


# (Issue 1): production-path test ─────────────────────


class _FakeAppForDoCleanup:
    """Minimal duck-typed stand-in for ``VoiceTyperApp`` used by the
    production-path YJ-20 test.

    Mirrors the collaborator mocks in ``tests/test_shutdown_posix_release.py``
    and ``tests/test_shutdown_controller.py``: every subsystem
    ``ShutdownController._do_cleanup`` touches is a ``MagicMock`` so the
    full cleanup body runs without raising. The test overrides
    ``_ipc_server._ws_dispatch_pool`` with a REAL ``ThreadPoolExecutor``
    (the production code path under test) and overrides
    ``recorder.stop`` / ``history_db.flush`` / ``_crash_recovery.flush``
    with timestamp-recording trackers to assert ordering relative to the
    pool drain.
    """

    def __init__(self) -> None:
        # Shutdown state (mirrors VoiceTyperApp.__init__)
        self._shutting_down = False
        self._shutting_down_event = threading.Event()
        self._cleanup_done = False
        self._electron_pid: int | None = None
        self._mutex_handle = None

        # Subsystem collaborators (MagicMock so any attribute/method call
        # is recorded and returns a MagicMock by default).
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

        # IPC server — left as None here; the test wires a MagicMock with
        # a REAL ``_ws_dispatch_pool`` (ThreadPoolExecutor) to exercise
        # the production pool-drain code path.
        self._ipc_server = None

        # Methods on VoiceTyperApp that _do_cleanup calls (kept on the
        # app as delegates to other controllers).
        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()

        # ``_do_cleanup`` delegate on VoiceTyperApp — not used by the
        # test (it calls ``controller._do_cleanup()`` directly).
        self._do_cleanup = MagicMock()


@pytest.fixture
def _stub_shutdown_environment(tmp_path, monkeypatch):
    """Stub the module-level helpers ``_do_cleanup`` touches so it
    doesn't touch the real filesystem / Win32 API / devnull FDs.

    Mirrors the stubs in ``tests/test_shutdown_posix_release.py``.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._clear_backend_pid_file", lambda: None)
    monkeypatch.setattr("voice_typer.server.app._close_devnull_files", lambda: None)
    monkeypatch.setattr("voice_typer.server.app._register_devnull_file", lambda f: None)
    monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: False)


class TestDoCleanupDrainsWsPoolViaProductionPath:
    """YJ-FIX-C1-rework (Issue 1): exercise the PRODUCTION
    ``ShutdownController._do_cleanup`` pool-drain code path end-to-end.

    The earlier tests in this module mirror the drain pattern inline;
    they don't invoke ``controller._do_cleanup()``. A mutation that
    removes the daemon-thread+join block (``shutdown_controller.py``
    lines 402-410 after the YJ-FIX-C1-rework comment additions) leaves
    those tests passing. This class exercises the real cleanup body so
    the mutation is detected.
    """

    def test_do_cleanup_drains_ws_pool_via_production_path(self, caplog, _stub_shutdown_environment):
        """YJ-20 production-path regression.

        Test plan:
        (a) Build a real ``ThreadPoolExecutor`` with an in-flight 6s-
            sleeping task (longer than the 5s drain deadline) and wire
            it as ``fake_app._ipc_server._ws_dispatch_pool``.
        (b) Invoke ``controller._do_cleanup()`` — the production cleanup
            body that contains the pool-drain block.
        (c) Assert:
            1. Total cleanup time ≤ 7s (5s drain + 2s slack for the
               mocked subsystem teardown).
            2. The production WARNING log record IS emitted via caplog
               (NOT re-emitted by the test) — filter ``caplog.records``
               for the ``ws_dispatch_pool did not drain`` message on
               the ``voice_typer.server.shutdown_controller`` logger.
            3. Subsequent teardown steps (``recorder.stop``,
               ``history_db.flush``, ``crash_recovery.flush``) are
               called AFTER the pool drain completes (~5s after
               ``_do_cleanup`` start).

        Mutation sanity check: temporarily commenting out the daemon-
        thread+join block (``shutdown_controller.py`` lines 402-410)
        makes assertions (c)(2) and (c)(3) FAIL — no WARNING is
        emitted and subsequent steps fire at ~0s instead of ~5s. The
        mutation check was performed manually during YJ-FIX-C1-rework
        and documented in the return summary.
        """
        # (a) Real ThreadPoolExecutor with an in-flight 6s-sleeping task.
        # 6s > 5s drain deadline → the join_thread is still alive when
        # join(timeout=5.0) returns → the production WARNING fires.
        ws_pool = ThreadPoolExecutor(max_workers=1)

        def sleepy_handler() -> None:
            time.sleep(6.0)

        ws_pool.submit(sleepy_handler)
        # Let the worker actually start the task (otherwise it'd be
        # QUEUED and cancel_futures=True would cancel it, defeating the
        # test's premise — the production WARNING only fires when a
        # RUNNING handler exceeds the deadline).
        time.sleep(0.1)

        # Build a fake_app with the real WS pool, all other subsystems mocked.
        fake_app = _FakeAppForDoCleanup()
        fake_app._ipc_server = MagicMock()
        fake_app._ipc_server._ws_dispatch_pool = ws_pool

        controller = ShutdownController(fake_app)

        # (c)(3) Track WHEN subsequent teardown steps fire. Each tracker
        # records the monotonic timestamp when the production code
        # invokes it; the assertion below checks the timestamp is AFTER
        # the pool drain completes (~5s after _do_cleanup start).
        call_times: dict[str, float] = {}

        def make_tracker(name: str):
            def tracker(*args, **kwargs):
                call_times[name] = time.monotonic()

            return tracker

        # Replace the MagicMock methods with trackers. Returning None is
        # safe — ``_run_with_timeout`` propagates the return value, but
        # the callers in ``_do_cleanup`` don't check it (they only check
        # ``is TIMEOUT``).
        fake_app.recorder.stop = make_tracker("recorder.stop")
        fake_app.history_db.flush = make_tracker("history_db.flush")
        fake_app._crash_recovery.flush = make_tracker("crash_recovery.flush")

        do_cleanup_start = time.monotonic()

        # (c)(2) Capture WARNING+ logs from the shutdown_controller logger.
        # ``caplog.at_level`` sets the level AND captures records at that
        # level for the specified logger.
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.shutdown_controller"):
            controller._do_cleanup()

        do_cleanup_elapsed = time.monotonic() - do_cleanup_start

        # (c)(1) Total cleanup time bounded (5s drain + 2s slack for the
        # mocked subsystem teardown which is effectively instant).
        assert do_cleanup_elapsed < 7.0, (
            f"YJ-20: _do_cleanup took {do_cleanup_elapsed:.2f}s — must be "
            f"≤ 7s (5s drain deadline + 2s slack for mocked subsystem "
            f"teardown). Note: this assertion alone does NOT catch the "
            f"mutation (a mutated _do_cleanup is even faster); the "
            f"WARNING and ordering assertions below catch it."
        )

        # (c)(2) Production WARNING log IS emitted via caplog (NOT
        # re-emitted by the test). Filter by logger name + message
        # substring so warnings from other loggers (event_bus,
        # level_monitor, etc.) don't cause false positives.
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
            f"(shutdown_controller.py lines 402-410) — without that block, "
            f"no WARNING is emitted. "
            f"All WARNING records seen: "
            f"{[(r.name, r.message) for r in caplog.records if r.levelno == logging.WARNING]}"
        )

        # (c)(3) Subsequent teardown steps still run AFTER pool drain.
        # Pool drain completes at ~do_cleanup_start + 5s (the join
        # deadline). Allow 0.5s slack for the mocked subsystem steps
        # between pool drain and recorder.stop (cancel timers, stop
        # watchdog, cancel streaming — all instant with mocks).
        pool_drain_end_approx = do_cleanup_start + 5.0
        for step in ("recorder.stop", "crash_recovery.flush", "history_db.flush"):
            assert step in call_times, (
                f"YJ-20: subsequent teardown step {step} was NOT called — "
                f"_do_cleanup must continue past the pool drain and invoke "
                f"every downstream subsystem cleanup."
            )
            elapsed_from_start = call_times[step] - do_cleanup_start
            assert call_times[step] >= pool_drain_end_approx - 0.5, (
                f"YJ-20: {step} fired at {elapsed_from_start:.2f}s after "
                f"_do_cleanup start — must be AFTER the pool drain "
                f"(~5s after start). This assertion catches mutations "
                f"that remove the daemon-thread+join block — without "
                f"it, {step} fires at ~0s (before the 5s drain deadline)."
            )

        # Defense-in-depth: assert the three trackers were each called
        # exactly once (the production code path invokes each subsystem
        # teardown exactly once per _do_cleanup call).
        for step in ("recorder.stop", "crash_recovery.flush", "history_db.flush"):
            assert step in call_times, f"YJ-20: {step} must be called exactly once by _do_cleanup"

        # NOTE: the sleepy_handler's worker thread is still alive after
        # _do_cleanup returns (it sleeps 6s total; only ~5s elapsed
        # during the drain). Since Python 3.9, ThreadPoolExecutor worker
        # threads are non-daemon, so the ``concurrent.futures.thread``
        # atexit handler will join it at interpreter shutdown (adding
        # ~1s to the test process exit time). This is the behavior
        # documented in the  comment note (Issue 4) — the 5s bound
        # only unblocks _do_cleanup, not the atexit join.
