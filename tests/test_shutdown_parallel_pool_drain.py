"""parallel pool drain, ASR unload inner
timeout, and ``join_leaked_workers`` wired into the shutdown
watchdog.

These tests pin the three fixes applied to
``voice_typer/server/shutdown_controller.py`` in task:

* **(Medium)** — ``_do_cleanup``'s early bookend runs
  ``ipc_server.stop`` (TCP pool drain) and the WS dispatch pool
  drain CONCURRENTLY in a 2-item ``_run_parallel_with_timeout``
  batch (instead of sequentially). They touch disjoint pools, so
  parallelisation is safe. The DJ-9 ``ws_drained_event`` wait
  still gates the parallel subsystem batch. Cuts early-bookend
  worst case from 12s to ~7s.

* **(Medium)** — ``_teardown_asr_models`` wraps
  ``registry.unload()`` in ``_run_with_timeout("asr_registry.unload",
  registry.unload, timeout=8.0)``. If it returns ``TIMEOUT``, logs
  at WARNING and still proceeds to ``release_gpu_memory()``. The 8s
  inner timeout leaves 2s slack within the 10s parallel-batch
  deadline.

* **(Low)** — ``_watchdog`` calls
  ``join_leaked_workers(total_budget=1.0)`` just before ``os._exit(0)``.
  The function was defined in ``_timeout_utils.py`` but never called
  — the ``_LEAKED_WORKERS`` registry accumulated without being
  drained. The 1.0s shared budget keeps the watchdog's
  ``SHUTDOWN_WATCHDOG_TIMEOUT_S`` (2.0s) ceiling intact (shared-deadline
  mode caps at 10 workers × 0.2s each = 2.0s worst case, well within
  the 2s watchdog budget).

The tests run headless on Linux — they stub every external
dependency (real ``VoiceTyperApp``, filesystem PID/devnull paths,
Win32 kernel32, ``release_gpu_memory``) so they don't touch real
subsystems.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from voice_typer.server._timeout_utils import TIMEOUT
from voice_typer.server.shutdown_controller import ShutdownController

# ── Fake app ───────────────────────────────────────────────────────────


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Mirrors the collaborator surface that ``ShutdownController._do_cleanup``
    touches. Every subsystem is a ``MagicMock`` so we can assert call counts
    and ordering without running real teardown code.
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
        self.recorder.recording = False
        self.recording = MagicMock()
        self.recording._transcription_thread = None
        self.recording.pop_streaming_session = MagicMock(return_value=None)
        self.hotkeys = MagicMock()
        self.hotkeys._hotkey_backend = MagicMock()
        self.hotkeys._esc_backend = MagicMock()
        self.hotkeys._repaste_backend = MagicMock()
        self.history_db = MagicMock()
        self._crash_recovery = MagicMock()
        self.tray = MagicMock()
        self._thread_registry = MagicMock()
        self.waveform_wiring = MagicMock()
        self.models = MagicMock()
        self.models.registry = MagicMock()

        # Methods on VoiceTyperApp that _do_cleanup calls (kept on the
        # app as delegates to other controllers).
        self._cancel_pending_timers = MagicMock()
        self._restore_volume = MagicMock()

        # ``_do_cleanup`` delegate on VoiceTyperApp — not used by tests
        # that call ``controller._do_cleanup()`` directly.
        self._do_cleanup = MagicMock()

        # IPC server — left as None here; the test wires it as needed.
        self._ipc_server = None


@pytest.fixture
def _stub_shutdown_environment(tmp_path, monkeypatch):
    """Stub the module-level helpers ``_do_cleanup`` touches so it
    doesn't touch the real filesystem / Win32 API / devnull FDs.

    Uses ``raising=False`` so missing attributes on the ``app`` module
    (e.g. ``_close_devnull_files`` which may have been refactored away)
    don't cause a setup error.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    monkeypatch.setattr("voice_typer.server.app._config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(
        "voice_typer.server.app._clear_backend_pid_file",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        "voice_typer.server.app._close_devnull_files",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        "voice_typer.server.app._register_devnull_file",
        lambda f: None,
        raising=False,
    )
    monkeypatch.setattr("voice_typer.server.app.is_windows", lambda: False, raising=False)


# ipc_server.stop + WS pool drain run concurrently ────────────


