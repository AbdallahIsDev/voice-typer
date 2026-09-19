"""Shutdown + recording controller performance fixes."""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from unittest.mock import MagicMock

# ``shutdown/`` package. Each pinned region moved with its body:
_SC_PACKAGE_INIT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller",
    "__init__.py",
)
_SC_PLANS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "plan.py",
)
_SC_CLEANUP_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "cleanup.py",
)
_SHUTDOWN_LIFECYCLE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "lifecycle.py",
)
_RECORDING_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "recording_controller.py",
)
_RECORDING_LIFECYCLE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "recording_lifecycle.py",
)
_WATCHDOG_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "transcription_watchdog.py",
)


def _shutdown_src() -> str:
    with open(_SC_PACKAGE_INIT_PATH, encoding="utf-8") as f:
        return f.read()


def _plans_src() -> str:
    with open(_SC_PLANS_PATH, encoding="utf-8") as f:
        return f.read()


def _cleanup_src() -> str:
    with open(_SC_CLEANUP_PATH, encoding="utf-8") as f:
        return f.read()


def _lifecycle_src() -> str:
    with open(_SHUTDOWN_LIFECYCLE_PATH, encoding="utf-8") as f:
        return f.read()


def _recording_src() -> str:
    with open(_RECORDING_CONTROLLER_PATH, encoding="utf-8") as f:
        return f.read()


def _recording_lifecycle_src() -> str:
    with open(_RECORDING_LIFECYCLE_PATH, encoding="utf-8") as f:
        return f.read()


def _watchdog_src() -> str:
    with open(_WATCHDOG_PATH, encoding="utf-8") as f:
        return f.read()


class TestIn17WatchdogJoinsLeakedWorkers:
    """``_watchdog`` must call"""

    def test_join_leaked_workers_is_imported(self) -> None:
        """The module-level import must include ``join_leaked_workers``."""
        s = _shutdown_src()
        assert "join_leaked_workers" in s, "shutdown_controller must import join_leaked_workers"
        # Verify it's in an import statement (not just a string in a comment)
        assert "join_leaked_workers," in s or "join_leaked_workers)" in s, (
            "join_leaked_workers must appear as an imported name"
        )

    def test_watchdog_calls_join_leaked_workers_before_os_exit(self) -> None:
        """Inside the ``_watchdog`` closure, ``join_leaked_workers`` must"""
        s = _lifecycle_src()
        # Find the _watchdog closure body.
        watchdog_idx = s.find("def _watchdog() -> None:")
        assert watchdog_idx > -1, "_watchdog closure must exist"
        # Slice to the next def or the end of arm_shutdown_watchdog
        next_def = s.find("\n        def ", watchdog_idx + 1)
        if next_def == -1:
            next_def = s.find("\n    t = threading.Thread(", watchdog_idx + 1)
        body = s[watchdog_idx:next_def]
        assert "join_leaked_workers(" in body, "_watchdog body must call join_leaked_workers()"
        # Find the ACTUAL os._exit(0) call (not a comment mention).
        join_idx = body.find("join_leaked_workers(")
        # Find the LAST os._exit(0) in the body, that's the real call.
        exit_idx = body.rfind("os._exit(0)")
        assert exit_idx > -1, "_watchdog body must call os._exit(0)"
        assert join_idx < exit_idx, (
            "join_leaked_workers must be called BEFORE the actual os._exit(0) call in the _watchdog body"
        )

    def test_watchdog_uses_1_0s_total_budget(self) -> None:
        """the watchdog uses shared-deadline mode"""
        s = _lifecycle_src()
        watchdog_idx = s.find("def _watchdog() -> None:")
        next_def = s.find("\n        def ", watchdog_idx + 1)
        if next_def == -1:
            next_def = s.find("\n    t = threading.Thread(", watchdog_idx + 1)
        body = s[watchdog_idx:next_def]
        assert "total_budget=1.0" in body, "join_leaked_workers must use total_budget=1.0 (shared-deadline mode)"

    def test_watchdog_never_propagates_join_errors(self) -> None:
        """If ``join_leaked_workers`` raises, the watchdog must still call"""
        s = _lifecycle_src()
        watchdog_idx = s.find("def _watchdog() -> None:")
        next_def = s.find("\n        def ", watchdog_idx + 1)
        if next_def == -1:
            next_def = s.find("\n    t = threading.Thread(", watchdog_idx + 1)
        body = s[watchdog_idx:next_def]
        # The join call must be inside a try/except.
        assert "try:" in body, "join_leaked_workers call must be wrapped in try/except"
        assert "except Exception" in body, "join_leaked_workers try must catch Exception"

    def test_watchdog_actually_calls_join_at_runtime(self, monkeypatch) -> None:
        """Dynamic test: arm the watchdog with timeout=0 and verify"""
        # Avoid importing the full module if it would fail; use a direct
        import voice_typer.server.shutdown_controller as sc_mod

        calls: list[dict] = []
        exit_calls: list[int] = []

        def fake_join(timeout: float = 1.0, *, total_budget: float | None = None) -> int:
            calls.append({"timeout": timeout, "total_budget": total_budget})
            return 0

        def fake_exit(code: int = 0) -> None:
            exit_calls.append(code)

        monkeypatch.setattr(sc_mod, "join_leaked_workers", fake_join)
        monkeypatch.setattr(sc_mod.os, "_exit", fake_exit)

        # Bypass __init__, we only need _arm_shutdown_watchdog.
        ctrl = sc_mod.ShutdownController.__new__(sc_mod.ShutdownController)
        ctrl._app = MagicMock()
        ctrl._arm_shutdown_watchdog(0.05)
        # Wait long enough for the watchdog to fire.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not exit_calls:
            time.sleep(0.01)
        assert exit_calls == [0], f"watchdog did not call os._exit(0); exit_calls={exit_calls}"
        assert len(calls) == 1, f"watchdog must call join_leaked_workers exactly once; got {len(calls)} calls"
        assert calls[0]["total_budget"] == 1.0, (
            f"watchdog must call join_leaked_workers(total_budget=1.0) (shared-deadline mode); got calls={calls}"
        )


