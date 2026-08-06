"""Behavioral coverage for ``_ensure_windows_single_instance`` on Linux.

The existing regression tests in ``tests/regressions/platform_win32_test.py``
and ``tests/regressions/security_test.py`` only assert that the source
string of ``_ensure_windows_single_instance`` contains certain tokens
(``"VoiceTyperSingleInstance"``, absence of ``hashlib.sha256(...)``
etc.).  Source-string tests cannot catch a behavior regression where
the function constructs the right token in a dead branch but never
actually passes it to ``CreateMutexW``.

This module mocks ``ctypes.windll`` so the Windows-only code path
executes on Linux, mirroring the strategy used by
``tests/test_clipboard_win32_coverage.py`` and
``tests/test_singleton_lock.py``:

1. Patch ``ctypes.windll`` with a ``MagicMock`` exposing ``kernel32``
   and ``user32`` attributes (``create=True`` because ``windll`` does
   not exist on POSIX).
2. Patch ``_create_restrictive_security_attributes`` to return ``None``
   so we don't have to mock the entire DACL builder — the function
   only uses the return value to pass to ``CreateMutexW`` as the
   ``lp_mutex_attributes`` argument, and ``None`` short-circuits the
   ``ctypes.byref(sa)`` path.
3. Patch ``_read_stale_backend_pid``, ``_clear_backend_pid_file`` and
   ``_write_backend_pid_file`` on the ``single_instance`` module so we
   don't touch the real PID file and so we can assert call counts.

The four cases below pin the contract documented in the function's
docstring (error_already_exists=183, HANDLE_FLAG_INHERIT=0x1, stale
PID recovery, exact mutex name).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import single_instance as si_mod

# Win32 magic numbers, mirrored from ``single_instance._ensure_windows_single_instance``.
ERROR_ALREADY_EXISTS = 183
HANDLE_FLAG_INHERIT = 0x00000001
WAIT_ABANDONED = 0x00000080
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
MUTEX_NAME = "Local\\VoiceTyperSingleInstance"


@pytest.fixture
def fake_win32(monkeypatch):
    """Mock ``ctypes.windll`` so the Windows mutex path runs on Linux.

    Yields a dict with the ``kernel32`` and ``user32`` MagicMock objects
    so individual tests can configure return values per-case.  Sensible
    defaults are pre-populated for the most-common success path so a
    test that doesn't care about a particular call still gets a
    deterministic result.
    """
    mock_kernel32 = MagicMock()
    mock_user32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.kernel32 = mock_kernel32
    mock_windll.user32 = mock_user32

    # Default to a non-zero mutex handle so the ``if mutex:`` branches
    # are taken.  Individual tests override ``GetLastError`` to steer
    # into the error_already_exists / error_access_denied / success
    # branches.
    mock_kernel32.CreateMutexW.return_value = 0xDEADBEEF
    mock_kernel32.GetLastError.return_value = 0  # success
    mock_kernel32.SetHandleInformation.return_value = 1  # BOOL TRUE
    mock_kernel32.WaitForSingleObject.return_value = WAIT_TIMEOUT
    mock_kernel32.CloseHandle.return_value = 1
    mock_user32.MessageBoxW.return_value = 1

    # Stub out the helpers that touch the real PID file / DACL builder
    # so the test never writes to disk or imports ``_security_attributes``'s
    # own ctypes machinery.  ``MagicMock`` is used (not a plain ``lambda``)
    # so tests can assert ``.called`` / ``.call_count`` on the stubs.
    monkeypatch.setattr(si_mod, "_create_restrictive_security_attributes", lambda: None)
    monkeypatch.setattr(si_mod, "_read_stale_backend_pid", MagicMock(return_value=None))
    monkeypatch.setattr(si_mod, "_clear_backend_pid_file", MagicMock())
    monkeypatch.setattr(si_mod, "_write_backend_pid_file", MagicMock())

    with patch("ctypes.windll", mock_windll, create=True):
        yield {
            "kernel32": mock_kernel32,
            "user32": mock_user32,
            "windll": mock_windll,
        }


# ---------------------------------------------------------------------------
# Test 1 — error_already_exists triggers sys.exit(1)
# ---------------------------------------------------------------------------


def test_error_already_exists_triggers_sys_exit(fake_win32):
    """When ``CreateMutexW`` returns ``error_already_exists`` (183) and
    no stale-PID recovery applies, the function MUST call ``sys.exit(1)``.

    Pins the contract documented in ``_ensure_windows_single_instance``:
    on duplicate launch Windows guarantees another process holds the
    mutex RIGHT NOW; we exit immediately rather than running a
    duplicate backend that would compete for the microphone / hotkeys
    / volume control.
    """
    fake_win32["kernel32"].GetLastError.return_value = ERROR_ALREADY_EXISTS
    # WaitForSingleObject returns WAIT_TIMEOUT → genuine duplicate → fall
    # through to sys.exit(1).  Stale-PID recovery is bypassed because the
    # ``_read_stale_backend_pid`` stub returns None.
    fake_win32["kernel32"].WaitForSingleObject.return_value = WAIT_TIMEOUT

    with pytest.raises(SystemExit) as exc_info:
        si_mod._ensure_windows_single_instance(silent=True)

    assert exc_info.value.code == 1, (
        "error_already_exists with no stale-PID recovery must exit(1)"
    )
    fake_win32["kernel32"].CreateMutexW.assert_called_once()
    fake_win32["kernel32"].GetLastError.assert_called_once()
    # The duplicate-launch path closes the mutex handle before exiting.
    fake_win32["kernel32"].CloseHandle.assert_called_once_with(0xDEADBEEF)


# ---------------------------------------------------------------------------
# Test 2 — SetHandleInformation clears HANDLE_FLAG_INHERIT
# ---------------------------------------------------------------------------


def test_set_handle_information_clears_inheritance_bit(fake_win32):
    """On the success path (``GetLastError`` == 0),
    ``SetHandleInformation`` MUST be called with the inheritance flag
    set to ``HANDLE_FLAG_INHERIT=0x1`` and the new mask value ``0``
    (clearing the inheritance bit).

    Pins the MED-SSS / XCUT-7 fix: without this call the mutex handle
    is inheritable by ``subprocess.Popen`` children, so a diagnostics
    child process would falsely see ``error_already_exists`` and refuse
    to start.
    """
    fake_win32["kernel32"].GetLastError.return_value = 0  # success

    result = si_mod._ensure_windows_single_instance(silent=True)

    # The function returns the mutex handle so the caller can hold it
    # alive for the process lifetime.
    assert result == 0xDEADBEEF

    set_handle_info = fake_win32["kernel32"].SetHandleInformation
    set_handle_info.assert_called_once_with(0xDEADBEEF, HANDLE_FLAG_INHERIT, 0)
    # The success path also writes our PID so the next launch can
    # detect a stale lock if we crash hard.
    assert si_mod._write_backend_pid_file.called


# ---------------------------------------------------------------------------
# Test 3 — stale PID recovery clears the PID file and proceeds
# ---------------------------------------------------------------------------


def test_stale_pid_recovery_clears_pid_file(fake_win32, monkeypatch):
    """When ``error_already_exists`` fires AND the PID file points to a
    dead process, the function MUST clear the stale PID file and
    proceed (return the mutex) instead of exiting.

    Pins the P1-1.4 belt-and-suspenders check: ``error_already_exists``
    usually means another process holds the mutex RIGHT NOW, but if the
    PID file points to a dead process the mutex may be abandoned
    (previous owner crashed).  ``WaitForSingleObject`` returns
    ``WAIT_ABANDONED`` in that case — we acquire ownership, write our
    own PID, and proceed.
    """
    fake_win32["kernel32"].GetLastError.return_value = ERROR_ALREADY_EXISTS
    # Stale PID present — _read_stale_backend_pid returns a non-None PID.
    monkeypatch.setattr(si_mod, "_read_stale_backend_pid", lambda: 12345)
    # WaitForSingleObject returns WAIT_ABANDONED → previous owner died,
    # we now own the mutex → proceed.
    fake_win32["kernel32"].WaitForSingleObject.return_value = WAIT_ABANDONED

    clear_calls: list[int] = []
    write_calls: list[bool] = []

    def _fake_clear() -> None:
        clear_calls.append(1)

    def _fake_write() -> None:
        write_calls.append(True)

    monkeypatch.setattr(si_mod, "_clear_backend_pid_file", _fake_clear)
    monkeypatch.setattr(si_mod, "_write_backend_pid_file", _fake_write)

    result = si_mod._ensure_windows_single_instance(silent=True)

    # Stale PID file was cleared as part of recovery.
    assert len(clear_calls) == 1, (
        "stale PID recovery must call _clear_backend_pid_file exactly once"
    )
    # The recovered mutex path writes OUR PID so the next launch can
    # detect a stale lock if we crash hard.
    assert len(write_calls) == 1, (
        "stale PID recovery must write our own PID via _write_backend_pid_file"
    )
    # The function proceeds (returns the mutex) — does NOT sys.exit.
    assert result == 0xDEADBEEF


# ---------------------------------------------------------------------------
# Test 4 — mutex name is exactly "Local\VoiceTyperSingleInstance"
# ---------------------------------------------------------------------------


def test_mutex_name_is_exactly_local_voicetyper_single_instance(fake_win32):
    """``CreateMutexW`` MUST be called with the exact name
    ``"Local\\\\VoiceTyperSingleInstance"``.

    Pins SEC-001 / PLAT-RUN-FIXED: the mutex name is a fixed string so
    ALL VoiceTyper processes (regardless of Python executable) share
    the same mutex.  The previous implementation included
    ``hashlib.sha256(sys.executable.encode())`` which let dev venvs
    and production installs run as separate instances.

    The source-string test in ``tests/regressions/platform_win32_test.py``
    only checks that the token appears in the source — this behavioral
    test asserts the token is actually passed to ``CreateMutexW``.
    """
    fake_win32["kernel32"].GetLastError.return_value = 0  # success

    si_mod._ensure_windows_single_instance(silent=True)

    # CreateMutexW(lp_mutex_attributes, bInitialOwner, lpName).
    create_mutex = fake_win32["kernel32"].CreateMutexW
    create_mutex.assert_called_once()
    args, _ = create_mutex.call_args
    # ``lp_mutex_attributes`` may be None or a byref; ``bInitialOwner``
    # must be True; ``lpName`` MUST be the exact fixed string.
    assert args[2] == MUTEX_NAME, (
        f"CreateMutexW must be called with mutex name {MUTEX_NAME!r}; "
        f"got {args[2]!r}"
    )
    # bInitialOwner must be True so WE own the handle.
    assert args[1] is True, "CreateMutexW must be called with bInitialOwner=True"
