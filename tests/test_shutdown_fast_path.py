"""shutdown fast-path + abortable per-phase cleanup.

These tests pin the ``_do_cleanup`` structure introduced by the
fixes. The architecture has evolved since the original
contract was written:

  * ``_teardown_history_db`` and ``_teardown_crash_recovery`` run
    SEQUENTIALLY (post-drain, BEFORE the parallel batch) via the
    ``sequenced_items`` list.
  * ``_teardown_asr_models`` is FIRST in the parallel batch.
  * the WS pool drain has a 5s timeout; if it doesn't complete,
    the cleanup path logs a WARNING and proceeds (does NOT call
    ``os._exit`` — the os._exit path is reserved for the Windows
    logoff/shutdown fast path in ``_do_fast_cleanup``).
  * the Windows logoff/shutdown fast path is implemented as a
    SEPARATE method ``_do_fast_cleanup`` (NOT a ``_critical_only_mode``
    flag on ``_do_cleanup``). The fast path runs critical flushes
    (crash_recovery, history_db, recorder.stop, pid_file, mutex,
    restore_volume) with 1s timeouts and ends with ``os._exit(0)``.
"""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller.py",
)


def _src() -> str:
    with open(_SHUTDOWN_CONTROLLER_PATH, encoding="utf-8") as f:
        return f.read()


# ── Static (source-inspection) contract tests ───────────────────────


class TestSequentialHistoryAndCrashRecovery:
    """``_teardown_history_db`` and ``_teardown_crash_recovery``
    run SEQUENTIALLY (post-drain), NOT in the parallel batch."""

    def test_history_db_and_crash_recovery_not_in_parallel_batch(self) -> None:
        """The parallel batch (``parallel_items`` list) must NOT contain
        ``_teardown_history_db`` or ``_teardown_crash_recovery`` — they
        live in the ``sequenced_items`` list instead."""
        s = _src()
        # Find the ``parallel_items`` list literal.
        parallel_idx = s.find("parallel_items")
        assert parallel_idx > -1, "_do_cleanup must define a parallel_items list"
        # Slice from the opening ``[`` to the matching ``]``.
        list_start = s.find("[", parallel_idx)
        assert list_start > -1
        list_end = s.find("]\n", list_start)
        assert list_end > -1
        parallel_block = s[list_start:list_end]
        assert "teardown_history_db" not in parallel_block, "_teardown_history_db must NOT be in the parallel batch"
        assert "teardown_crash_recovery" not in parallel_block, (
            "_teardown_crash_recovery must NOT be in the parallel batch"
        )

    def test_history_db_and_crash_recovery_run_sequentially_before_parallel(
        self,
    ) -> None:
        """Both helpers must be invoked via the ``sequenced_items`` list
        BEFORE the ``parallel_items`` block."""
        s = _src()
        # The sequenced_items list contains both helpers as tuples.
        seq_history = s.find('"teardown_history_db"', s.find("sequenced_items"))
        assert seq_history > -1, "_teardown_history_db must be in the sequenced_items list"
        seq_crash = s.find('"teardown_crash_recovery"', s.find("sequenced_items"))
        assert seq_crash > -1, "_teardown_crash_recovery must be in the sequenced_items list"
        # The sequenced_items list must come BEFORE the parallel_items
        # list in the source (the sequenced phase runs first).
        parallel_idx = s.find("parallel_items")
        assert parallel_idx > -1
        sequenced_idx = s.find("sequenced_items")
        assert sequenced_idx > -1
        assert sequenced_idx < parallel_idx, (
            "sequenced_items must be defined BEFORE parallel_items (the sequenced phase runs first)"
        )


class TestOsExitOnStuckWsDrain:
    """the WS pool drain has a 5s timeout. The architecture
    evolution moved the ``os._exit(0)`` call to the Windows
    logoff/shutdown fast path (``_do_fast_cleanup``); the normal
    ``_do_cleanup`` path logs a WARNING and proceeds (so a stuck WS
    handler doesn't block the rest of teardown)."""

    def test_ws_drain_timeout_branch_exists(self) -> None:
        """The ``if join_thread.is_alive():`` branch (the drain-timeout
        detector) must exist in ``_do_cleanup`` and log a WARNING.

        NOTE: the module docstring ALSO mentions the branch (as a
        ``code``-formatted comment), so a plain ``s.find`` would anchor
        on the docstring occurrence. We anchor inside the
        ``_drain_ws_dispatch_pool`` method body — the actual code —
        instead.
        """
        s = _src()
        method_idx = s.find("def _drain_ws_dispatch_pool(self, app) -> None:")
        assert method_idx > -1, "_drain_ws_dispatch_pool method must exist"
        next_def = s.find("\n    def ", method_idx + 1)
        body = s[method_idx:next_def]
        # The method docstring ALSO mentions the branch; use the LAST
        # occurrence in the method body — the actual code — so the
        # comment doesn't shadow it.
        drain_timeout_idx = body.rfind("if join_thread.is_alive():")
        assert drain_timeout_idx > -1, (
           "the ws-drain timeout branch (if join_thread.is_alive():) must exist "
           "in _drain_ws_dispatch_pool"
        )
        # Slice a generous window for the block.
        block = body[drain_timeout_idx : drain_timeout_idx + 800]
        assert "log.warning" in block, (
            "the drain-timeout branch must log a WARNING so operators see the degraded-shutdown event"
        )

    def test_do_fast_cleanup_calls_os_exit(self) -> None:
        """``_do_fast_cleanup`` (the Windows logoff/shutdown fast path)
        must end with ``os._exit(0)`` so the Win32 console-control
        callback does not return True to the OS without exiting."""
        s = _src()
        fast_path_idx = s.find("def _do_fast_cleanup(self) -> None:")
        assert fast_path_idx > -1, "_do_fast_cleanup method must be defined (the Windows logoff/shutdown fast path)"
        # Slice to the next ``def `` (end of the method body).
        next_def = s.find("\n    def ", fast_path_idx + 1)
        body = s[fast_path_idx:next_def] if next_def > -1 else s[fast_path_idx:]
        assert "os._exit(0)" in body, (
            "_do_fast_cleanup must call os._exit(0) at the end so the OS force-kill is pre-empted"
        )