class TestIn18CancelledCycleIdsBounded:
    """``_cancelled_cycle_ids`` must be a bounded LRU registry so"""

    def test_module_constant_max_cancelled_ids_is_1000(self) -> None:
        """``_MAX_CANCELLED_IDS = 1000`` must be a module-level constant."""
        s = _watchdog_src()
        assert "_MAX_CANCELLED_IDS = 1000" in s, "transcription_watchdog must define _MAX_CANCELLED_IDS = 1000"

    def test_init_uses_ordered_dict(self) -> None:
        """``__init__`` must initialize ``_cancelled_cycle_ids`` to an"""
        s = _recording_src()
        # The init line, type annotation + value
        assert "OrderedDict[str, None]" in s, "_cancelled_cycle_ids must be typed as OrderedDict[str, None]"
        assert "OrderedDict()" in s, "_cancelled_cycle_ids must be initialized to OrderedDict()"
        # Import must be present
        assert "from collections import OrderedDict" in s, (
            "recording_controller must import OrderedDict from collections"
        )

    def test_mark_cycle_cancelled_method_exists(self) -> None:
        """A ``_mark_cycle_cancelled`` helper method must be defined."""
        s = _recording_src()
        assert "def _mark_cycle_cancelled(self, cycle_id: str) -> None:" in s, (
            "_mark_cycle_cancelled method must be defined"
        )

    def test_discard_cancelled_cycle_id_method_exists(self) -> None:
        """A ``_discard_cancelled_cycle_id`` helper method must be defined."""
        s = _recording_src()
        assert "def _discard_cancelled_cycle_id(self, cycle_id: str) -> None:" in s, (
            "_discard_cancelled_cycle_id method must be defined"
        )

    def test_no_direct_add_calls_remain(self) -> None:
        """Production code must NOT call ``_cancelled_cycle_ids.add(...)``"""
        s = _recording_src()
        # ``.add(cycle_id)`` on the production OrderedDict is forbidden;
        add_count = s.count("_cancelled_cycle_ids.add(cycle_id)")
        # The duck-typed branch is the ONLY allowed call site.
        assert add_count <= 1, (
            f"found {add_count} direct _cancelled_cycle_ids.add() calls; "
            f"all mutations must go through _mark_cycle_cancelled"
        )

    def test_lru_eviction_at_1000_entries(self) -> None:
        """registry never exceeds ``_MAX_CANCELLED_IDS``."""
        from voice_typer.server.recording_controller import RecordingController
        from voice_typer.server.transcription_watchdog import (
            _MAX_CANCELLED_IDS,
            TranscriptionWatchdog,
        )

        assert _MAX_CANCELLED_IDS == 1000

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()
        ctrl._watchdog_helper = TranscriptionWatchdog()

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()

        # Add MAX+50 entries; the registry must stay bounded.
        for i in range(_MAX_CANCELLED_IDS + 50):
            ctrl._mark_cycle_cancelled(f"cycle-{i}")

        assert len(ctrl._cancelled_cycle_ids) == _MAX_CANCELLED_IDS, (
            f"registry size {len(ctrl._cancelled_cycle_ids)} "
            f"!= cap {_MAX_CANCELLED_IDS} after {_MAX_CANCELLED_IDS + 50} adds"
        )
        # Oldest entries must have been evicted (FIFO).
        assert "cycle-0" not in ctrl._cancelled_cycle_ids, "oldest entry (cycle-0) was not evicted after cap exceeded"
        assert "cycle-49" not in ctrl._cancelled_cycle_ids, "entry cycle-49 was not evicted (first 50 should be gone)"
        # Most-recent entries must still be present.
        assert f"cycle-{_MAX_CANCELLED_IDS + 49}" in ctrl._cancelled_cycle_ids, (
            "most-recent entry was incorrectly evicted"
        )

    def test_mark_is_idempotent(self) -> None:
        """Calling ``_mark_cycle_cancelled`` with the same cycle_id twice"""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()

        ctrl._mark_cycle_cancelled("cycle-X")
        ctrl._mark_cycle_cancelled("cycle-X")
        ctrl._mark_cycle_cancelled("cycle-X")

        assert len(ctrl._cancelled_cycle_ids) == 1, f"idempotent mark failed; len={len(ctrl._cancelled_cycle_ids)}"
        assert "cycle-X" in ctrl._cancelled_cycle_ids

    def test_discard_removes_entry(self) -> None:
        """``_discard_cancelled_cycle_id`` must remove the entry if"""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()

        ctrl._mark_cycle_cancelled("cycle-A")
        ctrl._mark_cycle_cancelled("cycle-B")
        assert "cycle-A" in ctrl._cancelled_cycle_ids

        ctrl._discard_cancelled_cycle_id("cycle-A")
        assert "cycle-A" not in ctrl._cancelled_cycle_ids, "_discard_cancelled_cycle_id did not remove cycle-A"
        assert "cycle-B" in ctrl._cancelled_cycle_ids, "_discard_cancelled_cycle_id incorrectly removed cycle-B"

        # Discarding a non-existent cycle must be a silent no-op.
        ctrl._discard_cancelled_cycle_id("never-existed")
        assert len(ctrl._cancelled_cycle_ids) == 1

    def test_discard_called_after_pipeline_run(self) -> None:
        """``_run_stop_and_transcribe`` must call"""
        s = _recording_lifecycle_src()
        # Find _run_stop_and_transcribe body
        idx = s.find("def _run_stop_and_transcribe(")
        assert idx > -1
        # Slice to the next def
        next_def = s.find("\n    def ", idx + 1)
        body = s[idx:next_def]
        assert "pipeline.run(" in body, "could not find pipeline.run() in _run_stop_and_transcribe"
        assert "_discard_cancelled_cycle_id(cycle_id)" in body, (
            "_run_stop_and_transcribe must call _discard_cancelled_cycle_id"
        )
        # The discard must come AFTER pipeline.run()
        run_idx = body.find("pipeline.run(")
        discard_idx = body.find("_discard_cancelled_cycle_id(cycle_id)")
        assert run_idx < discard_idx, "_discard_cancelled_cycle_id must be called AFTER pipeline.run()"


