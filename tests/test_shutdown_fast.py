"""Windows logoff/shutdown signals AND ends with ``os._exit(0)``."""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.shutdown_controller import ShutdownController

_FAST_CLEANUP_BODY_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "shutdown",
    "cleanup.py",
)
_SIGNAL_HANDLERS_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "voice_typer",
    "server",
    "signal_handlers.py",
)


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def _stub_os_exit(monkeypatch):
    """``_do_fast_cleanup()`` ends with ``os._exit(0)``. Stub it"""
    calls: list[int] = []
    monkeypatch.setattr(
        "voice_typer.server.shutdown_controller.os._exit",
        lambda code=0: calls.append(code),
    )
    yield calls


def _make_controller_with_app():
    """Build a ShutdownController with a MagicMock app for unit testing."""
    app = MagicMock()
    app._cleanup_done = False
    app._shutting_down = False
    app._shutting_down_event = MagicMock()
    app._shutting_down_event.set = MagicMock()
    app._crash_recovery = None
    app.history_db = None
    app.recorder = None
    app._mutex_handle = None
    controller = ShutdownController(app)
    return controller, app


class TestFastCleanupOsExitSource:
    """``_do_fast_cleanup`` source must end with ``os._exit(0)``,"""

    def test_do_fast_cleanup_calls_os_exit_zero(self, _stub_os_exit):
        """``_do_fast_cleanup()`` must call ``os._exit(0)`` exactly once"""
        controller, _ = _make_controller_with_app()
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0], (
            f"_do_fast_cleanup must call os._exit(0) at the end; got os._exit called with {_stub_os_exit}"
        )

    def test_do_fast_cleanup_calls_os_exit_even_when_cleanup_done_already(self, _stub_os_exit):
        """When ``_cleanup_done`` is already True (prior cleanup ran),"""
        controller, app = _make_controller_with_app()
        app._cleanup_done = True
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        # MUST still call os._exit(0).
        assert _stub_os_exit == [0], (
            f"_do_fast_cleanup must call os._exit(0) even when _cleanup_done is already True; got {_stub_os_exit}"
        )

    def test_do_fast_cleanup_idempotent_second_call_still_exits(self, _stub_os_exit):
        """Two sequential ``_do_fast_cleanup`` invocations: the second"""
        controller, app = _make_controller_with_app()
        controller._do_fast_cleanup()
        # Second call: arm a spy on crash_recovery.flush, it MUST be
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_called_once_with(timeout=1.0)
        assert _stub_os_exit == [0, 0], f"both _do_fast_cleanup invocations must call os._exit(0); got {_stub_os_exit}"

    def test_os_exit_runs_after_all_cleanup_steps(self, _stub_os_exit, monkeypatch):
        """All critical cleanup steps must run BEFORE ``os._exit(0)``."""
        controller, app = _make_controller_with_app()
        call_order: list[str] = []

        def _record_crash_flush(*args, **kwargs):
            call_order.append("crash_recovery.flush")

        def _record_history_flush(*args, **kwargs):
            call_order.append("history_db.flush")

        def _record_os_exit(code=0):
            call_order.append("os._exit")

        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = _record_crash_flush
        app.history_db = MagicMock()
        app.history_db.flush.side_effect = _record_history_flush
        monkeypatch.setattr(
            "voice_typer.server.shutdown_controller.os._exit",
            _record_os_exit,
        )

        controller._do_fast_cleanup()

        assert "crash_recovery.flush" in call_order
        assert "history_db.flush" in call_order
        assert "os._exit" in call_order
        crash_idx = call_order.index("crash_recovery.flush")
        history_idx = call_order.index("history_db.flush")
        exit_idx = call_order.index("os._exit")
        assert crash_idx < exit_idx, f"crash_recovery.flush must run BEFORE os._exit; got order: {call_order}"
        assert history_idx < exit_idx, f"history_db.flush must run BEFORE os._exit; got order: {call_order}"

    def test_os_exit_runs_even_when_cleanup_step_raises(self, _stub_os_exit):
        """If a cleanup step raises, ``_do_fast_cleanup`` must"""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = RuntimeError("simulated failure")
        # Must not raise.
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0]