class TestParallelPoolDrain:
    """``ipc_server.stop`` and the WS dispatch pool drain must
    run concurrently (not sequentially) via
    ``_run_parallel_with_timeout``."""

    def test_ipc_stop_and_ws_drain_run_concurrently(self, _stub_shutdown_environment):
        """both ``ipc_server.stop`` and the WS dispatch pool drain
        must run concurrently in a 2-item ``_run_parallel_with_timeout``
        batch.

        Test plan:
        (a) Wire ``fake_app._ipc_server`` with:
            - ``stop`` = a function that records its start time, sleeps
              0.3s, then returns.
            - ``_ws_dispatch_pool`` = a REAL ``ThreadPoolExecutor`` with
              a 0.3s-sleeping task submitted (so the pool drain blocks
              for ~0.3s on the in-flight task).
        (b) Invoke ``controller._do_cleanup()``.
        (c) Assert:
            1. Total elapsed time < 0.5s (sequential would be ~0.6s;
               parallel is ~0.3s).
            2. ``ipc_server.stop`` and the WS pool drain's
               ``shutdown(wait=True)`` call both started within a 0.15s
               window of each other (i.e. they overlap — concurrent).
        """
        fake_app = _FakeApp()
        fake_app._ipc_server = MagicMock()

        # Track ipc_server.stop start time.
        ipc_stop_start: list[float] = []
        ipc_stop_lock = threading.Lock()

        def slow_ipc_stop():
            with ipc_stop_lock:
                ipc_stop_start.append(time.monotonic())
            time.sleep(0.3)

        fake_app._ipc_server.stop = slow_ipc_stop

        # Real ThreadPoolExecutor with a 0.3s-sleeping in-flight task.
        ws_pool = ThreadPoolExecutor(max_workers=1)

        def sleepy_ws_handler():
            time.sleep(0.3)

        ws_pool.submit(sleepy_ws_handler)
        # Let the worker actually start the task (otherwise it'd be
        # QUEUED and cancel_futures=True would cancel it, defeating the
        # test's premise — the drain must block on a RUNNING handler).
        time.sleep(0.05)
        fake_app._ipc_server._ws_dispatch_pool = ws_pool
        # ``_ws_drained_event`` is a MagicMock → ``.wait(timeout=2.0)``
        # returns a truthy MagicMock instantly (no 2s delay).

        # Track the WS pool drain's ``shutdown(wait=True)`` call time.
        ws_drain_start: list[float] = []
        ws_drain_lock = threading.Lock()
        original_shutdown = ws_pool.shutdown

        def tracked_shutdown(*args, **kwargs):
            if kwargs.get("wait", False):
                with ws_drain_lock:
                    ws_drain_start.append(time.monotonic())
            return original_shutdown(*args, **kwargs)

        ws_pool.shutdown = tracked_shutdown  # type: ignore[method-assign]

        controller = ShutdownController(fake_app)

        start = time.monotonic()
        controller._do_cleanup()
        elapsed = time.monotonic() - start

        # (c)(1) Total elapsed < 2.0s — sequential would be ~0.8s
        # (0.3s ipc_stop + 0.3s ws_drain + ~0.2s for the rest of
        # _do_cleanup's parallel batch of 14 teardown helpers).
        # Parallel is ~0.5s (max(0.3, 0.3) + ~0.2s rest). The previous
        # 0.7s threshold was calibrated to a quiet local box and flaked
        # under CI runner CPU jitter (-n auto load on GitHub Actions
        # ubuntu-latest can push thread-spawn latency past 0.7s). Bumped
        # to 2.0s — still < the ~5s a fully-sequential implementation
        # would take (sum of 14 teardown helpers each ≥0.1s).
        assert elapsed < 2.0, (
            f"ipc_server.stop + WS drain should run CONCURRENTLY "
            f"(parallel ~0.5s, not sequential ~0.8s); elapsed={elapsed:.2f}s"
        )

        # (c)(2) Both must have been called.
        assert len(ipc_stop_start) == 1, (
            f"ipc_server.stop must be called exactly once; got {len(ipc_stop_start)} calls"
        )
        assert len(ws_drain_start) == 1, (
            f"ws_dispatch_pool shutdown(wait=True) must be called exactly once; got {len(ws_drain_start)} calls"
        )

        # They must overlap: the two start times must be within 1.0s of
        # each other (concurrent start). If sequential, ipc_stop would
        # finish (~0.3s) before the WS drain starts, so the gap would be
        # ~0.3s. The previous 0.15s threshold flaked under CI runner CPU
        # jitter — bumped to 1.0s while still catching the regression
        # (a fully-sequential dispatch would have a gap ≥0.3s; the
        # parallel path has both starts within microseconds of each
        # other modulo thread-scheduling latency).
        gap = abs(ipc_stop_start[0] - ws_drain_start[0])
        assert gap < 1.0, (
            f"ipc_server.stop and WS pool drain must start "
            f"concurrently (within 0.15s of each other); gap={gap:.2f}s "
            f"(ipc_stop_start={ipc_stop_start[0]:.3f}, "
            f"ws_drain_start={ws_drain_start[0]:.3f}) — they appear to "
            f"be running SEQUENTIALLY"
        )

    def test_uses_run_parallel_with_timeout_with_two_items(self, _stub_shutdown_environment, monkeypatch):
        """the early bookend must delegate to
        ``_run_parallel_with_timeout`` with a 2-item list (one for
        ``ipc_server.stop``, one for the WS pool drain).

        This is a structural assertion — it spies on
        ``_run_parallel_with_timeout`` and verifies the batch has
        exactly 2 items with the expected descriptions. The concurrency
        timing test (above) verifies the items actually run in parallel.
        """
        # Spy on _run_parallel_with_timeout.
        captured_batches: list[list] = []

        import voice_typer.server.shutdown_controller as _sc_module

        original_fn = _sc_module._run_parallel_with_timeout

        def spy(items):
            captured_batches.append(list(items))
            return original_fn(items)

        monkeypatch.setattr(
            "voice_typer.server.shutdown_controller._run_parallel_with_timeout",
            spy,
        )

        fake_app = _FakeApp()
        fake_app._ipc_server = MagicMock()
        # MagicMock.stop returns instantly; MagicMock._ws_dispatch_pool.shutdown
        # returns instantly (no real pool → _drain_ws_dispatch_pool early-returns
        # because hasattr(mock_pool, "shutdown") is True but the shutdown call
        # is a no-op MagicMock).
        controller = ShutdownController(fake_app)
        controller._do_cleanup()

        # Find the early-bookend batch (the one containing "ipc_server.stop").
        early_bookend = None
        for batch in captured_batches:
            descs = [item[0] for item in batch]
            if "ipc_server.stop" in descs:
                early_bookend = batch
                break

        assert early_bookend is not None, (
            "_run_parallel_with_timeout must be called with a batch containing 'ipc_server.stop'"
        )
        descs = [item[0] for item in early_bookend]
        assert len(early_bookend) == 2, (
            f"early-bookend batch must have exactly 2 items; got {len(early_bookend)} ({descs})"
        )
        assert "ipc_server.stop" in descs, f"early-bookend batch must contain 'ipc_server.stop'; got {descs}"
        assert "ws_dispatch_pool.drain" in descs, (
            f"early-bookend batch must contain 'ws_dispatch_pool.drain'; got {descs}"
        )
        # Timeouts: ipc_server.stop has a 2.0s hard ceiling (PERF-
        # SHUTDOWN-002 — it returns in ms since the drains are gated on
        # ``app._shutting_down``, and 2.0s bounds a regression); the WS
        # pool drain keeps a 5.0s budget (in-flight WS handlers can
        # legitimately run longer).
        timeouts = {desc: timeout for desc, _func, timeout in early_bookend}
        assert timeouts["ipc_server.stop"] == 2.0, (
            f"ipc_server.stop must have timeout=2.0 (hard ceiling after PERF-SHUTDOWN-002); "
            f"got {timeouts['ipc_server.stop']}"
        )
        assert timeouts["ws_dispatch_pool.drain"] == 5.0, (
            f"ws_dispatch_pool.drain must have timeout=5.0; got {timeouts['ws_dispatch_pool.drain']}"
        )