class TestIn19AsrTeardownSecondWave:
    """IN-19: ``_teardown_asr_models`` must run AFTER"""

    def test_asr_teardown_not_in_sequenced_plan(self) -> None:
        """``_teardown_asr_models`` must NOT be in the SEQUENCED"""
        s = _plans_src()
        seq_idx = s.find("sequenced_items: list[tuple[str, object, float, str | None, bool]] = []")
        assert seq_idx > -1, "IN-19: sequenced_items list not found"
        seq_ctor = s.find("sequenced_plan = ShutdownPlan(", seq_idx)
        assert seq_ctor > -1, "IN-19: sequenced_plan constructor not found"
        sequenced_region = s[seq_idx:seq_ctor]
        assert '("teardown_asr_models",' not in sequenced_region, (
            "IN-19: _teardown_asr_models must NOT be in the sequenced_items "
            "list (ASR teardown stays in the parallel batch, after the "
            "sequenced phase joins the transcription thread)"
        )

    def test_asr_teardown_in_parallel_plan(self) -> None:
        """``_teardown_asr_models`` must be in the parallel batch (the"""
        s = _plans_src()
        par_idx = s.find("all_parallel_items: list[tuple[str, object, float, str | None, bool]] = [")
        assert par_idx > -1, "IN-19: all_parallel_items list not found"
        # The builder body is a module-level function (4-space body
        par_end = s.find("\n    ]", par_idx)
        assert par_end > -1, "IN-19: could not find end of all_parallel_items list"
        parallel_body = s[par_idx:par_end]
        assert '("teardown_asr_models", controller._teardown_asr_models' in parallel_body, (
            "IN-19: _teardown_asr_models must be INSIDE the all_parallel_items list"
        )

    def test_sequenced_phase_contains_recorder_and_timers(self) -> None:
        """one that joins the transcription thread) and the timers"""
        s = _plans_src()
        seq_idx = s.find("sequenced_items: list[tuple[str, object, float, str | None, bool]] = []")
        assert seq_idx > -1, "IN-19: sequenced_items list not found"
        seq_ctor = s.find("sequenced_plan = ShutdownPlan(", seq_idx)
        assert seq_ctor > -1, "IN-19: sequenced_plan constructor not found"
        sequenced_region = s[seq_idx:seq_ctor]
        assert '("teardown_recorder",' in sequenced_region, "IN-19: _teardown_recorder must be in the sequenced phase"
        assert '("teardown_timers_and_recording",' in sequenced_region, (
            "IN-19: _teardown_timers_and_recording must be in the sequenced phase"
        )

    def test_parallel_plan_runs_after_sequenced_completes(self) -> None:
        """ASR teardown) can only start once the sequenced phase (recorder"""
        s = _cleanup_src()
        sequenced_call = s.find("_timed_out = controller._run_plan(sequenced_plan, frozenset())")
        assert sequenced_call > -1, "sequenced plan _run_plan call not found"
        parallel_call = s.find("controller._run_plan(parallel_plan, _timed_out)")
        assert parallel_call > -1, "parallel plan _run_plan call not found"
        assert parallel_call > sequenced_call, "IN-19: parallel plan must be run AFTER the sequenced plan returns"


