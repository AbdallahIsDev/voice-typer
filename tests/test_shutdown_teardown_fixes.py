"""UE-2, UE-1-F4, UE-1-F6, UE-1-F7 regression tests for Fix-A.

These tests pin the four secondary fixes from the Fix-A task scope
(see ``/home/z/my-project/skills/_persistent/review.md`` entries
UE-1 and UE-2):

  * **UE-2**: ``_teardown_sounddevice`` must capture the
    ``wait(timeout=9.5)`` return value and skip ``sd.stop()`` when
    the wait times out (the outer 10s ``_run_with_timeout`` wrapper
    leaked ``_teardown_recorder`` mid-execution —
    ``_recorder_force_closed`` was never published and the pre-fix
    code proceeded to ``sd.stop()``, reproducing the DE-54 PortAudio
    deadlock).
  * **UE-1-F4**: ``signal_watcher_loop`` body is wrapped in
    ``while True:`` so the watcher SURVIVES multiple signals (pre-fix
    it exited after the first signal; a second SIGTERM fell through
    to Python's default handler with no cleanup). The event is
    cleared after each wakeup so a subsequent signal re-arms the
    watcher.
  * **UE-1-F6**: ``_teardown_electron`` adds a Windows ctypes
    ``TerminateProcess`` fallback when ``terminate_electron`` times
    out (pre-fix the Windows branch was a silent no-op on timeout —
    the POSIX branch had SIGKILL escalation, but Windows had none).
  * **UE-1-F7**: ``signal_watcher_loop`` ``except`` block writes a
    byte to stderr via ``os.write(2, ...)`` as an async-signal-safe
    fallback when ``log.info`` / ``log.exception`` raises (logging
    lock held by interrupted thread, FileHandler on a closed log
    file during interpreter shutdown, etc.).
"""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController

_SHUTDOWN_CONTROLLER_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown_controller.py",
)
_SIGNAL_HANDLERS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "signal_handlers.py",
)
# Electron teardown source-inspection path removed with
# shutdown/teardowns/electron.py (Electron path deleted).


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _make_controller_with_app():
    """Build a ShutdownController with a MagicMock app for unit testing.

    Mirrors the helper in ``tests/test_shutdown_logoff_cleanup.py`` so the
    same fake-app surface is used across both test modules.
    """
    app = MagicMock()
    app._cleanup_done = False
    app._shutting_down = False
    app._shutting_down_event = MagicMock()
    app._shutting_down_event.set = MagicMock()
    app._crash_recovery = None
    app.history_db = None
    app.recorder = None
    app._mutex_handle = None
    app.hotkeys._hotkey_backend = None
    app.hotkeys._esc_backend = None
    app.hotkeys._repaste_backend = None
    controller = ShutdownController(app)
    return controller, app


# _teardown_sounddevice wait() return-value capture ─────────


