"""Race-condition regression tests for the shutdown teardown sequence."""

from __future__ import annotations

import os
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


@pytest.fixture(autouse=True)
def _stub_os_exit(monkeypatch):
    """Stub ``os._exit`` so tests that invoke ``_do_fast_cleanup``"""
    calls: list[int] = []
    monkeypatch.setattr(
        "voice_typer.server.shutdown_controller.os._exit",
        lambda code=0: calls.append(code),
    )
    yield calls


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
        # ``_do_fast_cleanup`` touches these, give them MagicMock defaults
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
    """Return a ``_FakeApp`` with all dynamic-lookup helpers stubbed."""
    fake_app_module = MagicMock()
    fake_app_module._clear_backend_pid_file = MagicMock()
    fake_app_module._close_devnull_files = MagicMock()
    fake_app_module._register_devnull_file = MagicMock()
    monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: False)
    fake_app_module._config_dir = lambda: "/tmp/voice-typer-test-race-fixes"
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


class TestTranscriptionThreadJoinBeforeDbClose:
    """F1 (OI-4): the transcription thread join (inside"""

    def test_transcription_thread_join_completes_before_history_db_close(self, controller, fake_app):
        """The transcription thread's \"write complete\" timestamp must"""
        # Spawn a real transcription thread that simulates ASR inference.
        write_complete_event = threading.Event()
        timestamps: dict[str, float] = {}
        write_started_event = threading.Event()

        def _simulate_transcription():
            # Simulate ASR inference (100ms, typical Whisper inference
            write_started_event.set()
            time.sleep(0.1)
            timestamps["write_complete"] = time.monotonic()
            # Simulate the fire-and-forget add_transcription() write
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
        transcription_thread.start()
        write_started_event.wait(timeout=1.0)
        assert write_started_event.is_set(), (
            "Test setup failure: simulated transcription thread did not start its inference sleep within 1s"
        )

        controller._do_cleanup()

        assert write_complete_event.is_set(), (
            "F1 (OI-4): the transcription thread's write must have "
            "completed before _do_cleanup returned, the join in "
            "_teardown_recorder should have waited for it"
        )
        assert "close_called" in timestamps, (
            "F1 (OI-4): history_db.close() must have been called by _do_cleanup (it's in the sequenced phase)"
        )
        assert timestamps["write_complete"] <= timestamps["close_called"], (
            f"F1 (OI-4): transcription thread write_complete (at "
            f"{timestamps['write_complete']:.4f}) must be BEFORE "
            f"history_db.close() (at {timestamps['close_called']:.4f}) "
            f"— the join in _teardown_recorder must complete BEFORE "
            f"_teardown_history_db calls close(). Pre-fix (parallel "
            f"batch), close() would fire ~99ms before the write."
        )
        # Clean up the thread (defensive, it should already be done).
        transcription_thread.join(timeout=1.0)
        assert not transcription_thread.is_alive(), "Test cleanup failure: simulated transcription thread did not exit"

    def test_asr_models_unload_runs_after_transcription_thread_join(self, controller, fake_app):
        """The ASR model unload (``_teardown_asr_models``) must run"""
        # Spawn a real transcription thread.
        timestamps: dict[str, float] = {}

        def _simulate_transcription():
            # Sleep 200ms, long enough that the thread is still alive
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
            "completed before _do_cleanup returned, the join in "
            "_teardown_recorder should have waited for it (or the "
            "thread finished before the join, in which case the write "
            "still completed before the parallel batch ran)"
        )
        assert "unload_called" in timestamps, (
            "F1 (OI-4): registry.unload() must have been called by _teardown_asr_models (it's in the parallel batch)"
        )
        assert timestamps["write_complete"] <= timestamps["unload_called"], (
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
        """The sequenced critical teardowns (timers/recording, recorder,"""
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


class TestFastCleanupUnconditionalFlush:
    """F2 (OI-5): ``_do_fast_cleanup`` must call ``crash_recovery.flush``"""

    def test_fast_cleanup_calls_crash_recovery_flush_when_cleanup_done_true(self, controller, fake_app):
        """When ``_cleanup_done == True`` on entry, ``_do_fast_cleanup``"""
        fake_app._cleanup_done = True
        fake_app._crash_recovery = MagicMock()
        # ``_do_fast_cleanup`` ends with os._exit(0), the autouse
        controller._do_fast_cleanup()
        fake_app._crash_recovery.flush.assert_called_once_with(timeout=1.0)

    def test_fast_cleanup_calls_history_db_flush_when_cleanup_done_true(self, controller, fake_app):
        """When ``_cleanup_done == True`` on entry, ``_do_fast_cleanup``"""
        fake_app._cleanup_done = True
        fake_app.history_db = MagicMock()
        controller._do_fast_cleanup()
        fake_app.history_db.flush.assert_called_once()

    def test_fast_cleanup_calls_both_flushes_when_cleanup_done_true(self, controller, fake_app):
        """When ``_cleanup_done == True`` on entry, ``_do_fast_cleanup``"""
        fake_app._cleanup_done = True
        fake_app._crash_recovery = MagicMock()
        fake_app.history_db = MagicMock()
        controller._do_fast_cleanup()
        fake_app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        fake_app.history_db.flush.assert_called_once()

    def test_fast_cleanup_calls_flushes_on_every_invocation(self, controller, fake_app, _stub_os_exit):
        """Two sequential ``_do_fast_cleanup`` invocations: BOTH must"""
        # First invocation: crash_recovery is a fresh mock.
        first_crash = MagicMock()
        fake_app._crash_recovery = first_crash
        controller._do_fast_cleanup()
        first_crash.flush.assert_called_once_with(timeout=1.0)

        second_crash = MagicMock()
        fake_app._crash_recovery = second_crash
        controller._do_fast_cleanup()
        second_crash.flush.assert_called_once_with(timeout=1.0)

        # Win32 callback must not return True without exiting).
        assert _stub_os_exit == [0, 0], (
            f"F2 (OI-5): both _do_fast_cleanup invocations must call os._exit(0); got {_stub_os_exit}"
        )

    def test_fast_cleanup_sets_cleanup_done_even_when_already_true(self, controller, fake_app):
        """flag was already True on entry. The flag SET is unconditional;"""
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
        """Source-inspection: the ``if not already_done:`` gate must"""
        import re

        shutdown_controller_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "voice_typer",
            "server",
            "shutdown_controller",
            "_cleanup.py",
        )
        with open(shutdown_controller_path, encoding="utf-8") as f:
            src = f.read()
        idx = src.find("def _do_fast_cleanup(self) -> None:")
        assert idx > -1, "_do_fast_cleanup method must exist"
        next_def = src.find("\n    def ", idx + 1)
        body = src[idx:next_def]
        # The ``if not already_done:`` CODE STATEMENT must NOT exist.
        code_statement_pattern = re.compile(
            r"^[ \t]+if not already_done:[ \t]*$",
            re.MULTILINE,
        )
        matches = code_statement_pattern.findall(body)
        assert not matches, (
            "F2 (OI-5): _do_fast_cleanup must NOT use `if not already_done:` "
            "as a code statement, the critical flushes must run "
            "unconditionally (running twice is safe; the previous gate "
            "caused quit-during-logoff to skip the flushes when "
            "_do_cleanup had already set _cleanup_done=True mid-flight)"
        )


