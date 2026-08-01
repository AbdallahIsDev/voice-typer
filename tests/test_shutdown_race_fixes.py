"""Race-condition regression tests for the shutdown teardown sequence.

These tests pin two critical race fixes applied to
``voice_typer/server/shutdown_controller.py``:

* **F1 (OI-4 — Critical)** — The transcription thread (spawned by
  ``recorder.stop()``) runs ASR inference and writes its result to
  ``history_db`` via fire-and-forget ``add_transcription()``. Pre-fix,
  ``_do_cleanup`` ran ``_teardown_recorder``, ``_teardown_history_db``,
  and ``_teardown_asr_models`` CONCURRENTLY in a single parallel wave —
  racing the thread's inference + DB write. The ASR model could be
  unloaded under the mid-inference thread (segfault / undefined torch
  state), and the DB could be closed before the thread's
  ``add_transcription()`` fired (silent drop of the user's last
  utterance). The fix moves the dependent teardowns into a SEQUENCED
  phase that runs BEFORE the parallel batch:

    1. ``_teardown_timers_and_recording`` (cancel timers, pop streaming session)
    2. ``_teardown_recorder`` (recorder.stop + join transcription thread)
    3. ``_teardown_history_db`` (flush + close — drains the thread's pending write)
    4. ``_teardown_crash_recovery`` (flush + shutdown)

  ``_teardown_asr_models`` stays in the parallel batch — the sequenced
  phase completes BEFORE the parallel batch starts, so the
  transcription thread is already joined by the time the ASR model is
  unloaded.

* **F2 (OI-5 — Critical)** — ``_do_cleanup`` sets ``_cleanup_done =
  True`` at the very START (before any actual cleanup). Pre-fix,
  ``_do_fast_cleanup`` (Windows logoff/shutdown fast path) checked this
  flag and SKIPPED its own critical flushes when it was True. If a
  normal ``quit()`` was in flight (had set the flag but not yet reached
  the parallel batch) when Windows logoff fired ``_do_fast_cleanup``,
  BOTH paths skipped the critical writes — the slow one was killed by
  ``os._exit(0)`` mid-flight, the fast one short-circuited. The fix
  removes the ``if not already_done:`` gate so the flushes run
  UNCONDITIONALLY on every invocation (the writes are idempotent;
  running them twice is safe).

These tests use a real ``threading.Thread`` to simulate the
transcription thread (so the join semantics are observable) and mock
the external dependencies (recorder, history_db, crash_recovery, etc.)
so the tests run headless on Linux without touching real subsystems.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

# Direct import — does NOT pull in voice_typer.server.app, so the
# clipboard_target_safety circular-import breakage in a parallel
# agent's WIP doesn't block these tests (same pattern as
# tests/test_shutdown_parallel.py).
from voice_typer.server.shutdown_controller import ShutdownController

# ── Override the autouse ``mock_heavy_imports`` conftest fixture ───────
#
# The shared ``tests/conftest.py::mock_heavy_imports`` fixture is
# autouse and tries to ``monkeypatch.setattr("voice_typer.server.app.
# atexit.register", ...)``. That ``setattr`` call triggers an import
# of ``voice_typer.server.app``, which (during a parallel agent's WIP)
# may raise ``ImportError``. These tests don't need
# ``voice_typer.server.app`` at all — they use a ``_FakeApp`` duck-typed
# stand-in. We override the autouse fixture with a no-op so the broken
# import doesn't break our test setup. The override is scoped to this
# module only (same pattern as tests/test_shutdown_parallel.py).


@pytest.fixture(autouse=True)
def mock_heavy_imports():
    """No-op override of the conftest autouse fixture.

    These tests don't need heavy-import mocking — they use a
    ``_FakeApp`` and inject mock modules into ``sys.modules`` directly.
    Overriding here avoids the broken ``voice_typer.server.app`` import
    in the shared conftest.
    """
    yield


@pytest.fixture(autouse=True)
def _stub_os_exit(monkeypatch):
    """Stub ``os._exit`` so tests that invoke ``_do_fast_cleanup``
    directly don't actually exit the test runner. Mirrors the autouse
    fixture in tests/test_shutdown_fast.py."""
    calls: list[int] = []
    monkeypatch.setattr(
        "voice_typer.server.shutdown_controller.os._exit",
        lambda code=0: calls.append(code),
    )
    yield calls


# ── Fake app ───────────────────────────────────────────────────────────


class _FakeApp:
    """Minimal duck-typed stand-in for ``VoiceTyperApp``.

    Mirrors the collaborator surface that ``ShutdownController._do_cleanup``
    and ``_do_fast_cleanup`` touch. Every subsystem is a ``MagicMock``
    so we can assert call counts without running real teardown code.
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
        # ``_do_fast_cleanup`` touches these — give them MagicMock defaults
        # so attribute access doesn't raise.
        self._restore_volume = MagicMock()
        self._duck_crash_recovery = MagicMock()

        self._cancel_pending_timers = MagicMock()

        # ``_do_cleanup`` looks up ``app._ipc_server`` for the WS drain.
        self._ipc_server = None

        # ``_teardown_asr_models`` reads ``app.models.registry``.
        self.models = MagicMock()
        self.models.registry = MagicMock()