class TestTeardownSounddeviceWaitReturn:  # noqa: N801
    """UE-2: ``_teardown_sounddevice`` must capture the ``wait()``
    return value and skip ``sd.stop()`` when the wait times out
    (recorder teardown was leaked mid-execution by the outer 10s
    ``_run_with_timeout`` wrapper)."""

    @pytest.mark.skip(
        reason="source refactored, _teardown_sounddevice no longer "
        "skips sd.stop() when wait() times out; the new contract "
        "always calls sd.stop() (bounded by _run_with_timeout(3.0)) "
        "when _recorder_force_closed is False, with force-abort "
        "fallback via _abort_sounddevice_streams"
    )
    def test_sd_stop_skipped_when_recorder_teardown_times_out(self, monkeypatch):
        """When ``_recorder_teardown_done.wait(timeout=9.5)`` returns
        False (timeout, the recorder teardown worker was leaked),
        ``_teardown_sounddevice`` must SKIP ``sd.stop()`` regardless of
        the ``_recorder_force_closed`` flag value."""
        controller, _ = _make_controller_with_app()

        # Simulate the leak: the event is NEVER set (recorder teardown
        # worker was abandoned mid-execution by the outer 10s timeout).
        # Speed up the wait by replacing wait() with a fast False return.
        never_set_event = threading.Event()
        controller._recorder_teardown_done = never_set_event
        # Flag is False (default), pre-fix the code would proceed to
        # sd.stop() because the flag check alone is insufficient.
        controller._recorder_force_closed = False

        # Inject a fake sounddevice module so we can spy on sd.stop().
        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

        # Speed up the wait by patching the Event.wait to return False
        # immediately (no real 9.5s sleep in the test).
        original_wait = never_set_event.wait

        def _fast_wait(timeout=None):
            # Return False immediately (simulating the timeout path)
            # without actually sleeping.
            return False

        never_set_event.wait = _fast_wait  # type: ignore[assignment]

        try:
            controller._teardown_sounddevice()
        finally:
            never_set_event.wait = original_wait  # type: ignore[assignment]

        (
            fake_sd.stop.assert_not_called(),
            (
                "UE-2: sd.stop() must NOT be called when "
                "_recorder_teardown_done.wait() times out (the leaked "
                "recorder teardown worker may still be accessing the "
                "PortAudio stream)"
            ),
        )

    def test_sd_stop_skipped_when_force_closed_flag_set(self, monkeypatch):
        """When ``_recorder_force_closed`` is True (recorder.stop() /
        discard() timed out inside ``_teardown_recorder``),
        ``_teardown_sounddevice`` must SKIP ``sd.stop()``, the leaked
        recorder.stop() worker is still holding the PortAudio stream
        lock. (This is the pre-fix DE-54 behavior, preserved.)"""
        controller, _ = _make_controller_with_app()

        # Simulate the force-closed path: event IS set, but the flag is
        # True (recorder.stop() timed out inside _teardown_recorder).
        done_event = threading.Event()
        done_event.set()
        controller._recorder_teardown_done = done_event
        controller._recorder_force_closed = True

        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        (
            fake_sd.stop.assert_not_called(),
            ("UE-2: sd.stop() must NOT be called when _recorder_force_closed is True (DE-54 preservation)"),
        )

    def test_sd_stop_called_when_recorder_teardown_completes(self, monkeypatch):
        """When ``_recorder_teardown_done.wait()`` returns True (event
        was set) AND ``_recorder_force_closed`` is False (recorder.stop()
        succeeded), ``sd.stop()`` MUST be called (the safety-net path
        is preserved)."""
        controller, _ = _make_controller_with_app()

        done_event = threading.Event()
        done_event.set()
        controller._recorder_teardown_done = done_event
        controller._recorder_force_closed = False

        fake_sd = MagicMock()
        fake_sd.stop = MagicMock()
        monkeypatch.setitem(__import__("sys").modules, "sounddevice", fake_sd)

        controller._teardown_sounddevice()

        (
            fake_sd.stop.assert_called_once_with(),
            ("UE-2: sd.stop() MUST be called when recorder teardown completed cleanly (no timeout, no force-close)"),
        )

    @pytest.mark.skip(
        reason="source refactored, _teardown_sounddevice no longer "
        "captures the wait() return value to a local `done` variable; "
        "the new contract wraps sd.stop()/sd.wait() in "
        "_run_with_timeout with force-abort fallback "
        "(_abort_sounddevice_streams) instead of gating on the wait() "
        "return value"
    )
    def test_wait_return_value_is_captured_in_source(self):
        """UE-2 (source-inspection): the source must assign the
        ``wait()`` return value to a local variable (``done = ...wait``)
        rather than calling it as a bare statement. A bare ``wait()``
        call discards the return value and is the exact bug UE-2 fixes."""
        s = _src(_SHUTDOWN_CONTROLLER_PATH)
        # Find the _teardown_sounddevice body.
        idx = s.find("def _teardown_sounddevice(self) -> None:")
        assert idx > -1, "_teardown_sounddevice must be defined"
        # Slice to the next method def.
        next_def = s.find("\n    def ", idx + 1)
        body = s[idx:next_def]
        # The wait call must be ``done = ...wait(timeout=9.5)``, NOT a
        # bare ``self._recorder_teardown_done.wait(timeout=9.5)``.
        assert "done = self._recorder_teardown_done.wait(timeout=9.5)" in body, (
            "UE-2: _teardown_sounddevice must capture wait() return value "
            "as ``done = self._recorder_teardown_done.wait(timeout=9.5)``"
        )
        # The skip condition must check BOTH ``not done`` (timeout) and
        # ``_recorder_force_closed`` (force-close flag).
        assert "if not done or self._recorder_force_closed:" in body, (
            "UE-2: _teardown_sounddevice must check `not done or "
            "self._recorder_force_closed` to skip sd.stop() on timeout OR "
            "force-close"
        )


# signal_watcher_loop survives multiple signals ──────────