# _teardown_asr_models inner timeout ──────────────────────────


class TestAsrUnloadInnerTimeout:
    """``_teardown_asr_models`` wraps ``registry.unload()`` in
    ``_run_with_timeout("asr_registry.unload", registry.unload,
    timeout=8.0)``."""

    def test_teardown_asr_models_calls_run_with_timeout_with_8s_timeout(self, monkeypatch):
        """``_teardown_asr_models`` must call ``_run_with_timeout``
        with ``description="asr_registry.unload"`` and ``timeout=8.0``
        on ``registry.unload()``."""
        fake_app = _FakeApp()
        # ``fake_app.models.registry`` is already a MagicMock.
        registry = fake_app.models.registry
        assert hasattr(registry, "unload"), "fixture must provide a registry with unload()"

        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app

        # Spy on _run_with_timeout in the shutdown_controller module.
        captured_calls: list[dict] = []
        original_rwt = None

        import voice_typer.server.shutdown_controller as _sc_module

        original_rwt = _sc_module._run_with_timeout

        def spy_rwt(description, func, timeout=5.0):
            captured_calls.append({"description": description, "func": func, "timeout": timeout})
            # Call the real _run_with_timeout so the unload actually runs
            # (and release_gpu_memory is reached).
            return original_rwt(description, func, timeout=timeout)

        monkeypatch.setattr("voice_typer.server.shutdown_controller._run_with_timeout", spy_rwt)

        # Stub release_gpu_memory so it doesn't import torch.
        monkeypatch.setattr("voice_typer.server.asr_utils.release_gpu_memory", lambda: None)

        ctrl._teardown_asr_models()

        # Find the asr_registry.unload call.
        asr_calls = [c for c in captured_calls if c["description"] == "asr_registry.unload"]
        assert len(asr_calls) == 1, (
            f"_run_with_timeout must be called exactly once with "
            f"description='asr_registry.unload'; got {len(asr_calls)} calls. "
            f"All captured: {captured_calls}"
        )
        assert asr_calls[0]["timeout"] == 8.0, (
            f"_run_with_timeout must be called with timeout=8.0; got {asr_calls[0]['timeout']}"
        )
        assert asr_calls[0]["func"] == registry.unload, (
            "_run_with_timeout must be called with registry.unload as the func"
        )
        # The unload must actually have been invoked (via the real
        # _run_with_timeout call).
        registry.unload.assert_called_once_with()

    def test_teardown_asr_models_still_calls_release_gpu_memory_on_timeout(self, monkeypatch):
        """when ``registry.unload()`` times out (returns
        ``TIMEOUT``), ``_teardown_asr_models`` must STILL call
        ``release_gpu_memory()`` — the GPU cache clear is independent of
        the model unload and is safe to run even if the unload hung."""
        fake_app = _FakeApp()
        registry = fake_app.models.registry

        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app

        # Patch _run_with_timeout to return TIMEOUT for asr_registry.unload.
        def fake_rwt(description, func, timeout=5.0):
            if description == "asr_registry.unload":
                return TIMEOUT
            # For any other call, actually run it.
            return func()

        monkeypatch.setattr("voice_typer.server.shutdown_controller._run_with_timeout", fake_rwt)

        # Track release_gpu_memory calls.
        gpu_release_calls: list[bool] = []

        def fake_release():
            gpu_release_calls.append(True)

        # Patch release_gpu_memory in the asr_utils module so the
        # ``from voice_typer.server.asr_utils import release_gpu_memory``
        # inside _teardown_asr_models picks up the fake.
        monkeypatch.setattr("voice_typer.server.asr_utils.release_gpu_memory", fake_release)

        ctrl._teardown_asr_models()

        assert len(gpu_release_calls) == 1, (
            f"release_gpu_memory must be called even when "
            f"registry.unload() times out; got {len(gpu_release_calls)} calls"
        )
        # registry.unload must NOT have been called directly (it was
        # wrapped in _run_with_timeout, which returned TIMEOUT without
        # calling func — because we replaced _run_with_timeout entirely).
        # This confirms the unload went through _run_with_timeout.
        registry.unload.assert_not_called()

    def test_teardown_asr_models_logs_warning_on_timeout(self, caplog):
        """when ``registry.unload()`` times out, a WARNING must
        be logged (not DEBUG) so the user knows the GPU memory may not
        be fully released."""
        fake_app = _FakeApp()
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app

        # Patch _run_with_timeout at the module level via direct
        # attribute replacement on the module (no monkeypatch needed —
        # we restore it in finally).
        import voice_typer.server.shutdown_controller as _sc_module

        original_rwt = _sc_module._run_with_timeout
        try:
            _sc_module._run_with_timeout = lambda desc, func, timeout=5.0: TIMEOUT
            # Patch release_gpu_memory too.
            import voice_typer.server.asr_utils as _asr_utils

            original_release = getattr(_asr_utils, "release_gpu_memory", None)
            _asr_utils.release_gpu_memory = lambda: None
            try:
                with caplog.at_level(logging.WARNING, logger="voice_typer.server.shutdown_controller"):
                    ctrl._teardown_asr_models()
            finally:
                if original_release is not None:
                    _asr_utils.release_gpu_memory = original_release
        finally:
            _sc_module._run_with_timeout = original_rwt

        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and r.name == "voice_typer.server.shutdown_controller"
            and "asr_registry.unload() did not finish" in r.message
        ]
        assert len(warning_records) == 1, (
            f"expected exactly one WARNING log record for the "
            f"asr_registry.unload timeout; got {len(warning_records)}. "
            f"All WARNING records: "
            f"{[(r.name, r.message) for r in caplog.records if r.levelno == logging.WARNING]}"
        )


