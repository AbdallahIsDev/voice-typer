"""Shutdown + recording controller performance fixes.

Pins the four findings assigned to this fix slot:

* **IN-17 (High)** — ``shutdown_controller._watchdog`` must call
  ``join_leaked_workers(timeout=0.5)`` BEFORE ``os._exit(0)`` so leaked
  daemon worker threads (from ``_run_with_timeout`` teardowns that
  exceeded their 10s deadline) get a bounded drain window before the
  process is hard-killed. Without the join, ``os._exit(0)`` reaps the
  daemon threads mid-write (mid PortAudio close, mid SQLite flush)
  and leaves external resources in a half-released state.

* **IN-18 (High)** — ``recording_controller._cancelled_cycle_ids`` must
  be bounded. Pre-fix it was a plain ``set[str]`` whose comment claimed
  "Entries are discarded by the pipeline's finally block" but grep
  found NO discard calls anywhere — the set grew by one entry per
  cancel event forever. The fix converts it to a bounded
  ``OrderedDict`` (LRU eviction at ``_MAX_CANCELLED_IDS = 1000``) and
  adds a ``_discard_cancelled_cycle_id`` helper called from
  ``_run_stop_and_transcribe`` after the pipeline returns.

* **IN-19 (High)** — ``_teardown_asr_models`` must run in a SECOND
  ``_run_parallel_with_timeout`` wave, AFTER the first batch (which
  includes ``_teardown_recorder`` and ``_teardown_timers_and_recording``)
  has completed. Pre-fix, ASR teardown ran CONCURRENTLY with recorder
  teardown, racing the recorder's final ``transcribe_words`` call
  against the registry's ``unload()`` of the same backend.

* **IN-20 (Medium)** — ``_toggle_lock`` must be RELEASED for the
  duration of ``ensure_active_engine_loaded()`` (5-30s on idle-unload)
  so the F2 hotkey backend's single dispatch thread is not blocked.
  The lock is re-acquired after the load completes so post-load steps
  (``active_transcriber`` check, streaming-session start) remain
  serialized against concurrent stop / cancel.

These tests use source-inspection (for static contracts) and minimal
runtime harnesses (for behavior) so they don't pull in the full
``VoiceTyperApp`` dependency chain.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from unittest.mock import MagicMock

# ── Helpers ────────────────────────────────────────────────────────────


_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller.py",
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


def _shutdown_src() -> str:
    with open(_SHUTDOWN_CONTROLLER_PATH, encoding="utf-8") as f:
        return f.read()


def _lifecycle_src() -> str:
    with open(_SHUTDOWN_LIFECYCLE_PATH, encoding="utf-8") as f:
        return f.read()


def _recording_src() -> str:
    with open(_RECORDING_CONTROLLER_PATH, encoding="utf-8") as f:
        return f.read()


# ── IN-17: shutdown watchdog calls join_leaked_workers before os._exit ──


class TestIn17WatchdogJoinsLeakedWorkers:
    """IN-17: ``_watchdog`` must call ``join_leaked_workers(timeout=0.5)``
    BEFORE ``os._exit(0)`` so abandoned daemon workers get a bounded
    drain window before the process is hard-killed."""

    def test_join_leaked_workers_is_imported(self) -> None:
        """The module-level import must include ``join_leaked_workers``."""
        s = _shutdown_src()
        assert "join_leaked_workers" in s, "IN-17: shutdown_controller must import join_leaked_workers"
        # Verify it's in an import statement (not just a string in a comment)
        assert "join_leaked_workers," in s or "join_leaked_workers)" in s, (
            "IN-17: join_leaked_workers must appear as an imported name"
        )

    def test_watchdog_calls_join_leaked_workers_before_os_exit(self) -> None:
        """Inside the ``_watchdog`` closure, ``join_leaked_workers`` must
        be called BEFORE ``os._exit(0)`` (the actual call, not a mention
        in a comment).

        extraction: the ``_watchdog`` closure body now
        lives in :mod:`voice_typer.server.shutdown.lifecycle` (the
        ``_arm_shutdown_watchdog`` method on :class:`ShutdownController`
        is a thin delegate). The source-inspection target therefore
        moved from ``shutdown_controller.py`` to ``shutdown/lifecycle.py``.
        """
        s = _lifecycle_src()
        # Find the _watchdog closure body.
        watchdog_idx = s.find("def _watchdog() -> None:")
        assert watchdog_idx > -1, "IN-17: _watchdog closure must exist"
        # Slice to the next def or the end of arm_shutdown_watchdog
        next_def = s.find("\n        def ", watchdog_idx + 1)
        if next_def == -1:
            next_def = s.find("\n    t = threading.Thread(", watchdog_idx + 1)
        body = s[watchdog_idx:next_def]
        assert "join_leaked_workers(" in body, "IN-17: _watchdog body must call join_leaked_workers()"
        # Find the ACTUAL os._exit(0) call (not a comment mention).
        # It must be the LAST occurrence of os._exit(0) in the body
        # (the join precedes it; any earlier mention is in a comment).
        join_idx = body.find("join_leaked_workers(")
        # Find the LAST os._exit(0) in the body — that's the real call.
        exit_idx = body.rfind("os._exit(0)")
        assert exit_idx > -1, "IN-17: _watchdog body must call os._exit(0)"
        assert join_idx < exit_idx, (
            "IN-17: join_leaked_workers must be called BEFORE the actual os._exit(0) call in the _watchdog body"
        )

    def test_watchdog_uses_0_5s_timeout(self) -> None:
        """The per-worker join timeout should be 0.5s (per the IN-17 spec).

        extraction: the ``_watchdog`` closure body now
        lives in :mod:`voice_typer.server.shutdown.lifecycle``.
        """
        s = _lifecycle_src()
        watchdog_idx = s.find("def _watchdog() -> None:")
        next_def = s.find("\n        def ", watchdog_idx + 1)
        if next_def == -1:
            next_def = s.find("\n    t = threading.Thread(", watchdog_idx + 1)
        body = s[watchdog_idx:next_def]
        assert "timeout=0.5" in body, "IN-17: join_leaked_workers must use timeout=0.5 (per spec)"

    def test_watchdog_never_propagates_join_errors(self) -> None:
        """If ``join_leaked_workers`` raises, the watchdog must still call
        ``os._exit(0)`` — the join is best-effort and must never block
        process exit.

        extraction: the ``_watchdog`` closure body now
        lives in :mod:`voice_typer.server.shutdown.lifecycle``.
        """
        s = _lifecycle_src()
        watchdog_idx = s.find("def _watchdog() -> None:")
        next_def = s.find("\n        def ", watchdog_idx + 1)
        if next_def == -1:
            next_def = s.find("\n    t = threading.Thread(", watchdog_idx + 1)
        body = s[watchdog_idx:next_def]
        # The join call must be inside a try/except.
        assert "try:" in body, "IN-17: join_leaked_workers call must be wrapped in try/except"
        assert "except Exception" in body, "IN-17: join_leaked_workers try must catch Exception"

    def test_watchdog_actually_calls_join_at_runtime(self, monkeypatch) -> None:
        """Dynamic test: arm the watchdog with timeout=0 and verify
        ``join_leaked_workers`` is invoked (and ``os._exit`` is stubbed
        so the test process doesn't actually die)."""
        # Avoid importing the full module if it would fail; use a direct
        # import which only pulls in shutdown_controller + _timeout_utils.
        import voice_typer.server.shutdown_controller as sc_mod

        calls: list[float] = []
        exit_calls: list[int] = []

        def fake_join(timeout: float = 1.0) -> int:
            calls.append(timeout)
            return 0

        def fake_exit(code: int = 0) -> None:
            exit_calls.append(code)

        monkeypatch.setattr(sc_mod, "join_leaked_workers", fake_join)
        monkeypatch.setattr(sc_mod.os, "_exit", fake_exit)

        # Bypass __init__ — we only need _arm_shutdown_watchdog.
        ctrl = sc_mod.ShutdownController.__new__(sc_mod.ShutdownController)
        ctrl._app = MagicMock()
        ctrl._arm_shutdown_watchdog(0.05)
        # Wait long enough for the watchdog to fire.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not exit_calls:
            time.sleep(0.01)
        assert exit_calls == [0], f"IN-17: watchdog did not call os._exit(0); exit_calls={exit_calls}"
        assert calls == [0.5], f"IN-17: watchdog did not call join_leaked_workers(timeout=0.5); calls={calls}"


# ── IN-18: _cancelled_cycle_ids is bounded ─────────────────────────────


class TestIn18CancelledCycleIdsBounded:
    """IN-18: ``_cancelled_cycle_ids`` must be a bounded LRU registry so
    it cannot grow unbounded across many cancel events."""

    def test_module_constant_max_cancelled_ids_is_1000(self) -> None:
        """``_MAX_CANCELLED_IDS = 1000`` must be a module-level constant."""
        s = _recording_src()
        assert "_MAX_CANCELLED_IDS = 1000" in s, "IN-18: recording_controller must define _MAX_CANCELLED_IDS = 1000"

    def test_init_uses_ordered_dict(self) -> None:
        """``__init__`` must initialize ``_cancelled_cycle_ids`` to an
        ``OrderedDict`` (not a plain ``set``)."""
        s = _recording_src()
        # The init line — type annotation + value
        assert "OrderedDict[str, None]" in s, "IN-18: _cancelled_cycle_ids must be typed as OrderedDict[str, None]"
        assert "OrderedDict()" in s, "IN-18: _cancelled_cycle_ids must be initialized to OrderedDict()"
        # Import must be present
        assert "from collections import OrderedDict" in s, (
            "IN-18: recording_controller must import OrderedDict from collections"
        )

    def test_mark_cycle_cancelled_method_exists(self) -> None:
        """A ``_mark_cycle_cancelled`` helper method must be defined."""
        s = _recording_src()
        assert "def _mark_cycle_cancelled(self, cycle_id: str) -> None:" in s, (
            "IN-18: _mark_cycle_cancelled method must be defined"
        )

    def test_discard_cancelled_cycle_id_method_exists(self) -> None:
        """A ``_discard_cancelled_cycle_id`` helper method must be defined."""
        s = _recording_src()
        assert "def _discard_cancelled_cycle_id(self, cycle_id: str) -> None:" in s, (
            "IN-18: _discard_cancelled_cycle_id method must be defined"
        )

    def test_no_direct_add_calls_remain(self) -> None:
        """Production code must NOT call ``_cancelled_cycle_ids.add(...)``
        directly — all mutations must go through ``_mark_cycle_cancelled``."""
        s = _recording_src()
        # ``.add(cycle_id)`` on the production OrderedDict is forbidden;
        # the duck-typed branch in _mark_cycle_cancelled uses ``.add()``
        # ONLY when the registry is a plain set (test-double path).
        # Count occurrences and assert they're all in the test-double branch.
        add_count = s.count("_cancelled_cycle_ids.add(cycle_id)")
        # The duck-typed branch is the ONLY allowed call site.
        assert add_count <= 1, (
            f"IN-18: found {add_count} direct _cancelled_cycle_ids.add() calls; "
            f"all mutations must go through _mark_cycle_cancelled"
        )

    def test_lru_eviction_at_1000_entries(self) -> None:
        """Dynamic test: adding 1001 entries evicts the oldest; the
        registry never exceeds ``_MAX_CANCELLED_IDS``."""
        from voice_typer.server.recording_controller import (
            _MAX_CANCELLED_IDS,
            RecordingController,
        )

        assert _MAX_CANCELLED_IDS == 1000

        # Bypass __init__ — we only need the two helper methods + the
        # registry + the lock.
        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()

        # Add MAX+50 entries; the registry must stay bounded.
        for i in range(_MAX_CANCELLED_IDS + 50):
            ctrl._mark_cycle_cancelled(f"cycle-{i}")

        assert len(ctrl._cancelled_cycle_ids) == _MAX_CANCELLED_IDS, (
            f"IN-18: registry size {len(ctrl._cancelled_cycle_ids)} "
            f"!= cap {_MAX_CANCELLED_IDS} after {_MAX_CANCELLED_IDS + 50} adds"
        )
        # Oldest entries must have been evicted (FIFO).
        assert "cycle-0" not in ctrl._cancelled_cycle_ids, (
            "IN-18: oldest entry (cycle-0) was not evicted after cap exceeded"
        )
        assert "cycle-49" not in ctrl._cancelled_cycle_ids, (
            "IN-18: entry cycle-49 was not evicted (first 50 should be gone)"
        )
        # Most-recent entries must still be present.
        assert f"cycle-{_MAX_CANCELLED_IDS + 49}" in ctrl._cancelled_cycle_ids, (
            "IN-18: most-recent entry was incorrectly evicted"
        )

    def test_mark_is_idempotent(self) -> None:
        """Calling ``_mark_cycle_cancelled`` with the same cycle_id twice
        must NOT create a duplicate entry (the registry is a set-like
        membership structure, not a counter)."""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()

        ctrl._mark_cycle_cancelled("cycle-X")
        ctrl._mark_cycle_cancelled("cycle-X")
        ctrl._mark_cycle_cancelled("cycle-X")

        assert len(ctrl._cancelled_cycle_ids) == 1, (
            f"IN-18: idempotent mark failed; len={len(ctrl._cancelled_cycle_ids)}"
        )
        assert "cycle-X" in ctrl._cancelled_cycle_ids

    def test_discard_removes_entry(self) -> None:
        """``_discard_cancelled_cycle_id`` must remove the entry if
        present (silent no-op if not)."""
        from voice_typer.server.recording_controller import RecordingController

        ctrl = RecordingController.__new__(RecordingController)
        ctrl._cancelled_cycle_ids = OrderedDict()
        ctrl._cancelled_cycle_ids_lock = threading.Lock()

        ctrl._mark_cycle_cancelled("cycle-A")
        ctrl._mark_cycle_cancelled("cycle-B")
        assert "cycle-A" in ctrl._cancelled_cycle_ids

        ctrl._discard_cancelled_cycle_id("cycle-A")
        assert "cycle-A" not in ctrl._cancelled_cycle_ids, "IN-18: _discard_cancelled_cycle_id did not remove cycle-A"
        assert "cycle-B" in ctrl._cancelled_cycle_ids, "IN-18: _discard_cancelled_cycle_id incorrectly removed cycle-B"

        # Discarding a non-existent cycle must be a silent no-op.
        ctrl._discard_cancelled_cycle_id("never-existed")
        assert len(ctrl._cancelled_cycle_ids) == 1

    def test_discard_called_after_pipeline_run(self) -> None:
        """``_run_stop_and_transcribe`` must call
        ``_discard_cancelled_cycle_id`` AFTER ``pipeline.run()`` returns."""
        s = _recording_src()
        # Find _run_stop_and_transcribe body
        idx = s.find("def _run_stop_and_transcribe(")
        assert idx > -1
        # Slice to the next def
        next_def = s.find("\n    def ", idx + 1)
        body = s[idx:next_def]
        assert "pipeline.run(" in body, "could not find pipeline.run() in _run_stop_and_transcribe"
        assert "_discard_cancelled_cycle_id(cycle_id)" in body, (
            "IN-18: _run_stop_and_transcribe must call _discard_cancelled_cycle_id"
        )
        # The discard must come AFTER pipeline.run()
        run_idx = body.find("pipeline.run(")
        discard_idx = body.find("_discard_cancelled_cycle_id(cycle_id)")
        assert run_idx < discard_idx, "IN-18: _discard_cancelled_cycle_id must be called AFTER pipeline.run()"


# ── IN-19: ASR teardown runs in second wave ────────────────────────────


class TestIn19AsrTeardownSecondWave:
    """IN-19: ``_teardown_asr_models`` must run AFTER
    ``_teardown_recorder`` (which joins the transcription thread) has
    completed, so the ASR model is never unloaded under a mid-inference
    torch state and the history DB is never closed before the thread's
    write fires.

    extraction contract (as built): the original "second parallel wave"
    design was folded into the sequenced-then-parallel ``ShutdownPlan``
    structure - ``_teardown_recorder`` lives in the SEQUENCED phase
    (which joins the transcription thread) and ``_teardown_asr_models``
    lives in the PARALLEL batch, which only starts after the sequenced
    phase has fully returned. The ordering guarantee IN-19 needs (thread
    joined before ASR unload) therefore holds through the plan driver:
    ``_run_plan(parallel_plan, ...)`` appears textually after
    ``_run_plan(sequenced_plan, ...)`` and consumes its timed-out-step
    set. These tests pin that structure."""

    def test_asr_teardown_not_in_sequenced_plan(self) -> None:
        """``_teardown_asr_models`` must NOT be in the SEQUENCED
        ``ShutdownPlan`` (that phase is the bounded critical flush
        path - the CUDA teardown is deliberately not serialized into
        it).

        The sequenced plan is built with ``sequenced_items.append(...)``
        calls up to the ``sequenced_plan = ShutdownPlan(...)``
        constructor, so the "list body" region is bounded by that
        constructor call, not by a literal closing ``]`` (the parallel
        ``all_parallel_items`` literal appears LATER in the file and its
        closing ``]`` is the first ``\\n        ]`` match — using that as
        the bound would swallow the ASR tuple and false-positive)."""
        s = _shutdown_src()
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
        """``_teardown_asr_models`` must be in the parallel batch (the
        first tuple of ``all_parallel_items``)."""
        s = _shutdown_src()
        par_idx = s.find("all_parallel_items: list[tuple[str, object, float, str | None, bool]] = [")
        assert par_idx > -1, "IN-19: all_parallel_items list not found"
        par_end = s.find("\n        ]", par_idx)
        assert par_end > -1, "IN-19: could not find end of all_parallel_items list"
        parallel_body = s[par_idx:par_end]
        assert '("teardown_asr_models", self._teardown_asr_models' in parallel_body, (
            "IN-19: _teardown_asr_models must be INSIDE the all_parallel_items list"
        )

    def test_sequenced_phase_contains_recorder_and_timers(self) -> None:
        """The SEQUENCED phase must contain the recorder teardown (the
        one that joins the transcription thread) and the timers
        teardown - they run first, before the parallel ASR batch. The
        region is bounded by the ``sequenced_plan = ShutdownPlan(...)``
        constructor (see test_asr_teardown_not_in_sequenced_plan for
        why the literal ``]`` bound would overreach)."""
        s = _shutdown_src()
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
        """The parallel plan's ``_run_plan`` call must appear AFTER the
        sequenced plan's ``_run_plan`` call - the parallel batch (with
        ASR teardown) can only start once the sequenced phase (recorder
        thread join) has returned."""
        s = _shutdown_src()
        sequenced_call = s.find("_timed_out = self._run_plan(sequenced_plan, frozenset())")
        assert sequenced_call > -1, "sequenced plan _run_plan call not found"
        parallel_call = s.find("self._run_plan(parallel_plan, _timed_out)")
        assert parallel_call > -1, "parallel plan _run_plan call not found"
        assert parallel_call > sequenced_call, "IN-19: parallel plan must be run AFTER the sequenced plan returns"


# ── IN-20: _toggle_lock released during ensure_active_engine_loaded ────


class TestIn20ToggleLockReleasedDuringModelLoad:
    """IN-20: ``_toggle_lock`` must be RELEASED for the duration of
    ``ensure_active_engine_loaded()`` (5-30s on idle-unload) so the
    F2 hotkey backend's single dispatch thread is not blocked."""

    def test_release_acquire_around_ensure_active_engine_loaded(self) -> None:
        """The source must contain ``_toggle_lock.release()`` BEFORE
        ``ensure_active_engine_loaded()`` (the actual call site, not a
        docstring mention) and ``_toggle_lock.acquire()`` AFTER (in a
        finally)."""
        s = _recording_src()
        # Find the actual CALL to ensure_active_engine_loaded() in
        # _start_impl. Skip the docstring mentions by anchoring on
        # ``_start_impl`` and slicing forward.
        start_impl_idx = s.find("def _start_impl(self) -> None:")
        assert start_impl_idx > -1, "could not find _start_impl"
        # Slice from _start_impl to the end of the next method.
        next_def = s.find("\n    def ", start_impl_idx + 1)
        start_impl_body = s[start_impl_idx:next_def]
        # Find the actual call (indented, not inside a comment).
        # The call is ``            app.models.ensure_active_engine_loaded()``
        # (12-space indent inside the try block).
        call_marker = "app.models.ensure_active_engine_loaded()"
        # Find the LAST occurrence in _start_impl body — the actual
        # call site. (Earlier occurrences are in docstring comments.)
        ensure_idx = start_impl_body.rfind(call_marker)
        assert ensure_idx > -1, "could not find ensure_active_engine_loaded() call in _start_impl"
        # Look at the surrounding context (release before, acquire after).
        ctx_start = max(0, ensure_idx - 1200)
        ctx_end = min(len(start_impl_body), ensure_idx + 400)
        ctx = start_impl_body[ctx_start:ctx_end]
        assert "self._toggle_lock.release()" in ctx, (
            "IN-20: _toggle_lock.release() must appear before ensure_active_engine_loaded()"
        )
        assert "self._toggle_lock.acquire()" in ctx, (
            "IN-20: _toggle_lock.acquire() must appear after ensure_active_engine_loaded()"
        )
        # The acquire must be in a finally block.
        finally_idx = ctx.find("finally:")
        acquire_idx = ctx.find("self._toggle_lock.acquire()")
        ensure_idx_local = ctx.find(call_marker)
        release_idx = ctx.find("self._toggle_lock.release()")
        assert release_idx < ensure_idx_local, (
            "IN-20: _toggle_lock.release() must come BEFORE ensure_active_engine_loaded()"
        )
        assert ensure_idx_local < acquire_idx, (
            "IN-20: _toggle_lock.acquire() must come AFTER ensure_active_engine_loaded()"
        )
        assert finally_idx > -1 and finally_idx < acquire_idx, (
            "IN-20: _toggle_lock.acquire() must be in a finally block "
            "(so the lock is re-acquired even if the load raises)"
        )

    def test_release_acquire_in_try_finally(self) -> None:
        """The release/acquire pattern must use try/finally so the lock
        is always re-acquired (even on exception)."""
        s = _recording_src()
        idx = s.find("self._toggle_lock.release()")
        assert idx > -1
        # Slice forward to verify the try/finally structure
        ctx = s[idx : idx + 400]
        assert "try:" in ctx, "IN-20: release must be followed by try:"
        assert "app.models.ensure_active_engine_loaded()" in ctx
        assert "finally:" in ctx, "IN-20: try must have a finally block"
        assert "self._toggle_lock.acquire()" in ctx, "IN-20: finally must re-acquire the lock"

    def test_lock_actually_released_during_load_at_runtime(self) -> None:
        """Dynamic test: when ``ensure_active_engine_loaded()`` blocks,
        another thread MUST be able to acquire ``_toggle_lock``
        (proving the lock was released)."""
        from voice_typer.server.recording_controller import RecordingController

        # Bypass __init__ — we need _toggle_lock + _app.
        ctrl = RecordingController.__new__(RecordingController)
        ctrl._toggle_lock = threading.RLock()
        app = MagicMock()
        # voice_biometric_consent must be True so _start_impl proceeds
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
        # tray + hotkeys are MagicMocks; any attr access returns a mock.
        app.tray = MagicMock()
        app.hotkeys = MagicMock()
        # Models: ensure_active_engine_loaded blocks until we set the event.
        load_started = threading.Event()
        load_can_finish = threading.Event()

        def blocking_load():
            load_started.set()
            # Block here with the lock RELEASED (per IN-20). The test
            # thread will verify the lock is acquirable, then signal.
            load_can_finish.wait(timeout=2.0)

        app.models.ensure_active_engine_loaded = blocking_load
        # active_transcriber must return a loaded mock so _start_impl
        # finishes the happy path after the load returns.
        app.models.active_transcriber.return_value = MagicMock(is_loaded=True)
        app.models.apply_pending_model_change = MagicMock()
        # Provide a real threading.Event for _shutting_down_event so
        # event_bus.publish (MagicMock) calls don't crash.
        app._shutting_down_event = threading.Event()

        # Start the model load on a worker thread (so we can observe
        # the lock state from the test's main thread).
        worker = threading.Thread(target=ctrl.start, name="start-worker", daemon=True)
        worker.start()
        try:
            # Wait for the load to start (lock should now be RELEASED).
            assert load_started.wait(timeout=1.0), "IN-20: ensure_active_engine_loaded was never called"
            # Give the worker a moment to enter the blocking_load body.
            time.sleep(0.05)
            # The lock MUST be acquirable now (proving it was released
            # during the model load). acquire(blocking=False) returns
            # True if the lock was free.
            acquired = ctrl._toggle_lock.acquire(blocking=False)
            assert acquired, (
                "IN-20: _toggle_lock was NOT released during "
                "ensure_active_engine_loaded() — another thread cannot "
                "acquire it (F2 hotkey backend would be blocked for 5-30s)"
            )
            ctrl._toggle_lock.release()
        finally:
            # Let the worker finish so it doesn't leak.
            load_can_finish.set()
            worker.join(timeout=2.0)