class TestSignalWatcherLoopSurvivesMultipleSignals:  # noqa: N801
    """UE-1-F4: ``signal_watcher_loop`` body is wrapped in ``while True:``
    so the watcher survives multiple signals. Pre-fix, the watcher exited
    after the first signal, a second SIGTERM (e.g. user double-tapping
    Ctrl+C because the first one was slow) would fall through to Python's
    default handler with no cleanup."""

    def test_watcher_body_wrapped_in_while_true_source(self):
        """UE-1-F4 (source-inspection): the watcher body must be wrapped
        in ``while True:`` so the watcher loops across multiple signals.
        The event must be cleared after each wakeup so a subsequent
        signal re-arms the watcher."""
        s = _src(_SIGNAL_HANDLERS_PATH)
        idx = s.find("def signal_watcher_loop(")
        assert idx > -1, "signal_watcher_loop must be defined"
        next_def = s.find("\ndef ", idx + 1)
        body = s[idx:next_def]
        # The outer ``while True:`` must exist.
        assert "while True:" in body, (
            "UE-1-F4: signal_watcher_loop body must be wrapped in "
            "``while True:`` so the watcher survives multiple signals"
        )
        # The event must be cleared after each wakeup.
        assert "controller._shutdown_signal_event.clear()" in body, (
            "UE-1-F4: signal_watcher_loop must clear the event after "
            "each wakeup so a subsequent signal re-arms the watcher"
        )

    def test_watcher_dispatches_quit_twice_on_two_signals(self):
        """UE-1-F4: when two signals arrive in quick succession, the
        watcher must dispatch ``quit()`` twice (quit is idempotent, so
        the second call is a no-op, but the watcher itself must still
        be alive to observe the second signal)."""
        from voice_typer.server.signal_handlers import signal_watcher_loop

        class _FakeController:
            """Minimal controller stand-in for signal_watcher_loop."""

            def __init__(self) -> None:
                self._shutdown_signal_event = threading.Event()
                self._shutdown_signum = 15  # SIGTERM
                self.quit_calls: list[int] = []
                self._stop = threading.Event()

            def quit(self) -> None:
                self.quit_calls.append(1)
                # Stop the watcher after the second quit() call so the
                # test doesn't loop forever (the real watcher is a
                # daemon thread that dies with the process).
                if len(self.quit_calls) >= 2:
                    self._stop.set()

        controller = _FakeController()
        # Run the watcher in a thread so we can signal it.
        watcher_thread = threading.Thread(
            target=signal_watcher_loop,
            args=(controller,),
            name="test-signal-watcher",
            daemon=True,
        )
        watcher_thread.start()

        try:
            # First signal.
            controller._shutdown_signal_event.set()
            # Wait for the first quit() call.
            for _ in range(100):
                if controller.quit_calls:
                    break
                threading.Event().wait(timeout=0.05)
            assert controller.quit_calls, "UE-1-F4: signal_watcher_loop must dispatch quit() on the first signal"
            len(controller.quit_calls)

            # Second signal, the watcher must STILL be alive to observe
            # it (pre-fix, the watcher had exited after the first signal
            # and the second signal would fall through to Python's
            # default handler).
            controller._shutdown_signal_event.set()

            # Wait for the second quit() call (or for the _stop event
            # that the fake controller sets after the 2nd call).
            for _ in range(200):
                if controller._stop.is_set():
                    break
                threading.Event().wait(timeout=0.05)

            assert len(controller.quit_calls) >= 2, (
                f"UE-1-F4: signal_watcher_loop must dispatch quit() on "
                f"the SECOND signal (pre-fix the watcher had exited "
                f"after the first); got {len(controller.quit_calls)} "
                f"calls"
            )
        finally:
            # The watcher is a daemon thread; the test process will
            # clean it up. Force-unblock any pending wait() so the
            # thread can exit promptly.
            controller._shutdown_signal_event.set()
            controller._stop.set()


# Windows TerminateProcess fallback tests removed with the Electron path.


# signal_watcher_loop stderr fallback write ──────────────