class TestDoFastCleanup:
    """the Windows logoff/shutdown fast path is implemented as a
    separate ``_do_fast_cleanup`` method (NOT a ``_critical_only_mode``
    flag on ``_do_cleanup``). The fast path runs critical flushes with
    1s timeouts and ends with ``os._exit(0)``."""

    def test_do_fast_cleanup_method_exists(self) -> None:
        """``_do_fast_cleanup`` must be defined as a method on
        ``ShutdownController``."""
        s = _src()
        assert "def _do_fast_cleanup(self) -> None:" in s, "_do_fast_cleanup method must be defined"

    def test_do_fast_cleanup_invokes_critical_flushes(self) -> None:
        """``_do_fast_cleanup`` must invoke the critical flushes:
        crash_recovery.flush, history_db.flush, recorder.stop,
        _clear_backend_pid_file, mutex release, restore_volume."""
        s = _src()
        helper_idx = s.find("def _do_fast_cleanup(self) -> None:")
        assert helper_idx > -1
        # Slice to the next ``def `` (end of the method body).
        next_def = s.find("\n    def ", helper_idx + 1)
        body = s[helper_idx:next_def] if next_def > -1 else s[helper_idx:]
        # Each critical flush is wrapped in try/except; we assert the
        # call site exists.
        for expected in [
            "crash_recovery",
            "history_db",
            "recorder.stop",
            "_clear_backend_pid_file",
            "_mutex_handle",
            "_restore_volume",
        ]:
            assert expected in body, f"_do_fast_cleanup must touch critical resource: {expected}"


# ── Dynamic test: verify _do_cleanup runs all helpers in normal mode ──


class _FakeApp:
    """Minimal ``VoiceTyperApp`` look-alike for ``_do_cleanup``."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self._ipc_server = None
        self._cleanup_done = False
        self._shutting_down = True
        self._shutting_down_event = threading.Event()
        self._shutting_down_event.set()


class TestNormalModeRunsAllHelpers:
    """Dynamic test: ``_do_cleanup`` (normal mode) runs every helper in
    the sequenced phase + parallel batch + late bookend. There is no
    ``_critical_only_mode`` flag — the Windows logoff/shutdown fast path
    is a separate ``_do_fast_cleanup`` method (tested above)."""

    def test_normal_mode_runs_all_tier_helpers(self) -> None:
        """``_do_cleanup`` must call ALL tier helpers (sequenced + parallel
        + late bookend) in normal mode."""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        ctrl._quit_lock = threading.Lock()
        ctrl._electron_pid_lock = threading.Lock()
        ctrl._recorder_teardown_done = threading.Event()
        ctrl._recorder_force_closed = False
        # Spy on all helpers.
        for name in [
            "_teardown_recorder",
            "_teardown_hotkeys",
            "_teardown_sounddevice",
            "_teardown_level_monitor",
            "_teardown_asr_models",
            "_teardown_crash_recovery",
            "_teardown_history_db",
            "_teardown_pid_file",
            "_teardown_mutex_handle",
            "_teardown_timers_and_recording",
            "_teardown_restore_volume",
            "_teardown_waveform_wiring",
            "_teardown_devnull_files",
            "_teardown_electron",
            "_teardown_event_bus",
        ]:
            setattr(ctrl, name, MagicMock())

        ctrl._do_cleanup()

        # All helpers in the parallel batch MUST have been called.
        ctrl._teardown_asr_models.assert_called_once()
        ctrl._teardown_restore_volume.assert_called_once()
        ctrl._teardown_waveform_wiring.assert_called_once()
        ctrl._teardown_sounddevice.assert_called_once()
        ctrl._teardown_pid_file.assert_called_once()
        ctrl._teardown_mutex_handle.assert_called_once()
        ctrl._teardown_devnull_files.assert_called_once()
        ctrl._teardown_level_monitor.assert_called_once()
        ctrl._teardown_hotkeys.assert_called_once()
        ctrl._teardown_electron.assert_called_once()
        ctrl._teardown_event_bus.assert_called_once()

        # Sequenced phase helpers MUST have been called.
        ctrl._teardown_timers_and_recording.assert_called_once()
        ctrl._teardown_recorder.assert_called_once()
        ctrl._teardown_crash_recovery.assert_called_once()
        ctrl._teardown_history_db.assert_called_once()

        # tray.stop MUST have been called (late bookend).
        fake_app.tray.stop.assert_called_once_with()
