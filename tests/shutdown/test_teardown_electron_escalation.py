"""escalation-path coverage for
``voice_typer.server.shutdown.teardowns.electron.teardown_electron``.

The ``teardown_electron`` helper terminates the Electron subprocess via
``electron_launcher.terminate_electron`` wrapped in ``_run_with_timeout(5.0)``.
When that call times out (returns the ``TIMEOUT`` sentinel), the helper
escalates:

  * **Windows** — calls ``ctypes.windll.kernel32.OpenProcess`` with
    ``PROCESS_TERMINATE`` (``0x0001``) access, then ``TerminateProcess``,
    then ``CloseHandle``.
  * **POSIX** — calls ``os.kill(pid, SIGKILL)`` (after the initial
    SIGTERM attempt inside ``terminate_electron``).

Pre-fix, the Windows branch was a silent no-op on timeout (the POSIX
branch had SIGKILL escalation but Windows had none). The
extracted ``teardowns/electron.py`` module closes that gap; these
tests pin the escalation behavior so a future refactor doesn't silently
re-introduce the no-op.

Platform-qualified: the Windows test mocks ``ctypes.windll`` +
``is_windows()`` so it runs on the Linux CI host without touching real
Win32 APIs. The POSIX test mocks ``is_windows()`` to return False and
mocks ``os.kill`` so no real signal is delivered.

All OS-level calls are mocked — no real signals, no real subprocess,
no real Win32 handles.
"""

from __future__ import annotations

import signal
import threading
from unittest.mock import MagicMock

import pytest

# The teardown helper looks up ``_run_with_timeout`` and ``TIMEOUT``
# DYNAMICALLY from ``shutdown_controller`` at call time (see the module
# docstring in ``teardowns/electron.py``). Patching
# ``shutdown_controller._run_with_timeout`` therefore takes effect on
# the next ``teardown_electron`` call — mirrors the convention used by
# ``tests/test_shutdown_teardown_fixes.py``.
from voice_typer.server import shutdown_controller as _sc
from voice_typer.server.shutdown.teardowns.electron import teardown_electron

# PROCESS_TERMINATE access right (Win32). The literal ``0x0001`` is
# pinned in the teardown helper's source; the test asserts the same
# value reaches ``OpenProcess`` so a future refactor can't silently
# broaden the access right (e.g. to PROCESS_ALL_ACCESS).
_PROCESS_TERMINATE = 0x0001


def _make_controller_with_app():
    """Build a minimal controller+app pair for ``teardown_electron``.

    The helper reads ``controller._app`` and ``controller._electron_pid_lock``
    and (in the timeout branch) touches ``app._electron_pid``. The rest of
    the ShutdownController surface is not exercised by this helper, so a
    bare ``MagicMock`` app + a real ``threading.Lock`` suffices.

    Mirrors the ``_make_controller_with_app`` helper in
    ``tests/test_shutdown_teardown_fixes.py`` (kept here so this test
    module is self-contained — the sibling helper uses a richer fake-app
    surface for the broader ``_do_cleanup`` tests).
    """
    app = MagicMock()
    app._electron_pid = None
    controller = MagicMock()
    controller._app = app
    controller._electron_pid_lock = threading.Lock()
    return controller, app


# ── Windows TerminateProcess fallback ─────────────────────────────────