@pytest.fixture
def fake_app(monkeypatch):
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed.

    The teardown helpers do ``from voice_typer.server import app``
    inside their bodies (for ``_clear_backend_pid_file`` /
    ``_close_devnull_files``). We pre-install ``voice_typer.server.app``
    as a ``MagicMock`` so the dynamic import succeeds without pulling
    in the real (potentially broken) app module. ``sys.modules``
    injection is the standard way to short-circuit a
    ``from X import Y`` statement.
    """
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    fake_app_module.is_windows = lambda: False
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-race-fixes"
    monkeypatch.setitem(sys.modules, "voice_typer.server.app", fake_app_module)

    # event_bus.shutdown is imported dynamically inside the helper.
    fake_event_bus = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.event_bus", fake_event_bus)

    # level_monitor.stop_monitoring is imported dynamically inside the helper.
    fake_level_monitor = MagicMock()
    monkeypatch.setitem(sys.modules, "voice_typer.server.level_monitor", fake_level_monitor)

    return _FakeApp()


@pytest.fixture
def controller(fake_app):
    """A ``ShutdownController`` wrapping ``fake_app``.

    Wires ``fake_app._do_cleanup`` to delegate to the controller's real
    body (via ``side_effect``), mirroring the post-extraction delegate
    on ``VoiceTyperApp``.
    """
    ctrl = ShutdownController(fake_app)
    fake_app._do_cleanup = MagicMock(side_effect=ctrl._do_cleanup)
    return ctrl


# ── F1: transcription thread join BEFORE history_db.close() ───────────


class TestTranscriptionThreadJoinBeforeDbClose:
    """F1 (OI-4): the transcription thread join (inside
    ``_teardown_recorder``) must complete BEFORE ``history_db.close()``
    is called (inside ``_teardown_history_db``).

    Pre-fix, both helpers ran CONCURRENTLY in the same parallel wave —
    the join could still be in progress while ``close()`` fired,
    silently dropping the thread's pending ``add_transcription()`` write.
    The fix moves both helpers into a SEQUENCED phase that runs BEFORE
    the parallel batch, so the join completes before ``close()``.

    These tests use a REAL ``threading.Thread`` to simulate the
    transcription thread (so the join semantics are observable) and
    record timestamps to verify the ordering invariant."""

    def test_transcription_thread_join_completes_before_history_db_close(self, controller, fake_app):
        """The transcription thread's "write complete" timestamp must
        be EARLIER than the ``history_db.close()`` call timestamp.

        We spawn a real thread that sleeps 100ms (simulating ASR
        inference) then records a "write complete" timestamp. We wire
        it as ``app.recording._transcription_thread``. We spy on
        ``history_db.close`` to record a "close called" timestamp.

        With the sequenced-phase fix, ``_teardown_recorder`` runs
        FIRST (joins the thread — waits for the 100ms inference +
        write), THEN ``_teardown_history_db`` runs (calls close()).
        The "write complete" timestamp must be < "close called"
        timestamp.

        Pre-fix (parallel batch), both helpers started simultaneously.
        ``_teardown_history_db`` would call ``close()`` within ~1ms of
        start, while the thread was still sleeping for 100ms. The
        "close called" timestamp would be ~99ms EARLIER than "write
        complete" — the race we're fixing."""
        # Spawn a real transcription thread that simulates ASR inference.
        write_complete_event = threading.Event()
        timestamps: dict[str, float] = {}
        write_started_event = threading.Event()

        def _simulate_transcription():
            # Simulate ASR inference (100ms — typical Whisper inference
            # is 1-5s; 100ms keeps the test fast while still being
            # observable).
            write_started_event.set()
            time.sleep(0.1)
            timestamps["write_complete"] = time.monotonic()
            # Simulate the fire-and-forget add_transcription() write
            # that races the DB close.
            with contextlib_suppress(Exception):
                fake_app.history_db.add_transcription(text="hello world")
            write_complete_event.set()

        transcription_thread = threading.Thread(
            target=_simulate_transcription,
            name="simulated-transcription",
            daemon=True,
        )
        # Wire the thread as the recording controller's transcription thread.
        fake_app.recording._transcription_thread = transcription_thread

        # Spy on history_db.close to record the call timestamp.
        original_close = fake_app.history_db.close

        def _spy_close(*args, **kwargs):
            timestamps["close_called"] = time.monotonic()
            return original_close(*args, **kwargs)

        fake_app.history_db.close = _spy_close  # type: ignore[assignment]

        # Start the transcription thread BEFORE _do_cleanup so it's
        # mid-inference when shutdown begins (mirrors the real race:
        # the user dictates something, then presses stop / quit).
        transcription_thread.start()
        # Wait until the thread has started its "inference" sleep so
        # we know it's genuinely in-flight when _do_cleanup runs.
        write_started_event.wait(timeout=1.0)
        assert write_started_event.is_set(), (
            "Test setup failure: simulated transcription thread did not start its inference sleep within 1s"
        )

        # Run _do_cleanup — the sequenced phase should join the
        # transcription thread BEFORE calling history_db.close().
        controller._do_cleanup()

        # The transcription thread must have completed (the join in
        # _teardown_recorder waited for it).
        assert write_complete_event.is_set(), (
            "F1 (OI-4): the transcription thread's write must have "
            "completed before _do_cleanup returned — the join in "
            "_teardown_recorder should have waited for it"
        )
        # history_db.close must have been called (it's in the sequenced
        # phase, after the join).
        assert "close_called" in timestamps, (
            "F1 (OI-4): history_db.close() must have been called by _do_cleanup (it's in the sequenced phase)"
        )
        # The KEY assertion: "write_complete" < "close_called". The
        # thread's write completed BEFORE close() was called — the race
        # is closed.
        assert timestamps["write_complete"] < timestamps["close_called"], (
            f"F1 (OI-4): transcription thread write_complete (at "
            f"{timestamps['write_complete']:.4f}) must be BEFORE "
            f"history_db.close() (at {timestamps['close_called']:.4f}) "
            f"— the join in _teardown_recorder must complete BEFORE "
            f"_teardown_history_db calls close(). Pre-fix (parallel "
            f"batch), close() would fire ~99ms before the write."
        )
        # Clean up the thread (defensive — it should already be done).
        transcription_thread.join(timeout=1.0)
        assert not transcription_thread.is_alive(), "Test cleanup failure: simulated transcription thread did not exit"

    def test_asr_models_unload_runs_after_transcription_thread_join(self, controller, fake_app):
        """The ASR model unload (``_teardown_asr_models``) must run
        AFTER the transcription thread has finished — unloading the
        model under a mid-inference thread risks a segfault or
        undefined torch state.

        ``_teardown_asr_models`` stays in the parallel batch (it
        benefits from parallel speedup for the CUDA teardown). The
        sequenced phase (which includes ``_teardown_recorder``) runs
        BEFORE the parallel batch, so the transcription thread is
        already joined (or has finished) by the time
        ``_teardown_asr_models`` fires.

        We verify by recording timestamps: the transcription thread's
        "write complete" timestamp must be EARLIER than the
        ``registry.unload()`` call timestamp. (We use "write complete"
        rather than "join complete" because the join is only called if
        the thread is still alive when ``_teardown_recorder`` runs —
        if the thread finished first, the join is a no-op. The KEY
        invariant is that the thread's write completed BEFORE the
        unload, regardless of whether a join was needed.)"""
        # Spawn a real transcription thread.
        timestamps: dict[str, float] = {}

        def _simulate_transcription():
            # Sleep 200ms — long enough that the thread is still alive
            # when _teardown_recorder runs (the sequenced phase has
            # some import overhead from the OI-36 delegate refactor),
            # short enough to keep the test fast.
            time.sleep(0.2)
            timestamps["write_complete"] = time.monotonic()

        transcription_thread = threading.Thread(
            target=_simulate_transcription,
            name="simulated-transcription-asr",
            daemon=True,
        )
        fake_app.recording._transcription_thread = transcription_thread

        # Spy on the ASR registry's unload() to record the call timestamp.
        original_unload = fake_app.models.registry.unload

        def _spy_unload(*args, **kwargs):
            timestamps["unload_called"] = time.monotonic()
            return original_unload(*args, **kwargs)

        fake_app.models.registry.unload = _spy_unload  # type: ignore[assignment]

        transcription_thread.start()

        controller._do_cleanup()

        # The transcription thread's write must have completed.
        assert "write_complete" in timestamps, (
            "F1 (OI-4): the transcription thread's write must have "
            "completed before _do_cleanup returned — the join in "
            "_teardown_recorder should have waited for it (or the "
            "thread finished before the join, in which case the write "
            "still completed before the parallel batch ran)"
        )
        # registry.unload must have been called (it's in the parallel batch).
        assert "unload_called" in timestamps, (
            "F1 (OI-4): registry.unload() must have been called by _teardown_asr_models (it's in the parallel batch)"
        )
        # The KEY assertion: "write_complete" < "unload_called". The
        # thread's write completed BEFORE the ASR model was unloaded —
        # no race between the thread's inference and the unload.
        assert timestamps["write_complete"] < timestamps["unload_called"], (
            f"F1 (OI-4): transcription thread write_complete (at "
            f"{timestamps['write_complete']:.4f}) must be BEFORE "
            f"registry.unload() (at {timestamps['unload_called']:.4f}) "
            f"— the sequenced phase (which joins the transcription "
            f"thread) must complete BEFORE the parallel batch (which "
            f"calls registry.unload()). Pre-fix (both in the same "
            f"parallel wave), unload() could fire while the thread was "
            f"still mid-inference."
        )
        # Clean up.
        transcription_thread.join(timeout=1.0)

    def test_sequenced_phase_runs_before_parallel_batch(self, controller, fake_app):
        """The sequenced critical teardowns (timers/recording, recorder,
        history_db, crash_recovery) must run BEFORE the parallel batch
        (asr_models, hotkeys, electron, etc.).

        We verify by recording the call order of representative helpers
        from each phase. The sequenced helpers must ALL complete before
        ANY parallel-batch helper starts."""
        call_order: list[str] = []
        # Sequenced-phase helpers.
        original_recorder = controller._teardown_recorder

        def _spy_recorder():
            call_order.append("sequenced.teardown_recorder")
            original_recorder()

        controller._teardown_recorder = _spy_recorder  # type: ignore[assignment]

        original_history = controller._teardown_history_db

        def _spy_history():
            call_order.append("sequenced.teardown_history_db")
            original_history()

        controller._teardown_history_db = _spy_history  # type: ignore[assignment]

        # Parallel-batch helpers.
        original_asr = controller._teardown_asr_models

        def _spy_asr():
            call_order.append("parallel.teardown_asr_models")
            original_asr()

        controller._teardown_asr_models = _spy_asr  # type: ignore[assignment]

        original_hotkeys = controller._teardown_hotkeys

        def _spy_hotkeys():
            call_order.append("parallel.teardown_hotkeys")
            original_hotkeys()

        controller._teardown_hotkeys = _spy_hotkeys  # type: ignore[assignment]

        controller._do_cleanup()

        # All four spies must have been called.
        assert "sequenced.teardown_recorder" in call_order, (
            "F1 (OI-4): _teardown_recorder must be called (sequenced phase)"
        )
        assert "sequenced.teardown_history_db" in call_order, (
            "F1 (OI-4): _teardown_history_db must be called (sequenced phase)"
        )
        assert "parallel.teardown_asr_models" in call_order, (
            "F1 (OI-4): _teardown_asr_models must be called (parallel batch)"
        )
        assert "parallel.teardown_hotkeys" in call_order, "F1 (OI-4): _teardown_hotkeys must be called (parallel batch)"
        # The KEY assertion: ALL sequenced helpers must complete before
        # ANY parallel-batch helper starts.
        last_sequenced_idx = max(
            call_order.index("sequenced.teardown_recorder"),
            call_order.index("sequenced.teardown_history_db"),
        )
        first_parallel_idx = min(
            call_order.index("parallel.teardown_asr_models"),
            call_order.index("parallel.teardown_hotkeys"),
        )
        assert last_sequenced_idx < first_parallel_idx, (
            f"F1 (OI-4): all sequenced-phase helpers must complete BEFORE "
            f"any parallel-batch helper starts; got order: {call_order}. "
            f"Last sequenced at index {last_sequenced_idx}, first parallel "
            f"at index {first_parallel_idx}."
        )