class TestWin32RoutingFastCleanup:
    """``win32_console_handler`` must route"""

    def test_logoff_event_routes_to_fast_cleanup(self, _stub_os_exit):
        """CTRL_LOGOFF_EVENT (5) must invoke ``_do_fast_cleanup``"""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        fast_cleanup_calls: list[int] = []

        def _spy_fast_cleanup():
            fast_cleanup_calls.append(1)

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]
        controller.quit = MagicMock()  # type: ignore[assignment]

        result = win32_console_handler(controller, 5)

        assert result is True, "win32_console_handler must return True for CTRL_LOGOFF_EVENT"
        assert fast_cleanup_calls == [1], "CTRL_LOGOFF_EVENT must invoke _do_fast_cleanup exactly once"
        (
            controller.quit.assert_not_called(),
            (
                "CTRL_LOGOFF_EVENT must NOT invoke controller.quit() "
                "(the slow ~25-85s path would be force-killed by Windows "
                "before completing)"
            ),
        )

    def test_shutdown_event_routes_to_fast_cleanup(self, _stub_os_exit):
        """CTRL_SHUTDOWN_EVENT (6) must invoke ``_do_fast_cleanup``."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        fast_cleanup_calls: list[int] = []

        def _spy_fast_cleanup():
            fast_cleanup_calls.append(1)

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]
        controller.quit = MagicMock()  # type: ignore[assignment]

        result = win32_console_handler(controller, 6)

        assert result is True, "win32_console_handler must return True for CTRL_SHUTDOWN_EVENT"
        assert fast_cleanup_calls == [1], "CTRL_SHUTDOWN_EVENT must invoke _do_fast_cleanup exactly once"
        controller.quit.assert_not_called(), ("CTRL_SHUTDOWN_EVENT must NOT invoke controller.quit()")

    def test_logoff_event_calls_fast_cleanup_synchronously(self, _stub_os_exit):
        """dedicated OS thread and returning True signals \"handled\"."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        caller_threads: list[int] = []

        def _spy_fast_cleanup():
            caller_threads.append(threading.get_ident())

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]

        main_thread_id = threading.get_ident()
        win32_console_handler(controller, 5)

        assert caller_threads, "_do_fast_cleanup must be called synchronously (not on a spawned thread)"
        assert caller_threads[0] == main_thread_id, (
            "_do_fast_cleanup must run on the SAME thread as the win32_console_handler callback (synchronous dispatch)"
        )

    def test_ctrl_c_still_routes_to_quit(self):
        """no OS-imposed deadline, so the slow path is correct."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        controller.quit = MagicMock()  # type: ignore[assignment]
        controller._do_fast_cleanup = MagicMock()  # type: ignore[assignment]

        for ctrl_type in (0, 1):
            controller.quit.reset_mock()
            controller._do_fast_cleanup.reset_mock()
            result = win32_console_handler(controller, ctrl_type)
            assert result is True, f"win32_console_handler must return True for ctrl_type={ctrl_type}"
            controller.quit.assert_called_once_with()
            (
                controller._do_fast_cleanup.assert_not_called(),
                (f"ctrl_type={ctrl_type} must NOT route to _do_fast_cleanup"),
            )


# POSIX SIGTERM intentionally NOT routed to fast cleanup ────


class TestPosixSigtermUsesSlowPath:
    """POSIX SIGTERM must still route to ``controller.quit()``"""

    def test_signal_watcher_loop_invokes_quit_not_fast_cleanup(self):
        """``signal_watcher_loop`` (the POSIX signal watcher thread)"""
        src = _src(_SIGNAL_HANDLERS_PATH)
        # The watcher loop's dispatch line.
        assert "target=controller.quit" in src, (
            "POSIX signal_watcher_loop must dispatch to "
            "controller.quit (slow path). NOT _do_fast_cleanup. "
            "POSIX SIGTERM has no OS-imposed deadline."
        )
        # And it must NOT mention _do_fast_cleanup anywhere in the
        watcher_idx = src.find("def signal_watcher_loop(")
        assert watcher_idx > -1, "signal_watcher_loop must exist"
        # Slice to the next top-level ``def `` or ``__all__``.
        next_def = src.find("\ndef ", watcher_idx + 1)
        if next_def == -1:
            next_def = len(src)
        watcher_body = src[watcher_idx:next_def]
        assert "_do_fast_cleanup" not in watcher_body, (
            "POSIX signal_watcher_loop must NOT reference _do_fast_cleanup (the fast path is Windows-only)"
        )

    def test_install_signal_handlers_does_not_reference_fast_cleanup(self):
        """``install_signal_handlers`` (POSIX SIGINT/SIGTERM/SIGHUP"""
        src = _src(_SIGNAL_HANDLERS_PATH)
        install_idx = src.find("def install_signal_handlers(")
        assert install_idx > -1
        next_def = src.find("\ndef ", install_idx + 1)
        if next_def == -1:
            next_def = len(src)
        install_body = src[install_idx:next_def]
        assert "_do_fast_cleanup" not in install_body, (
            "install_signal_handlers must NOT reference "
            "_do_fast_cleanup (POSIX signals route to quit, not the "
            "fast cleanup path)"
        )

    def test_win32_console_handler_references_fast_cleanup(self):
        """
        ``win32_console_handler`` (the Windows console-control
        Contract under test: Windows logoff/shutdown routes to the fast
        """
        src = _src(_SIGNAL_HANDLERS_PATH)
        handler_idx = src.find("def win32_console_handler(")
        assert handler_idx > -1
        next_def = src.find("\ndef ", handler_idx + 1)
        if next_def == -1:
            next_def = src.find("\n\n__all__", handler_idx + 1)
        if next_def == -1:
            next_def = len(src)
        handler_body = src[handler_idx:next_def]
        assert "_do_fast_cleanup" in handler_body, (
            "win32_console_handler must reference _do_fast_cleanup (Windows logoff/shutdown routes to the fast path)"
        )


# _do_fast_cleanup source-level contract ────────────────────


class TestFastCleanupSource:
    """Source-level contract for ``_do_fast_cleanup``."""

    def test_do_fast_cleanup_ends_with_os_exit_zero(self):
        """The LAST executable statement in ``_do_fast_cleanup`` must"""
        src = _src(_FAST_CLEANUP_BODY_PATH)
        # Find the do_fast_cleanup body.
        idx = src.find("def do_fast_cleanup(controller) -> None:")
        assert idx > -1, "do_fast_cleanup function must exist"
        # Slice to the next ``def `` (end of the function body).
        next_def = src.find("\ndef ", idx + 1)
        body = src[idx:next_def] if next_def > -1 else src[idx:]
        # The last non-comment, non-blank line in the body must be
        code_lines = [line for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
        assert code_lines, "_do_fast_cleanup body must not be empty"
        last_line = code_lines[-1].strip()
        assert last_line == "os._exit(0)", (
            f"_do_fast_cleanup must end with `os._exit(0)` (last executable line); got: {last_line!r}"
        )

    def test_os_exit_is_outside_cleanup_done_guard(self):
        """
        The ``os._exit(0)`` call must fire UNCONDITIONALLY on every
        ``_do_fast_cleanup`` invocation, the Win32 callback must NOT
        """
        import re

        src = _src(_FAST_CLEANUP_BODY_PATH)
        idx = src.find("def do_fast_cleanup(controller) -> None:")
        next_def = src.find("\ndef ", idx + 1)
        body = src[idx:next_def] if next_def > -1 else src[idx:]
        # The ``if not already_done:`` CODE STATEMENT must NOT exist.
        code_statement_pattern = re.compile(
            r"^[ \t]+if not already_done:[ \t]*$",
            re.MULTILINE,
        )
        code_matches = code_statement_pattern.findall(body)
        assert not code_matches, (
            "OI-5: _do_fast_cleanup must NOT use `if not already_done:` "
            "as a code statement to gate the cleanup body, the critical "
            "flushes must run unconditionally (running twice is safe; "
            "the previous gate caused quit-during-logoff to skip the "
            "flushes when _do_cleanup had already set _cleanup_done=True "
            "mid-flight)"
        )
        # ``os._exit(0)`` must appear in the body.
        exit_idx = body.rfind("os._exit(0)")
        assert exit_idx > -1, "os._exit(0) must appear in _do_fast_cleanup"
        # The line containing ``os._exit(0)`` must be at function-body
        exit_line_start = body.rfind("\n", 0, exit_idx) + 1
        exit_line = body[exit_line_start : body.find("\n", exit_idx)]
        leading_spaces = len(exit_line) - len(exit_line.lstrip(" "))
        assert leading_spaces == 4, (
            f"os._exit(0) must be at function-body indentation "
            f"(4 spaces) so it runs unconditionally; got {leading_spaces} "
            f"spaces (line: {exit_line!r})"
        )
        # ``history_db.flush``) must NOT be nested inside an
        crash_flush_idx = body.find("app._crash_recovery.flush")
        assert crash_flush_idx > -1, "OI-5: _do_fast_cleanup must call app._crash_recovery.flush"
        history_flush_idx = body.find("app.history_db.flush")
        assert history_flush_idx > -1, "OI-5: _do_fast_cleanup must call app.history_db.flush"
        lock_idx = body.find("with controller._quit_lock:")
        assert lock_idx > -1, "OI-5: _do_fast_cleanup must acquire _quit_lock to set _cleanup_done"
        assert crash_flush_idx > lock_idx, (
            "OI-5: crash_recovery.flush must run AFTER the _quit_lock block "
            "(unconditionally, not gated by _cleanup_done)"
        )
        assert history_flush_idx > lock_idx, (
            "OI-5: history_db.flush must run AFTER the _quit_lock block (unconditionally, not gated by _cleanup_done)"
        )


# _do_fast_cleanup restores system volume + clears duck marker ─


class TestFastCleanupVolumeRestore:
    """FR-4: ``_do_fast_cleanup`` must restore system volume (if it was"""

    def test_do_fast_cleanup_calls_restore_volume_with_fade_ms_zero(self, _stub_os_exit):
        """``_do_fast_cleanup`` must call ``app._restore_volume(fade_ms=0)``"""
        controller, app = _make_controller_with_app()
        app._restore_volume = MagicMock()
        app._duck_crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._restore_volume.assert_called_once_with(fade_ms=0)

    def test_do_fast_cleanup_calls_duck_crash_recovery_clear(self, _stub_os_exit):
        """``_do_fast_cleanup`` must call ``app._duck_crash_recovery.clear()``"""
        controller, app = _make_controller_with_app()
        app._restore_volume = MagicMock()
        app._duck_crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._duck_crash_recovery.clear.assert_called_once_with()

    def test_do_fast_cleanup_restore_volume_runs_before_os_exit(self, _stub_os_exit, monkeypatch):
        """FR-4: volume restore MUST run BEFORE ``os._exit(0)``, otherwise"""
        controller, app = _make_controller_with_app()
        call_order: list[str] = []

        def _record_restore(fade_ms=0):
            call_order.append("restore_volume")

        def _record_clear():
            call_order.append("duck_crash_recovery.clear")

        def _record_os_exit(code=0):
            call_order.append("os._exit")

        app._restore_volume = MagicMock(side_effect=_record_restore)
        app._duck_crash_recovery = MagicMock()
        app._duck_crash_recovery.clear.side_effect = _record_clear
        monkeypatch.setattr(
            "voice_typer.server.shutdown_controller.os._exit",
            _record_os_exit,
        )

        controller._do_fast_cleanup()

        assert "restore_volume" in call_order
        assert "duck_crash_recovery.clear" in call_order
        assert "os._exit" in call_order
        restore_idx = call_order.index("restore_volume")
        clear_idx = call_order.index("duck_crash_recovery.clear")
        exit_idx = call_order.index("os._exit")
        assert restore_idx < exit_idx, f"FR-4: restore_volume must run BEFORE os._exit; got order: {call_order}"
        assert clear_idx < exit_idx, (
            f"FR-4: duck_crash_recovery.clear must run BEFORE os._exit; got order: {call_order}"
        )

    def test_do_fast_cleanup_never_raises_when_restore_volume_raises(self, _stub_os_exit):
        """FR-4: if ``_restore_volume`` raises, ``_do_fast_cleanup`` must"""
        controller, app = _make_controller_with_app()
        app._restore_volume = MagicMock(side_effect=RuntimeError("simulated"))
        app._duck_crash_recovery = MagicMock()
        # Must not raise.
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0], (
            f"FR-4: os._exit(0) must still fire when restore_volume raises; got {_stub_os_exit}"
        )

    def test_do_fast_cleanup_never_raises_when_duck_crash_recovery_clear_raises(self, _stub_os_exit):
        """FR-4: if ``_duck_crash_recovery.clear()`` raises, ``_do_fast_cleanup``"""
        controller, app = _make_controller_with_app()
        app._restore_volume = MagicMock()
        app._duck_crash_recovery = MagicMock()
        app._duck_crash_recovery.clear.side_effect = RuntimeError("simulated")
        # Must not raise.
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0], (
            f"FR-4: os._exit(0) must still fire when duck_crash_recovery.clear raises; got {_stub_os_exit}"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