class TestWin32ConsoleHandlerInstallIdempotent:
    """F3 ``install_win32_console_handler`` must NOT replace"""

    def test_second_call_is_noop(self, controller, fake_app, monkeypatch):
        """
        Two sequential installs: the second call must NOT replace
        ``app._console_handler`` and must NOT call
        """
        # Force the Windows code path. We access the app module via
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
        # Avoid the pythonw.exe short-circuit.
        monkeypatch.setattr(sys, "executable", "/fake/path/python.exe")

        # Mock ctypes.windll (Windows-only on Linux). The install body
        import ctypes

        fake_kernel32 = MagicMock()
        fake_kernel32.SetConsoleCtrlHandler.return_value = True
        fake_windll = MagicMock()
        fake_windll.kernel32 = fake_kernel32
        monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

        # First call: installs the handler. ``_console_handler`` goes
        controller._install_win32_console_handler()
        first_handler = fake_app._console_handler
        assert first_handler is not None, (
            "first call to install_win32_console_handler must set "
            "app._console_handler (the test setup forces Windows + python.exe "
            "so the install path must execute end-to-end)"
        )
        # SetConsoleCtrlHandler must have been called once with the new
        assert fake_kernel32.SetConsoleCtrlHandler.call_count == 1, (
            f"first call must register the handler exactly once; "
            f"got {fake_kernel32.SetConsoleCtrlHandler.call_count} calls"
        )

        # Second call: must be a no-op. ``_console_handler`` MUST NOT
        controller._install_win32_console_handler()
        assert fake_app._console_handler is first_handler, (
            "second call to install_win32_console_handler must NOT "
            "replace app._console_handler, overwriting drops the only "
            "Python reference to the previous CFUNCTYPE wrapper while "
            "Windows still holds the raw function pointer in the console-"
            "control chain → use-after-free → segfault on next console event"
        )
        # SetConsoleCtrlHandler MUST NOT have been called a second time
        assert fake_kernel32.SetConsoleCtrlHandler.call_count == 1, (
            f"second call must not invoke SetConsoleCtrlHandler, "
            f"the Win32 API ADDS to the handler chain (no replace); a "
            f"second registration would leak the first raw pointer when "
            f"its wrapper is GC'd. Got call_count="
            f"{fake_kernel32.SetConsoleCtrlHandler.call_count}"
        )

    def test_guard_fires_even_when_ctypes_raises(self, controller, fake_app, monkeypatch):
        """The idempotency guard must fire BEFORE the ctypes try block."""
        monkeypatch.setattr("voice_typer.server.platform_utils.is_windows", lambda: True)
        monkeypatch.setattr(sys, "executable", "/fake/path/python.exe")

        import ctypes

        fake_kernel32 = MagicMock()
        fake_kernel32.SetConsoleCtrlHandler.return_value = True
        fake_windll = MagicMock()
        fake_windll.kernel32 = fake_kernel32
        monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

        # First call: succeeds.
        controller._install_win32_console_handler()
        first_handler = fake_app._console_handler
        assert first_handler is not None

        # Break ctypes.windll AFTER the first install, the second call
        class _BrokenWindll:
            def __getattr__(self, name):
                raise RuntimeError(f"ctypes.windll.{name} access broken")

        monkeypatch.setattr(ctypes, "windll", _BrokenWindll(), raising=False)

        controller._install_win32_console_handler()
        assert fake_app._console_handler is first_handler, (
            "idempotency guard must fire BEFORE the ctypes try block "
            "so a broken ctypes does not crash the second call or replace "
            "the existing handler"
        )

    def test_idempotency_guard_present_in_source(self):
        """
        Source-inspection: ``install_win32_console_handler`` must
        Pins the guard against accidental removal by a future refactor
        """
        import re

        signal_handlers_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "voice_typer",
            "server",
            "signal_handlers.py",
        )
        with open(signal_handlers_path, encoding="utf-8") as f:
            src = f.read()
        idx = src.find("def install_win32_console_handler(")
        assert idx > -1, "install_win32_console_handler must exist"
        next_def = src.find("\ndef ", idx + 1)
        body = src[idx:next_def]
        guard_pattern = re.compile(
            r'^[ \t]+if getattr\(app, ["\']_console_handler["\'], None\) is not None:',
            re.MULTILINE,
        )
        assert guard_pattern.search(body), (
            "install_win32_console_handler must contain the idempotency "
            "guard `if getattr(app, '_console_handler', None) is not None: "
            "return` as a code statement before constructing a new "
            "CFUNCTYPE wrapper, without it, the previous wrapper is GC'd "
            "while Windows still holds the raw function pointer → "
            "use-after-free → segfault on the next console event"
        )


def contextlib_suppress(*exceptions):
    """``contextlib.suppress``, imported lazily so the module-level"""
    import contextlib

    return contextlib.suppress(*exceptions)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