# ── F2: _do_fast_cleanup unconditional flush ──────────────────────────


class TestFastCleanupUnconditionalFlush:
    """F2 (OI-5): ``_do_fast_cleanup`` must call ``crash_recovery.flush``
    and ``history_db.flush`` UNCONDITIONALLY — even when
    ``_cleanup_done == True`` on entry.

    Pre-fix, the ``if not already_done:`` gate skipped the flushes when
    ``_cleanup_done`` was already True. This created a false positive
    under quit-during-logoff: the slow ``_do_cleanup`` had set the flag
    at its start but not yet reached the parallel batch when the fast
    path fired; the fast path's flushes were skipped, and
    ``os._exit(0)`` killed the slow path mid-flight — both paths skipped
    the critical writes. The fix removes the gate so the flushes run on
    every invocation (the writes are idempotent; running them twice is
    safe)."""

    def test_fast_cleanup_calls_crash_recovery_flush_when_cleanup_done_true(self, controller, fake_app):
        """When ``_cleanup_done == True`` on entry, ``_do_fast_cleanup``
        must STILL call ``crash_recovery.flush(timeout=1.0)``."""
        fake_app._cleanup_done = True
        fake_app._crash_recovery = MagicMock()
        # ``_do_fast_cleanup`` ends with os._exit(0) — the autouse
        # _stub_os_exit fixture stubs it so the test runner doesn't exit.
        controller._do_fast_cleanup()
        fake_app._crash_recovery.flush.assert_called_once_with(timeout=1.0)

    def test_fast_cleanup_calls_history_db_flush_when_cleanup_done_true(self, controller, fake_app):
        """When ``_cleanup_done == True`` on entry, ``_do_fast_cleanup``
        must STILL call ``history_db.flush`` (via ``_run_with_timeout``
        with a 1s timeout)."""
        fake_app._cleanup_done = True
        fake_app.history_db = MagicMock()
        controller._do_fast_cleanup()
        # history_db.flush is wrapped in _run_with_timeout(timeout=1.0).
        # The mock records the call regardless of the wrapper.
        fake_app.history_db.flush.assert_called_once()

    def test_fast_cleanup_calls_both_flushes_when_cleanup_done_true(self, controller, fake_app):
        """When ``_cleanup_done == True`` on entry, ``_do_fast_cleanup``
        must call BOTH ``crash_recovery.flush`` AND ``history_db.flush``
        — the gate is removed for ALL critical flushes, not just one."""
        fake_app._cleanup_done = True
        fake_app._crash_recovery = MagicMock()
        fake_app.history_db = MagicMock()
        controller._do_fast_cleanup()
        fake_app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        fake_app.history_db.flush.assert_called_once()

    def test_fast_cleanup_calls_flushes_on_every_invocation(self, controller, fake_app, _stub_os_exit):
        """Two sequential ``_do_fast_cleanup`` invocations: BOTH must
        call ``crash_recovery.flush`` (the writes are idempotent —
        running them twice is safe).

        Pre-fix, the second invocation short-circuited at the
        ``if not already_done:`` gate. The fix removes the gate so
        every invocation runs the flushes."""
        # First invocation: crash_recovery is a fresh mock.
        first_crash = MagicMock()
        fake_app._crash_recovery = first_crash
        controller._do_fast_cleanup()
        first_crash.flush.assert_called_once_with(timeout=1.0)

        # Second invocation: replace with a fresh mock to verify the
        # flush is called AGAIN (not skipped due to _cleanup_done).
        second_crash = MagicMock()
        fake_app._crash_recovery = second_crash
        controller._do_fast_cleanup()
        second_crash.flush.assert_called_once_with(timeout=1.0)

        # os._exit(0) must have been called on BOTH invocations (the
        # Win32 callback must not return True without exiting).
        assert _stub_os_exit == [0, 0], (
            f"F2 (OI-5): both _do_fast_cleanup invocations must call os._exit(0); got {_stub_os_exit}"
        )

    def test_fast_cleanup_sets_cleanup_done_even_when_already_true(self, controller, fake_app):
        """``_do_fast_cleanup`` must set ``_cleanup_done = True`` (so a
        subsequent ``_do_cleanup`` call short-circuits) — even when the
        flag was already True on entry. The flag SET is unconditional;
        only the flush GATE was removed."""
        fake_app._cleanup_done = True
        fake_app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        # The flag must still be True (it was set unconditionally).
        assert fake_app._cleanup_done is True, (
            "F2 (OI-5): _do_fast_cleanup must set _cleanup_done = True "
            "(so a subsequent _do_cleanup call short-circuits). The flag "
            "SET is unconditional; only the flush GATE was removed."
        )

    def test_fast_cleanup_no_gate_around_flushes_in_source(self):
        """Source-inspection: the ``if not already_done:`` gate must
        NOT appear as a code statement in ``_do_fast_cleanup``. The
        critical flushes must run unconditionally.

        We look for the pattern as an indented code statement (line
        starts with whitespace + ``if not already_done:``). The
        docstring MENTIONS the phrase as part of the rationale, but
        that's inline text inside a triple-quoted string, not an
        indented code statement."""
        import re

        shutdown_controller_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "voice_typer",
            "server",
            "shutdown_controller.py",
        )
        with open(shutdown_controller_path, encoding="utf-8") as f:
            src = f.read()
        idx = src.find("def _do_fast_cleanup(self) -> None:")
        assert idx > -1, "_do_fast_cleanup method must exist"
        next_def = src.find("\n    def ", idx + 1)
        body = src[idx:next_def]
        # The ``if not already_done:`` CODE STATEMENT must NOT exist.
        # Match lines that start with whitespace + ``if not already_done:``
        # (the actual code statement). The docstring occurrence is inline
        # text without leading whitespace before ``if``.
        code_statement_pattern = re.compile(
            r"^[ \t]+if not already_done:[ \t]*$",
            re.MULTILINE,
        )
        matches = code_statement_pattern.findall(body)
        assert not matches, (
            "F2 (OI-5): _do_fast_cleanup must NOT use `if not already_done:` "
            "as a code statement — the critical flushes must run "
            "unconditionally (running twice is safe; the previous gate "
            "caused quit-during-logoff to skip the flushes when "
            "_do_cleanup had already set _cleanup_done=True mid-flight)"
        )


# ── Helper ────────────────────────────────────────────────────────────


def contextlib_suppress(*exceptions):
    """``contextlib.suppress`` — imported lazily so the module-level
    imports stay minimal (mirrors the pattern in test_shutdown_parallel.py)."""
    import contextlib

    return contextlib.suppress(*exceptions)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