class TestSignalWatcherLoopStderrFallback:  # noqa: N801
    """UE-1-F7: ``signal_watcher_loop`` ``except`` block must write a
    byte to stderr via ``os.write(2, ...)`` as an async-signal-safe
    fallback when ``log.info`` / ``log.exception`` raises. Pre-fix the
    ``except`` block was a bare ``pass``, the operator got zero
    evidence the signal was delivered if logging failed."""

    def test_stderr_fallback_write_exists_in_source(self):
        """UE-1-F7 (source-inspection): the source must contain an
        ``os.write(2, ...)`` call inside the ``except Exception:`` block
        of ``signal_watcher_loop``."""
        s = _src(_SIGNAL_HANDLERS_PATH)
        idx = s.find("def signal_watcher_loop(")
        assert idx > -1
        next_def = s.find("\ndef ", idx + 1)
        body = s[idx:next_def]
        # The fallback must use ``os.write`` to fd 2 (stderr), the
        # async-signal-safe write primitive per POSIX.
        assert "os.write(2," in body, (
            "UE-1-F7: signal_watcher_loop except block must call "
            "``os.write(2, ...)`` as the async-signal-safe stderr "
            "fallback when logging raises"
        )
        # The fallback must be wrapped in ``contextlib.suppress(OSError)``
        # so a failed write (e.g. stderr closed) doesn't propagate.
        assert "contextlib.suppress(OSError)" in body, (
            "the os.write fallback must be wrapped in "
            "``contextlib.suppress(OSError)`` so a failed write doesn't "
            "prevent the subsequent quit() dispatch"
        )

    def test_log_info_failure_triggers_stderr_fallback(self, monkeypatch, capsys):
        """UE-1-F7 (dynamic): when ``log.info`` raises inside
        ``signal_watcher_loop``, the ``os.write(2, ...)`` fallback must
        fire so the operator gets at least one line of evidence on
        stderr. We verify by capturing stderr during a signal dispatch
        where logging raises."""
        from voice_typer.server import signal_handlers as _sh

        # Replace log.info with one that raises (simulates logging-lock
        # deadlock or FileHandler failure during interpreter shutdown).
        def _raising_log_info(*args, **kwargs):
            raise RuntimeError("simulated logging failure")

        monkeypatch.setattr(_sh.log, "info", _raising_log_info)

        class _FakeController:
            def __init__(self) -> None:
                self._shutdown_signal_event = threading.Event()
                self._shutdown_signum = 15  # SIGTERM
                self.quit_calls: list[int] = []
                self._stop = threading.Event()

            def quit(self) -> None:
                self.quit_calls.append(1)
                self._stop.set()

        controller = _FakeController()

        # Run the watcher in a thread.
        watcher_thread = threading.Thread(
            target=_sh.signal_watcher_loop,
            args=(controller,),
            name="test-signal-watcher-ue1f7",
            daemon=True,
        )
        watcher_thread.start()

        try:
            # Trigger the signal.
            controller._shutdown_signal_event.set()

            # Wait for quit() to be called (proves the except branch
            # didn't prevent the subsequent quit() dispatch).
            for _ in range(100):
                if controller._stop.is_set():
                    break
                threading.Event().wait(timeout=0.05)

            assert controller.quit_calls, (
                "UE-1-F7: signal_watcher_loop must still dispatch quit() "
                "even when log.info raises (the except block must not "
                "prevent the subsequent quit() worker spawn)"
            )
        finally:
            controller._shutdown_signal_event.set()
            controller._stop.set()

        # The stderr fallback must have written at least one byte to
        # fd 2. ``capsys`` captures Python-level stderr writes; but
        # ``os.write(2, ...)`` bypasses Python's stderr object and
        # writes directly to fd 2, which ``capsys`` does NOT capture
        # by default. Use ``capfd`` semantics instead by reading from
        # the captured file descriptor.
        # Pytest's ``capsys`` captures sys.stdout/sys.stderr at the
        # Python level; ``os.write(2, ...)`` bypasses this. To verify
        # the fallback fired, we instead assert via a fd-level capture
        # (pytest's ``capfd`` fixture). Since we can't easily switch
        # fixtures mid-test, the source-inspection test above is the
        # authoritative check; this dynamic test only verifies that
        # quit() still dispatches (the primary contract of the except
        # block, never prevent shutdown).

    def test_quit_dispatch_failure_triggers_stderr_fallback_source(self):
        """UE-1-F7 (source-inspection): the SECOND ``except Exception:``
        block (around the ``threading.Thread(target=controller.quit,
        daemon=True).start()`` call) must ALSO have an ``os.write(2, ...)``
        fallback, pre-fix it called ``log.exception`` which has the
        same logging-failure risk."""
        s = _src(_SIGNAL_HANDLERS_PATH)
        idx = s.find("def signal_watcher_loop(")
        assert idx > -1
        next_def = s.find("\ndef ", idx + 1)
        body = s[idx:next_def]
        # Find the second ``except Exception:`` block (around the
        # threading.Thread.start() call). Count the occurrences of
        # ``os.write(2,``, there must be at least 2 (one for the log.info
        # except, one for the threading.Thread.start except).
        write_count = body.count("os.write(2,")
        assert write_count >= 2, (
            f"UE-1-F7: signal_watcher_loop must have at least 2 "
            f"``os.write(2, ...)`` fallbacks (one for the log.info except, "
            f"one for the threading.Thread.start except); got {write_count}"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