class TestIn20ToggleLockReleasedDuringModelLoad:
    """F2 hotkey backend's single dispatch thread is not blocked."""

    def test_join_outside_lock_no_manual_release(self) -> None:
        """The model load runs on the daemon worker thread"""
        s = _recording_lifecycle_src()
        worker_idx = s.find("def _start_dictation_worker_entry(")
        assert worker_idx > -1, "could not find _start_dictation_worker_entry in recording_lifecycle"
        worker_next = s.find("\n    def ", worker_idx + 1)
        worker_body = s[worker_idx:worker_next]
        assert "app.models.ensure_active_engine_loaded()" in worker_body, (
            "IN-20: ensure_active_engine_loaded() must be called on the "
            "daemon worker thread (the F2 dispatch thread must not run "
            "the 5-30s model load)"
        )
        # _start_impl must NOT manually release/re-acquire the lock: the
        start_impl_idx = s.find("def _start_impl(self, controller)")
        assert start_impl_idx > -1, "could not find _start_impl in recording_lifecycle"
        next_def = s.find("\n    def ", start_impl_idx + 1)
        body = s[start_impl_idx:next_def]
        assert "controller._toggle_lock.release()" not in body, (
            "_start_impl must NOT manually release _toggle_lock, the "
            "public entry owns the lock lifecycle and joins outside it"
        )
        assert "controller._toggle_lock.acquire()" not in body, (
            "_start_impl must NOT manually re-acquire _toggle_lock, the "
            "public entry owns the lock lifecycle and joins outside it"
        )
        # The bounded join lives in _run_public_entry, at method-body
        entry_idx = s.find("def _run_public_entry(self, controller, impl_method)")
        assert entry_idx > -1, "could not find _run_public_entry in recording_lifecycle"
        entry_next = s.find("\n    def ", entry_idx + 1)
        entry_body = s[entry_idx:entry_next]
        assert "with controller._toggle_lock:" in entry_body, (
            "_run_public_entry must acquire the lock around the state decision"
        )
        join_idx = entry_body.find("worker.join(timeout=")
        assert join_idx > -1, "_run_public_entry must bounded-join the worker (worker.join(timeout=...))"
        with_lock_idx = entry_body.find("with controller._toggle_lock:")
        finally_idx = entry_body.find("finally:")
        assert with_lock_idx > -1 and with_lock_idx < join_idx, (
            "the bounded worker join must come AFTER the with _toggle_lock block"
        )
        assert finally_idx > -1 and finally_idx < join_idx, (
            "the bounded worker join must come AFTER the locked section's "
            "finally block (the lock is released before the join)"
        )

    def test_manual_release_pattern_stays_gone(self) -> None:
        """worker join must stay gone module-wide, reintroducing it would"""
        s = _recording_lifecycle_src()
        assert "controller._toggle_lock.release()" not in s, (
            "manual _toggle_lock.release() must not reappear in "
            "recording_lifecycle, the public entry joins outside the lock"
        )
        assert "controller._toggle_lock.acquire()" not in s, (
            "manual _toggle_lock.acquire() must not reappear in "
            "recording_lifecycle, the public entry joins outside the lock"
        )

    def test_lock_actually_released_during_load_at_runtime(self) -> None:
        """Dynamic test: when ``ensure_active_engine_loaded()`` blocks,"""
        from voice_typer.server.recording_controller import RecordingController

        # Bypass __init__, we need _toggle_lock + _app.
        ctrl = RecordingController.__new__(RecordingController)
        ctrl._toggle_lock = threading.RLock()
        app = MagicMock()
        app.config.voice_biometric_consent = True
        app.config.streaming_transcription = False
        app.config.esc_cancel_enabled = False
        app.config.bubble_behavior = "hide"
        app.config.sample_rate = 16000
        app.recorder.recording = False
        app._busy_event = threading.Event()
        app._busy_event.set()  # not busy
        app._cycle_id = "#test"
        app._cycle_counter = 0
        app._cancel_pending_timers = MagicMock()
        app._duck_volume = MagicMock()
        app._waveform_bubble = MagicMock()
        app._audio_quality = MagicMock()
        app._stop_level_monitor_for_recorder_start = MagicMock()
        # Stub _stop_level_monitor_for_recorder_start on the controller.
        ctrl._stop_level_monitor_for_recorder_start = lambda: None
        ctrl._start_streaming_session_if_enabled = lambda: None
        ctrl._cancel_streaming_session = lambda: None
        ctrl._app = app
        app.tray = MagicMock()
        app.hotkeys = MagicMock()
        # Models: ensure_active_engine_loaded blocks until we set the event.
        load_started = threading.Event()
        load_can_finish = threading.Event()

        def blocking_load():
            load_started.set()
            # Block here with the lock RELEASED (per IN-20). The test
            load_can_finish.wait(timeout=2.0)

        app.models.ensure_active_engine_loaded = blocking_load
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        app.models.apply_pending_model_change = MagicMock()
        app._shutting_down_event = threading.Event()

        # Start the model load on a worker thread (so we can observe
        worker = threading.Thread(target=ctrl.start, name="start-worker", daemon=True)
        worker.start()
        try:
            # Wait for the load to start (lock should now be RELEASED).
            assert load_started.wait(timeout=1.0), "IN-20: ensure_active_engine_loaded was never called"
            # Give the worker a moment to enter the blocking_load body.
            time.sleep(0.05)
            # The lock MUST be acquirable now (proving it was released
            acquired = ctrl._toggle_lock.acquire(blocking=False)
            assert acquired, (
                "IN-20: _toggle_lock was NOT released during "
                "ensure_active_engine_loaded(), another thread cannot "
                "acquire it (F2 hotkey backend would be blocked for 5-30s)"
            )
            ctrl._toggle_lock.release()
        finally:
            # Let the worker finish so it doesn't leak.
            load_can_finish.set()
            worker.join(timeout=2.0)