class TestWindowsTerminateProcessEscalation:
    """when ``terminate_electron`` times out on Windows,
    ``teardown_electron`` MUST fall back to ``OpenProcess`` +
    ``TerminateProcess`` + ``CloseHandle`` via ctypes, requesting the
    ``PROCESS_TERMINATE`` (``0x0001``) access right.

    Pre-fix the Windows branch was a silent no-op on timeout.
    """

    def test_windows_terminate_process_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force ``is_windows()`` to True, mock ``terminate_electron`` to
        time out (return ``TIMEOUT``), mock ``ctypes.windll.kernel32`` so
        ``OpenProcess`` / ``TerminateProcess`` / ``CloseHandle`` are
        MagicMocks, then assert:

          1. ``OpenProcess`` was called with ``PROCESS_TERMINATE`` (0x0001)
             as the first positional arg.
          2. ``TerminateProcess`` was called (with the handle returned by
             ``OpenProcess``).
          3. ``CloseHandle`` was called (so the handle is not leaked).
          4. ``app._electron_pid`` is ``None`` after teardown (the stale
             PID is cleared even on the timeout path so the next launch
             isn't blocked).

        No real Win32 API is touched — ``ctypes`` itself is replaced in
        ``sys.modules`` so the ``import ctypes`` inside the helper
        resolves to the fake.
        """
        controller, app = _make_controller_with_app()
        app._electron_pid = 99999  # the tracked Electron subprocess PID

        # Force the Windows branch. ``teardown_electron`` imports
        # ``is_windows`` from ``platform_utils`` at module load, so we
        # patch the same reference the helper sees at call time.
        monkeypatch.setattr(
            "voice_typer.server.shutdown.teardowns.electron.is_windows",
            lambda: True,
        )

        # Stub ``electron_launcher.terminate_electron`` so it does NOT
        # actually try to kill a real process. Patch BOTH
        # ``sys.modules`` AND the parent-package attribute (mirrors the
        # test-isolation hardening in
        # ``test_shutdown_teardown_fixes.py::test_electron_pid_cleared_even_on_windows_timeout``)
        # so the ``from voice_typer.server import electron_launcher``
        # inside the helper resolves to the fake regardless of import
        # order.
        fake_electron_launcher = MagicMock()
        # terminate_electron returns None on the happy path; the TIMEOUT
        # sentinel comes from ``_run_with_timeout``, not the helper
        # itself. So the stub just needs to exist (return value unused
        # when _run_with_timeout returns TIMEOUT).
        fake_electron_launcher.terminate_electron = MagicMock(return_value=None)
        import sys as _sys

        monkeypatch.setitem(
            _sys.modules,
            "voice_typer.server.electron_launcher",
            fake_electron_launcher,
        )
        monkeypatch.setattr(
            "voice_typer.server.electron_launcher",
            fake_electron_launcher,
            raising=False,
        )

        # Force ``_run_with_timeout`` to return TIMEOUT for the
        # ``terminate_electron`` call so the escalation branch runs.
        # Other calls (none expected in this helper) pass through to
        # the real implementation.
        real_run_with_timeout = _sc._run_with_timeout

        def _timeout_on_terminate(description, func, timeout=5.0):
            if description == "electron_launcher.terminate_electron":
                return _sc.TIMEOUT
            return real_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _timeout_on_terminate)

        # Fake ctypes + wintypes so the ``import ctypes`` / ``from
        # ctypes import wintypes`` inside the helper resolve to mocks.
        # ``kernel32.OpenProcess`` returns a non-zero handle so the
        # ``if handle:`` branch proceeds to TerminateProcess + CloseHandle.
        fake_kernel32 = MagicMock()
        fake_kernel32.OpenProcess.return_value = 12345  # non-NULL handle
        fake_kernel32.TerminateProcess.return_value = 1
        fake_kernel32.CloseHandle.return_value = 1

        fake_windll = MagicMock()
        fake_windll.kernel32 = fake_kernel32

        class _FakeWintypes:
            DWORD = int
            BOOL = int
            HANDLE = int

        fake_ctypes = MagicMock()
        fake_ctypes.windll = fake_windll
        fake_ctypes.wintypes = _FakeWintypes

        monkeypatch.setitem(_sys.modules, "ctypes", fake_ctypes)
        monkeypatch.setitem(_sys.modules, "ctypes.wintypes", _FakeWintypes)

        teardown_electron(controller)

        # 1. OpenProcess was called with PROCESS_TERMINATE (0x0001) as
        # the first positional arg.
        fake_kernel32.OpenProcess.assert_called_once()
        call_args, _ = fake_kernel32.OpenProcess.call_args
        assert call_args[0] == _PROCESS_TERMINATE, (
            f"OpenProcess must be called with PROCESS_TERMINATE (0x0001) "
            f"as the first arg; got {call_args[0]!r}"
        )
        # The PID must be passed as the third positional arg.
        assert call_args[2] == 99999, (
            f"OpenProcess must be called with the Electron PID (99999) as "
            f"the third arg; got {call_args[2]!r}"
        )

        # 2. TerminateProcess was called with the handle OpenProcess
        # returned.
        fake_kernel32.TerminateProcess.assert_called_once()
        term_args, _ = fake_kernel32.TerminateProcess.call_args
        assert term_args[0] == 12345, (
            f"TerminateProcess must be called with the handle returned by "
            f"OpenProcess (12345); got {term_args[0]!r}"
        )

        # 3. CloseHandle was called (no handle leak).
        fake_kernel32.CloseHandle.assert_called_once()
        close_args, _ = fake_kernel32.CloseHandle.call_args
        assert close_args[0] == 12345, (
            f"CloseHandle must be called with the same handle (12345); "
            f"got {close_args[0]!r}"
        )

        # 4. The PID was cleared even on the timeout path.
        assert app._electron_pid is None, (
            "app._electron_pid must be cleared after the Windows "
            "TerminateProcess fallback path (stale PID would block the "
            "next launch's single-instance check)"
        )


# ── POSIX SIGTERM → SIGKILL escalation ────────────────────────────────


class TestPosixSigkillEscalation:
    """when ``terminate_electron`` times out on POSIX,
    ``teardown_electron`` MUST escalate to ``os.kill(pid, SIGKILL)``.

    The initial SIGTERM attempt happens inside
    ``electron_launcher.terminate_electron`` (which itself uses
    SIGTERM→SIGKILL on POSIX). The escalation tested here is the
    OUTER fallback in ``teardown_electron``: if
    ``terminate_electron`` times out (the helper is stuck), the
    teardown helper directly calls ``os.kill(pid, SIGKILL)``.

    No real signal is delivered — ``os.kill`` is mocked.
    """

    def test_posix_sigterm_then_sigkill_escalation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force ``is_windows()`` to False, mock ``terminate_electron``
        to time out (return ``TIMEOUT``), mock ``os.kill`` so no real
        signal is delivered, then assert:

          1. ``os.kill`` was called with ``SIGKILL`` (escalation after
             the SIGTERM timeout).
          2. The PID passed to ``os.kill`` matches the tracked
             ``app._electron_pid``.
          3. ``app._electron_pid`` is ``None`` after teardown.

        The ``contextlib.suppress(OSError, ProcessLookupError)`` wrapper
        around ``os.kill`` means a raised OSError is swallowed — the
        mock returns None (success) so no exception is raised.
        """
        controller, app = _make_controller_with_app()
        app._electron_pid = 88888

        # Force the POSIX branch.
        monkeypatch.setattr(
            "voice_typer.server.shutdown.teardowns.electron.is_windows",
            lambda: False,
        )

        # Stub electron_launcher.terminate_electron (no-op return).
        fake_electron_launcher = MagicMock()
        fake_electron_launcher.terminate_electron = MagicMock(return_value=None)
        import sys as _sys

        monkeypatch.setitem(
            _sys.modules,
            "voice_typer.server.electron_launcher",
            fake_electron_launcher,
        )
        monkeypatch.setattr(
            "voice_typer.server.electron_launcher",
            fake_electron_launcher,
            raising=False,
        )

        # Force _run_with_timeout to return TIMEOUT for the
        # terminate_electron call so the POSIX SIGKILL escalation runs.
        real_run_with_timeout = _sc._run_with_timeout

        def _timeout_on_terminate(description, func, timeout=5.0):
            if description == "electron_launcher.terminate_electron":
                return _sc.TIMEOUT
            return real_run_with_timeout(description, func, timeout=timeout)

        monkeypatch.setattr(_sc, "_run_with_timeout", _timeout_on_terminate)

        # Mock os.kill so no real signal is delivered. Capture every
        # call so we can assert SIGKILL was among them.
        kill_calls: list[tuple[int, int]] = []

        def _fake_kill(pid, sig):
            kill_calls.append((pid, sig))
            return None

        monkeypatch.setattr("os.kill", _fake_kill)

        teardown_electron(controller)

        # 1. os.kill was called at least once.
        assert kill_calls, (
            "POSIX SIGKILL escalation must call os.kill when "
            "terminate_electron times out (pre-fix the POSIX branch had "
            "SIGKILL escalation; this test pins it survives refactors)"
        )

        # 2. At least one call used SIGKILL (the escalation signal).
        # ``signal.SIGKILL`` is absent on Windows Python — the source
        # uses ``getattr(signal, "SIGKILL", 9)`` (the POSIX value);
        # mirror that here so the assertion is portable.
        sigkill = getattr(signal, "SIGKILL", 9)
        sigkill_calls = [(pid, sig) for (pid, sig) in kill_calls if sig == sigkill]
        assert sigkill_calls, (
            f"os.kill must be called with SIGKILL ({sigkill}) "
            f"on the escalation path; got signals {[sig for _, sig in kill_calls]}"
        )

        # 3. The PID passed to SIGKILL matches the tracked Electron PID.
        assert sigkill_calls[0][0] == 88888, (
            f"SIGKILL must target the tracked Electron PID (88888); got "
            f"{sigkill_calls[0][0]}"
        )

        # 4. The PID was cleared after teardown.
        assert app._electron_pid is None, (
            "app._electron_pid must be cleared after the POSIX "
            "SIGKILL escalation path"
        )


# ── PID clear invariant ───────────────────────────────────────────────


class TestElectronPidClearedAfterTeardown:
    """``app._electron_pid`` MUST be set to ``None`` after
    ``teardown_electron`` runs, regardless of which branch
    (happy-path / Windows-timeout / POSIX-timeout) executed.

    A stale PID would block the next launch's single-instance check
    (the launcher sees the old PID, thinks Electron is already running,
    and skips the spawn).
    """

    def test_electron_pid_cleared_after_teardown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On the happy path (``terminate_electron`` succeeds within the
        5s timeout), ``app._electron_pid`` is cleared unconditionally
        after the terminate call. This test pins that the clear runs on
        the SUCCESS path too (the escalation tests above pin the
        timeout paths)."""
        controller, app = _make_controller_with_app()
        app._electron_pid = 77777

        # POSIX (the test host is Linux); happy path — no timeout.
        monkeypatch.setattr(
            "voice_typer.server.shutdown.teardowns.electron.is_windows",
            lambda: False,
        )

        fake_electron_launcher = MagicMock()
        fake_electron_launcher.terminate_electron = MagicMock(return_value=None)
        import sys as _sys

        monkeypatch.setitem(
            _sys.modules,
            "voice_typer.server.electron_launcher",
            fake_electron_launcher,
        )
        monkeypatch.setattr(
            "voice_typer.server.electron_launcher",
            fake_electron_launcher,
            raising=False,
        )

        # Use the real _run_with_timeout (the stub terminate_electron
        # returns immediately, so no actual 5s wait).
        teardown_electron(controller)

        assert app._electron_pid is None, (
            "app._electron_pid must be cleared after teardown on "
            "the happy path (stale PID would block the next launch)"
        )

    def test_electron_pid_cleared_when_no_pid_tracked(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``app._electron_pid`` is already ``None`` (no Electron
        subprocess was launched), ``teardown_electron`` takes the
        legacy ``tray_window.get_electron_pid()`` branch. That branch
        doesn't touch ``app._electron_pid`` (it discovers the PID via
        the tray). The PID must remain ``None`` after teardown (no
        spurious mutation)."""
        controller, app = _make_controller_with_app()
        app._electron_pid = None  # no tracked PID

        monkeypatch.setattr(
            "voice_typer.server.shutdown.teardowns.electron.is_windows",
            lambda: False,
        )

        # Stub tray_window.get_electron_pid to return None (no Electron
        # running) so the legacy branch is a no-op too.
        fake_tray_window = MagicMock()
        fake_tray_window.get_electron_pid = MagicMock(return_value=None)
        import sys as _sys

        monkeypatch.setitem(
            _sys.modules,
            "voice_typer.server.tray_window",
            fake_tray_window,
        )
        monkeypatch.setattr(
            "voice_typer.server.tray_window",
            fake_tray_window,
            raising=False,
        )

        # Must not raise.
        teardown_electron(controller)

        # PID is still None (no spurious mutation).
        assert app._electron_pid is None
