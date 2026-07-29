"""DJ-6 / DJ-8 / DJ-9: shutdown fast-path + abortable per-phase cleanup.

These tests pin the new ``_do_cleanup`` structure introduced by the
DJ-6/DJ-8/DJ-9 fixes:

  * DJ-9: ``_teardown_history_db`` and ``_teardown_crash_recovery`` run
    SEQUENTIALLY (post-drain, BEFORE the parallel batch).
  * DJ-7: ``_teardown_asr_models`` is FIRST in the parallel batch.
  * DJ-8: if the WS pool drain doesn't complete in 5s, the cleanup
    path calls ``os._exit(0)`` (after running the critical fast-path).
  * DJ-6: when ``_critical_only_mode`` is True (or ``_critical_only=True``
    is passed), the parallel batch is reduced to ONLY the fast-tier
    helpers (``_teardown_pid_file`` + ``_teardown_mutex_handle``); the
    slow tier (recorder, hotkeys, sounddevice, level_monitor) is
    skipped.
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


class TestDJ9SequentialHistoryAndCrashRecovery:
    """DJ-9: ``_teardown_history_db`` and ``_teardown_crash_recovery``
    run SEQUENTIALLY (post-drain), NOT in the parallel batch."""

    def test_history_db_and_crash_recovery_not_in_parallel_batch(self) -> None:
        """The parallel batch (non-critical branch) must NOT contain
        ``_teardown_history_db`` or ``_teardown_crash_recovery``."""
        s = _src()
        # The parallel batch is the list literal assigned to
        # ``parallel_items`` in the ``else:`` branch. Find it.
        else_idx = s.find("        else:\n            parallel_items = [")
        assert else_idx > -1, "DJ-9: the non-critical parallel_items block must exist"
        # Slice to the closing ``]`` of that list literal.
        list_start = s.find("[", else_idx)
        list_end = s.find("]\n", list_start)
        parallel_block = s[list_start:list_end]
        assert "teardown_history_db" not in parallel_block, (
            "DJ-9: _teardown_history_db must NOT be in the parallel batch"
        )
        assert "teardown_crash_recovery" not in parallel_block, (
            "DJ-9: _teardown_crash_recovery must NOT be in the parallel batch"
        )

    def test_history_db_and_crash_recovery_run_sequentially_before_parallel(
        self,
    ) -> None:
        """Both helpers must be invoked via ``_run_with_timeout`` in a
        sequential phase BEFORE the ``parallel_items`` block."""
        s = _src()
        # Find the sequential phase markers.
        seq_crash = s.find('"teardown_crash_recovery",\n                self._teardown_crash_recovery,')
        seq_history = s.find('"teardown_history_db",\n                self._teardown_history_db,')
        assert seq_crash > -1, "DJ-9: _teardown_crash_recovery must be invoked sequentially via _run_with_timeout"
        assert seq_history > -1, "DJ-9: _teardown_history_db must be invoked sequentially via _run_with_timeout"
        # The sequential invocations must come BEFORE the parallel
        # batch (``parallel_items = [``).
        parallel_idx = s.find("parallel_items: list[tuple")
        assert parallel_idx > -1
        assert seq_crash < parallel_idx, (
            "DJ-9: _teardown_crash_recovery sequential invocation must be BEFORE the parallel batch"
        )
        assert seq_history < parallel_idx, (
            "DJ-9: _teardown_history_db sequential invocation must be BEFORE the parallel batch"
        )


class TestDJ8OsExitOnStuckWsDrain:
    """DJ-8: if the WS pool drain doesn't complete in 5s, the cleanup
    path calls ``os._exit(0)``."""

    def test_os_exit_called_when_drain_times_out(self) -> None:
        """The ``if join_thread.is_alive():`` block (the drain-timeout
        branch) must call ``os._exit(0)`` (after running the critical
        fast-path)."""
        s = _src()
        # Find the DJ-8 block: ``if join_thread.is_alive():`` followed
        # by ``log.warning(...)`` + ``self._run_critical_fast_path(app)``
        # + ``os._exit(0)``.
        drain_timeout_idx = s.find("if join_thread.is_alive():")
        assert drain_timeout_idx > -1, "DJ-8: the ws-drain timeout branch (if join_thread.is_alive():) must exist"
        # Slice a generous window (the block spans the log.warning +
        # _run_critical_fast_path + os._exit lines).
        block = s[drain_timeout_idx : drain_timeout_idx + 1500]
        assert "DJ-8" in block, "DJ-8: the drain-timeout block must reference DJ-8"
        assert "os._exit(0)" in block, "DJ-8: the drain-timeout branch must call os._exit(0)"
        assert "_run_critical_fast_path" in block, (
            "DJ-8: the drain-timeout branch must run _run_critical_fast_path BEFORE os._exit(0)"
        )


class TestDJ6CriticalOnlyMode:
    """DJ-6: when ``_critical_only_mode`` is True, the parallel batch
    is reduced to ONLY the fast-tier helpers."""

    def test_critical_only_mode_flag_exists(self) -> None:
        """``_critical_only_mode: bool`` must be initialized in
        ``__init__``."""
        s = _src()
        assert "self._critical_only_mode: bool = False" in s, (
            "DJ-6: _critical_only_mode flag must be initialized to False in __init__"
        )

    def test_critical_only_branch_runs_only_fast_tier_helpers(self) -> None:
        """In the ``if critical_only:`` branch, the parallel batch must
        contain ONLY ``_teardown_pid_file`` and ``_teardown_mutex_handle``
        (the fast-tier helpers). The slow tier (recorder, hotkeys,
        sounddevice, level_monitor) must NOT be present."""
        s = _src()
        # Find the ``if critical_only:`` branch's parallel_items.
        critical_idx = s.find("if critical_only:")
        assert critical_idx > -1, "DJ-6: _do_cleanup must have an ``if critical_only:`` branch"
        # Slice to the ``else:`` branch (the non-critical parallel batch).
        else_idx = s.find("        else:\n            parallel_items", critical_idx)
        assert else_idx > -1, "DJ-6: the non-critical ``else:`` branch must follow the ``if critical_only:`` branch"
        critical_block = s[critical_idx:else_idx]
        assert "teardown_pid_file" in critical_block, "DJ-6: critical-only branch must include _teardown_pid_file"
        assert "teardown_mutex_handle" in critical_block, (
            "DJ-6: critical-only branch must include _teardown_mutex_handle"
        )
        # The slow-tier helpers must NOT be in the critical-only branch.
        for slow_helper in [
            "teardown_recorder",
            "teardown_hotkeys",
            "teardown_sounddevice",
            "teardown_level_monitor",
            "teardown_asr_models",
        ]:
            assert slow_helper not in critical_block, (
                f"DJ-6: slow-tier helper {slow_helper} must NOT be in the critical-only branch"
            )

    def test_run_critical_fast_path_method_exists(self) -> None:
        """``_run_critical_fast_path`` must be defined as a method on
        ``ShutdownController``."""
        s = _src()
        assert "def _run_critical_fast_path(self, app" in s, "DJ-6: _run_critical_fast_path(app) method must be defined"

    def test_run_critical_fast_path_invokes_fast_tier_helpers(self) -> None:
        """``_run_critical_fast_path`` must invoke (via
        ``_run_with_timeout``) the fast-tier helpers: crash_recovery,
        history_db, pid_file, mutex_handle, tray.stop."""
        s = _src()
        helper_idx = s.find("def _run_critical_fast_path(self, app")
        assert helper_idx > -1
        # Slice to the next ``def `` (end of the helper body).
        next_def = s.find("\n    def ", helper_idx + 1)
        body = s[helper_idx:next_def]
        for expected in [
            "self._teardown_crash_recovery",
            "self._teardown_history_db",
            "self._teardown_pid_file",
            "self._teardown_mutex_handle",
            "app.tray.stop",
        ]:
            assert expected in body, f"DJ-6: _run_critical_fast_path must invoke {expected}"


# ── Dynamic test: actually invoke _do_cleanup in critical-only mode ──


class _FakeApp:
    """Minimal ``VoiceTyperApp`` look-alike for ``_do_cleanup``."""

    def __init__(self) -> None:
        self.tray = MagicMock()
        self._ipc_server = None
        self._cleanup_done = False
        self._shutting_down = True
        self._shutting_down_event = threading.Event()
        self._shutting_down_event.set()


class TestDJ6CriticalOnlyDynamic:
    """Dynamic test: actually invoke ``_do_cleanup`` in critical-only
    mode and verify the slow-tier helpers are skipped."""

    def test_critical_only_mode_skips_slow_tier(self) -> None:
        """When ``_critical_only_mode = True``, ``_do_cleanup`` must
        NOT call the slow-tier helpers (recorder.stop, hotkeys.stop,
        sounddevice.stop, level_monitor.stop)."""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        ctrl._quit_lock = threading.Lock()
        ctrl._critical_only_mode = True
        ctrl._recorder_teardown_done = threading.Event()
        ctrl._recorder_force_closed = False
        # Spy on the slow-tier helpers — patch them to record calls.
        ctrl._teardown_recorder = MagicMock()
        ctrl._teardown_hotkeys = MagicMock()
        ctrl._teardown_sounddevice = MagicMock()
        ctrl._teardown_level_monitor = MagicMock()
        ctrl._teardown_asr_models = MagicMock()
        # Spy on the fast-tier helpers — patch them to no-op (avoid
        # touching real subsystem state).
        ctrl._teardown_crash_recovery = MagicMock()
        ctrl._teardown_history_db = MagicMock()
        ctrl._teardown_pid_file = MagicMock()
        ctrl._teardown_mutex_handle = MagicMock()
        # Also patch the other middle-tier helpers (they're in the
        # non-critical batch but skipped in critical-only mode).
        ctrl._teardown_timers_and_recording = MagicMock()
        ctrl._teardown_restore_volume = MagicMock()
        ctrl._teardown_waveform_wiring = MagicMock()
        ctrl._teardown_devnull_files = MagicMock()
        ctrl._teardown_electron = MagicMock()
        ctrl._teardown_event_bus = MagicMock()

        # Invoke _do_cleanup in critical-only mode.
        ctrl._do_cleanup()

        # Slow tier MUST NOT have been called.
        ctrl._teardown_recorder.assert_not_called()
        ctrl._teardown_hotkeys.assert_not_called()
        ctrl._teardown_sounddevice.assert_not_called()
        ctrl._teardown_level_monitor.assert_not_called()
        ctrl._teardown_asr_models.assert_not_called()

        # Fast tier MUST have been called.
        ctrl._teardown_crash_recovery.assert_called_once()
        ctrl._teardown_history_db.assert_called_once()
        ctrl._teardown_pid_file.assert_called_once()
        ctrl._teardown_mutex_handle.assert_called_once()

        # Middle tier MUST NOT have been called (skipped in critical-
        # only mode).
        ctrl._teardown_timers_and_recording.assert_not_called()
        ctrl._teardown_restore_volume.assert_not_called()
        ctrl._teardown_waveform_wiring.assert_not_called()
        ctrl._teardown_devnull_files.assert_not_called()
        ctrl._teardown_electron.assert_not_called()
        ctrl._teardown_event_bus.assert_not_called()

        # tray.stop MUST have been called (late bookend always runs).
        fake_app.tray.stop.assert_called_once_with()

    def test_normal_mode_runs_all_tier_helpers(self) -> None:
        """When ``_critical_only_mode = False`` (default), ``_do_cleanup``
        must call ALL tier helpers (fast + middle + slow) — the
        critical-only mode is OPT-IN."""
        from voice_typer.server.shutdown_controller import ShutdownController

        fake_app = _FakeApp()
        ctrl = ShutdownController.__new__(ShutdownController)
        ctrl._app = fake_app
        ctrl._quit_lock = threading.Lock()
        ctrl._critical_only_mode = False
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
        ctrl._teardown_timers_and_recording.assert_called_once()
        ctrl._teardown_recorder.assert_called_once()
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

        # history_db + crash_recovery are now in the SEQUENTIAL phase
        # (DJ-9), so they MUST also have been called.
        ctrl._teardown_crash_recovery.assert_called_once()
        ctrl._teardown_history_db.assert_called_once()

        # tray.stop MUST have been called.
        fake_app.tray.stop.assert_called_once_with()
