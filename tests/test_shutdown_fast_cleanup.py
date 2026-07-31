"""UE-1 regression: ``_do_fast_cleanup`` is the dispatch target for
Windows logoff/shutdown signals AND ends with ``os._exit(0)``.

Context
-------
Windows fires ``CTRL_LOGOFF_EVENT`` (5) / ``CTRL_SHUTDOWN_EVENT`` (6)
via ``SetConsoleCtrlHandler`` when the user logs off or the system is
shutting down. The OS gives the process a hard ~5-second deadline
before force-killing it. The full ``_do_cleanup`` body has a
cumulative worst-case of ~25-85s; on Windows logoff the OS would
force-kill the process mid-cleanup, silently losing unsaved
transcriptions + history writes.

``_do_fast_cleanup`` (XZ-R17-06) is the critical-only cleanup path
(crash_recovery.flush, history_db.flush, recorder.stop,
_clear_backend_pid_file, mutex release) with 1s timeouts each,
targeting <3s total. UE-1 completes the XZ-R17-06 contract:

  1. ``signal_handlers.win32_console_handler`` routes
     CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT (the Win32 analogues of
     WM_QUERYENDSESSION) to ``controller._do_fast_cleanup()`` — NOT
     ``controller.quit()`` (the slow path).
  2. ``_do_fast_cleanup`` ends with ``os._exit(0)`` so the Win32
     callback returns control to the OS via the async-signal-safe
     exit primitive (bypassing atexit handlers — the OS is killing
     us anyway, so orderly atexit cleanup would race the deadline).
  3. ``os._exit(0)`` MUST fire even on a no-op second invocation
     (when ``_cleanup_done`` was already True) so the Win32 callback
     does not return ``True`` to the OS without exiting.

POSIX SIGTERM is intentionally routed to ``controller.quit()`` (the
slow path), NOT to ``_do_fast_cleanup``: POSIX does NOT impose a
5-second OS deadline on SIGTERM (``systemd``'s
``DefaultTimeoutStopSec`` defaults to 90s), so the slow path is
correct there. Only Windows logoff/shutdown has the 5s constraint.

This module pins both contracts via source-inspection and behavioral
tests. Tests that invoke ``_do_fast_cleanup`` directly MUST monkey-
patch ``os._exit`` so the test runner doesn't actually exit (the
autouse ``_stub_os_exit`` fixture below handles this).
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


def _src(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(autouse=True)
def _stub_os_exit(monkeypatch):
    """UE-1: ``_do_fast_cleanup()`` ends with ``os._exit(0)``. Stub it
    so the test runner doesn't actually exit when tests invoke
    ``_do_fast_cleanup()`` directly."""
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


# ── UE-1: source-level contracts on _do_fast_cleanup ────────────────


class TestUE1FastCleanupOsExitSource:
    """UE-1: ``_do_fast_cleanup`` source must end with ``os._exit(0)``,
    and the ``_cleanup_done`` short-circuit must NOT skip it."""

    def test_do_fast_cleanup_calls_os_exit_zero(self, _stub_os_exit):
        """``_do_fast_cleanup()`` must call ``os._exit(0)`` exactly once
        at the end of the cleanup body."""
        controller, _ = _make_controller_with_app()
        controller._do_fast_cleanup()
        assert _stub_os_exit == [0], (
            f"UE-1: _do_fast_cleanup must call os._exit(0) at the end; got os._exit called with {_stub_os_exit}"
        )

    def test_do_fast_cleanup_calls_os_exit_even_when_cleanup_done_already(self, _stub_os_exit):
        """When ``_cleanup_done`` is already True (prior cleanup ran),
        ``_do_fast_cleanup`` still calls ``os._exit(0)`` — we're being
        invoked from the Windows logoff/shutdown callback and must not
        return True (which would let the OS re-evaluate us)."""
        controller, app = _make_controller_with_app()
        app._cleanup_done = True
        # Should NOT call crash_recovery.flush (already cleaned up).
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_not_called()
        # MUST still call os._exit(0).
        assert _stub_os_exit == [0], (
            f"UE-1: _do_fast_cleanup must call os._exit(0) even when _cleanup_done is already True; got {_stub_os_exit}"
        )

    def test_do_fast_cleanup_idempotent_second_call_still_exits(self, _stub_os_exit):
        """Two sequential ``_do_fast_cleanup`` invocations: the second
        short-circuits on ``_cleanup_done`` (no cleanup steps re-run),
        but BOTH invocations call ``os._exit(0)``."""
        controller, app = _make_controller_with_app()
        controller._do_fast_cleanup()
        # Second call: arm a spy on crash_recovery.flush — it must NOT
        # be called (idempotency), but os._exit(0) MUST still fire.
        app._crash_recovery = MagicMock()
        controller._do_fast_cleanup()
        app._crash_recovery.flush.assert_not_called()
        assert _stub_os_exit == [0, 0], (
            f"UE-1: both _do_fast_cleanup invocations must call os._exit(0); got {_stub_os_exit}"
        )

    def test_os_exit_runs_after_all_cleanup_steps(self, _stub_os_exit, monkeypatch):
        """All critical cleanup steps must run BEFORE ``os._exit(0)``.
        Verify by recording the call order."""
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
        assert crash_idx < exit_idx, f"UE-1: crash_recovery.flush must run BEFORE os._exit; got order: {call_order}"
        assert history_idx < exit_idx, f"UE-1: history_db.flush must run BEFORE os._exit; got order: {call_order}"

    def test_os_exit_runs_even_when_cleanup_step_raises(self, _stub_os_exit):
        """UE-1: if a cleanup step raises, ``_do_fast_cleanup`` must
        still reach ``os._exit(0)`` (best-effort cleanup — the OS is
        killing us and we must not return True without exiting)."""
        controller, app = _make_controller_with_app()
        app._crash_recovery = MagicMock()
        app._crash_recovery.flush.side_effect = RuntimeError("simulated failure")
        # Must not raise.
        controller._do_fast_cleanup()
        # UE-1: os._exit(0) must still be called.
        assert _stub_os_exit == [0]


# ── UE-1: signal_handlers.win32_console_handler routing ─────────────


class TestUE1Win32RoutingFastCleanup:
    """UE-1: ``win32_console_handler`` must route
    CTRL_LOGOFF_EVENT (5) / CTRL_SHUTDOWN_EVENT (6) — the Win32
    analogues of WM_QUERYENDSESSION — to ``_do_fast_cleanup()``
    (NOT ``controller.quit()``)."""

    def test_logoff_event_routes_to_fast_cleanup(self, _stub_os_exit):
        """CTRL_LOGOFF_EVENT (5) must invoke ``_do_fast_cleanup``
        synchronously (not on a daemon thread — the OS force-kills
        after ~5s)."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        fast_cleanup_calls: list[int] = []

        def _spy_fast_cleanup():
            fast_cleanup_calls.append(1)

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]
        controller.quit = MagicMock()  # type: ignore[assignment]

        result = win32_console_handler(controller, 5)

        assert result is True, "UE-1: win32_console_handler must return True for CTRL_LOGOFF_EVENT"
        assert fast_cleanup_calls == [1], "UE-1: CTRL_LOGOFF_EVENT must invoke _do_fast_cleanup exactly once"
        (
            controller.quit.assert_not_called(),
            (
                "UE-1: CTRL_LOGOFF_EVENT must NOT invoke controller.quit() "
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

        assert result is True, "UE-1: win32_console_handler must return True for CTRL_SHUTDOWN_EVENT"
        assert fast_cleanup_calls == [1], "UE-1: CTRL_SHUTDOWN_EVENT must invoke _do_fast_cleanup exactly once"
        controller.quit.assert_not_called(), ("UE-1: CTRL_SHUTDOWN_EVENT must NOT invoke controller.quit()")

    def test_logoff_event_calls_fast_cleanup_synchronously(self, _stub_os_exit):
        """The fast-cleanup dispatch must be SYNCHRONOUS (not on a
        daemon thread) — the Win32 console-control callback runs on a
        dedicated OS thread and returning True signals "handled".
        Spawning a daemon thread would race the OS force-kill (~5s)."""
        from voice_typer.server.signal_handlers import win32_console_handler

        controller, _ = _make_controller_with_app()
        caller_threads: list[int] = []

        def _spy_fast_cleanup():
            caller_threads.append(threading.get_ident())

        controller._do_fast_cleanup = _spy_fast_cleanup  # type: ignore[assignment]

        main_thread_id = threading.get_ident()
        win32_console_handler(controller, 5)

        assert caller_threads, "UE-1: _do_fast_cleanup must be called synchronously (not on a spawned thread)"
        assert caller_threads[0] == main_thread_id, (
            "UE-1: _do_fast_cleanup must run on the SAME thread as the "
            "win32_console_handler callback (synchronous dispatch)"
        )

    def test_ctrl_c_still_routes_to_quit(self):
        """Sanity: Ctrl+C (0) / Ctrl+Break (1) must STILL route to
        ``controller.quit()`` — only logoff/shutdown were changed to
        ``_do_fast_cleanup()``. Ctrl+C is a user-initiated signal with
        no OS-imposed deadline, so the slow path is correct."""
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


# ── UE-1: POSIX SIGTERM intentionally NOT routed to fast cleanup ────


class TestUE1PosixSigtermUsesSlowPath:
    """UE-1: POSIX SIGTERM must still route to ``controller.quit()``
    (the slow path). POSIX does NOT impose a 5-second OS deadline on
    SIGTERM (``systemd``'s ``DefaultTimeoutStopSec`` defaults to 90s),
    so the slow path with full cleanup is correct. Only Windows
    logoff/shutdown has the 5s deadline that requires the fast path.

    This is the negative-space assertion of the dispatch contract:
    ``_do_fast_cleanup`` is the dispatch target for Windows
    WM_QUERYENDSESSION-equivalent signals ONLY, not for POSIX SIGTERM.
    """

    def test_signal_watcher_loop_invokes_quit_not_fast_cleanup(self):
        """``signal_watcher_loop`` (the POSIX signal watcher thread)
        spawns ``threading.Thread(target=controller.quit)`` — NOT
        ``controller._do_fast_cleanup``."""
        src = _src(_SIGNAL_HANDLERS_PATH)
        # The watcher loop's dispatch line.
        assert "target=controller.quit" in src, (
            "UE-1: POSIX signal_watcher_loop must dispatch to "
            "controller.quit (slow path) — NOT _do_fast_cleanup. "
            "POSIX SIGTERM has no OS-imposed deadline."
        )
        # And it must NOT mention _do_fast_cleanup anywhere in the
        # POSIX signal-handler path (the function exists only for the
        # Win32 console-control callback).
        # Find the signal_watcher_loop body and assert _do_fast_cleanup
        # is NOT referenced inside it.
        watcher_idx = src.find("def signal_watcher_loop(")
        assert watcher_idx > -1, "signal_watcher_loop must exist"
        # Slice to the next top-level ``def `` or ``__all__``.
        next_def = src.find("\ndef ", watcher_idx + 1)
        if next_def == -1:
            next_def = len(src)
        watcher_body = src[watcher_idx:next_def]
        assert "_do_fast_cleanup" not in watcher_body, (
            "UE-1: POSIX signal_watcher_loop must NOT reference _do_fast_cleanup (the fast path is Windows-only)"
        )

    def test_install_signal_handlers_does_not_reference_fast_cleanup(self):
        """``install_signal_handlers`` (POSIX SIGINT/SIGTERM/SIGHUP
        registration) must NOT reference ``_do_fast_cleanup`` — only
        the Win32 console-control handler routes to it."""
        src = _src(_SIGNAL_HANDLERS_PATH)
        install_idx = src.find("def install_signal_handlers(")
        assert install_idx > -1
        next_def = src.find("\ndef ", install_idx + 1)
        if next_def == -1:
            next_def = len(src)
        install_body = src[install_idx:next_def]
        assert "_do_fast_cleanup" not in install_body, (
            "UE-1: install_signal_handlers must NOT reference "
            "_do_fast_cleanup (POSIX signals route to quit, not the "
            "fast cleanup path)"
        )

    def test_win32_console_handler_references_fast_cleanup(self):
        """``win32_console_handler`` (the Windows console-control
        callback) MUST reference ``_do_fast_cleanup`` — this is the
        UE-1 contract: Windows logoff/shutdown routes to the fast
        path, NOT the slow path."""
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
            "UE-1: win32_console_handler must reference _do_fast_cleanup "
            "(Windows logoff/shutdown routes to the fast path)"
        )


# ── UE-1: _do_fast_cleanup source-level contract ────────────────────


class TestUE1FastCleanupSource:
    """UE-1: source-level contract for ``_do_fast_cleanup``."""

    def test_do_fast_cleanup_ends_with_os_exit_zero(self):
        """The LAST executable statement in ``_do_fast_cleanup`` must
        be ``os._exit(0)`` (after the conditional cleanup body)."""
        src = _src(_SHUTDOWN_CONTROLLER_PATH)
        # Find the _do_fast_cleanup body.
        idx = src.find("def _do_fast_cleanup(self) -> None:")
        assert idx > -1, "UE-1: _do_fast_cleanup method must exist"
        # Slice to the next ``def `` (end of the method body).
        next_def = src.find("\n    def ", idx + 1)
        assert next_def > -1, "UE-1: _do_fast_cleanup must be followed by another method"
        body = src[idx:next_def]
        # The last non-comment, non-blank line in the body must be
        # ``os._exit(0)``.
        code_lines = [line for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
        assert code_lines, "UE-1: _do_fast_cleanup body must not be empty"
        last_line = code_lines[-1].strip()
        assert last_line == "os._exit(0)", (
            f"UE-1: _do_fast_cleanup must end with `os._exit(0)` (last executable line); got: {last_line!r}"
        )

    def test_os_exit_is_outside_cleanup_done_guard(self):
        """The ``os._exit(0)`` call must be OUTSIDE the
        ``_cleanup_done`` short-circuit ``return`` so it fires even
        on a no-op second invocation (the Win32 callback must NOT
        return True without exiting)."""
        src = _src(_SHUTDOWN_CONTROLLER_PATH)
        idx = src.find("def _do_fast_cleanup(self) -> None:")
        next_def = src.find("\n    def ", idx + 1)
        body = src[idx:next_def]
        # The early-return guard must exist (idempotency).
        assert "if not already_done:" in body, (
            "UE-1: _do_fast_cleanup must use `if not already_done:` to "
            "guard the cleanup body (idempotency with prior _do_cleanup)"
        )
        # ``os._exit(0)`` must appear AFTER the ``if not already_done:``
        # block closes (i.e. it must be at the same indentation level
        # as the ``if`` statement, not nested inside it).
        if_idx = body.find("if not already_done:")
        # Find the last ``os._exit(0)`` in the body.
        exit_idx = body.rfind("os._exit(0)")
        assert exit_idx > if_idx, (
            "UE-1: os._exit(0) must appear AFTER the `if not already_done:` "
            "block so it fires regardless of the _cleanup_done state"
        )
        # The line containing ``os._exit(0)`` must NOT be indented
        # inside the ``if`` block. The ``if`` block body is indented
        # one level deeper than the ``if`` statement. We check by
        # finding the line and counting its leading whitespace.
        exit_line_start = body.rfind("\n", 0, exit_idx) + 1
        exit_line = body[exit_line_start : body.find("\n", exit_idx)]
        # The ``if not already_done:`` statement is indented 8 spaces
        # (2 levels of 4-space indent: class + method body). The body
        # of the ``if`` is indented 12 spaces. ``os._exit(0)`` MUST
        # be at 8 spaces (method-body level, NOT inside the ``if``).
        leading_spaces = len(exit_line) - len(exit_line.lstrip(" "))
        assert leading_spaces == 8, (
            f"UE-1: os._exit(0) must be at method-body indentation "
            f"(8 spaces) so it runs unconditionally; got {leading_spaces} "
            f"spaces (line: {exit_line!r})"
        )


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v", "-o", "addopts="])