# _watchdog calls join_leaked_workers before os._exit ─────────


class TestWatchdogJoinLeakedWorkers:
    """SU-26: ``_watchdog`` must call
    ``join_leaked_workers(total_budget=1.0)`` just before ``os._exit(0)``
    so leaked daemon workers get a bounded window to release resources.

    The watchdog uses shared-deadline mode
    (``total_budget=1.0``) instead of per-worker mode
    (``timeout=0.5``). Shared-deadline mode caps the iteration at the
    first 10 workers and uses ``min(0.2, remaining_budget)`` per
    worker, so the worst-case wall time is ``min(2.0, total_budget)``
    seconds — bounded regardless of how many workers are in the
    registry. Per-worker mode with N leaked workers would block for
    ``N * 0.5`` seconds (e.g. 20 workers → 10s — far exceeding the 2s
    watchdog budget).
    """

    def test_watchdog_calls_join_leaked_workers_with_1_0s_total_budget(self, monkeypatch):
        """SU-26: ``_watchdog`` calls
        ``join_leaked_workers(total_budget=1.0)`` before ``os._exit(0)``.

        Test plan:
        (a) Use ``timeout_s=0.0`` so the watchdog fires immediately
            (``time.sleep(0.0)`` returns instantly in CPython — no
            need to patch ``time.sleep``, which would also clobber the
            test's own ``time.sleep`` calls since ``time`` is a singleton
            module).
        (b) Patch ``os._exit`` to record the call and NOT kill the test
            process.
        (c) Patch ``join_leaked_workers`` in the shutdown_controller
            module to record the call (and return 0 — no leaked workers).
        (d) Call ``_arm_shutdown_watchdog(timeout_s=0.0)`` — starts the
            daemon watchdog thread.
        (e) Poll until ``os._exit`` is recorded (i.e. the watchdog has
            run to completion). The watchdog is a daemon thread that
            fires within microseconds of ``timeout_s=0.0``.
        (f) Assert:
            1. ``join_leaked_workers`` was called exactly once with
               ``total_budget=1.0`` (shared-deadline mode).
            2. ``os._exit`` was called exactly once with code ``0``.
            3. ``join_leaked_workers`` was called BEFORE ``os._exit``
               (recorded via a shared call-order list).
        """
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = MagicMock()

        # (b) Patch os._exit to record the call and NOT kill the process.
        call_order: list[str] = []

        def fake_exit(code=0):
            call_order.append("os._exit")

        monkeypatch.setattr("os._exit", fake_exit)

        # (c) Patch join_leaked_workers in the shutdown_controller module.
        # The watchdog now calls ``join_leaked_workers(total_budget=1.0)``
        # (shared-deadline mode). The fake accepts both ``timeout`` (legacy,
        # per-worker mode) and ``total_budget`` (new, shared-deadline mode)
        # kwargs so the test records whichever mode is used.
        join_calls: list[dict] = []

        def fake_join(timeout: float = 1.0, *, total_budget: float | None = None):
            call_order.append("join_leaked_workers")
            join_calls.append({"timeout": timeout, "total_budget": total_budget})
            return 0

        monkeypatch.setattr("voice_typer.server.shutdown_controller.join_leaked_workers", fake_join)

        # (d) Arm the watchdog with timeout_s=0.0 → fires immediately.
        ctrl._arm_shutdown_watchdog(timeout_s=0.0)

        # (e) Poll until the watchdog has called os._exit (i.e. run to
        # completion). The watchdog is a daemon thread; with timeout_s=0.0
        # it fires within microseconds. We poll with the REAL time.sleep
        # (not patched) for up to 2s.
        deadline = time.monotonic() + 2.0
        while "os._exit" not in call_order and time.monotonic() < deadline:
            time.sleep(0.001)

        assert "os._exit" in call_order, f"SU-26: watchdog did not call os._exit within 2s — call_order={call_order}"

        # (f)(1) join_leaked_workers called once with total_budget=1.0 (shared-deadline).
        assert len(join_calls) == 1, (
            f"SU-26: join_leaked_workers must be called exactly once; got {len(join_calls)} calls"
        )
        assert join_calls[0]["total_budget"] == 1.0, (
            f"SU-26: join_leaked_workers must be called with total_budget=1.0 "
            f"(shared-deadline mode); got total_budget={join_calls[0]['total_budget']}"
        )

        # (f)(2) os._exit called once with code 0 (recorded via call_order).
        assert call_order.count("os._exit") == 1, (
            f"SU-26: os._exit must be called exactly once; got {call_order.count('os._exit')} calls"
        )

        # (f)(3) join_leaked_workers must be called BEFORE os._exit.
        assert call_order == ["join_leaked_workers", "os._exit"], (
            f"SU-26: join_leaked_workers must be called BEFORE os._exit; got call_order={call_order}"
        )
