"""Behavioral coverage for ``_ensure_windows_single_instance`` on Linux."""

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
MUTEX_NAME = "Local\\LausuSingleInstance"


@pytest.fixture
def fake_win32(monkeypatch):
    """Mock ``ctypes.windll`` so the Windows mutex path runs on Linux."""
    mock_kernel32 = MagicMock()
    mock_user32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.kernel32 = mock_kernel32
    mock_windll.user32 = mock_user32

    # Default to a non-zero mutex handle so the ``if mutex:`` branches
    mock_kernel32.CreateMutexW.return_value = 0xDEADBEEF
    mock_kernel32.GetLastError.return_value = 0  # success
    mock_kernel32.SetHandleInformation.return_value = 1  # BOOL TRUE
    mock_kernel32.WaitForSingleObject.return_value = WAIT_TIMEOUT
    mock_kernel32.CloseHandle.return_value = 1
    mock_user32.MessageBoxW.return_value = 1

    # Stub out the helpers that touch the real PID file / DACL builder
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


def test_error_already_exists_triggers_sys_exit(fake_win32):
    """
    no stale-PID recovery applies, the function MUST call ``sys.exit(1)``.
    Pins the contract documented in ``_ensure_windows_single_instance``:
    """
    fake_win32["kernel32"].GetLastError.return_value = ERROR_ALREADY_EXISTS
    # WaitForSingleObject returns WAIT_TIMEOUT → genuine duplicate → fall
    fake_win32["kernel32"].WaitForSingleObject.return_value = WAIT_TIMEOUT

    with pytest.raises(SystemExit) as exc_info:
        si_mod._ensure_windows_single_instance(silent=True)

    assert exc_info.value.code == 1, "error_already_exists with no stale-PID recovery must exit(1)"
    fake_win32["kernel32"].CreateMutexW.assert_called_once()
    fake_win32["kernel32"].GetLastError.assert_called_once()
    # The duplicate-launch path closes the mutex handle before exiting.
    fake_win32["kernel32"].CloseHandle.assert_called_once_with(0xDEADBEEF)


def test_set_handle_information_clears_inheritance_bit(fake_win32):
    """On the success path (``GetLastError`` == 0),"""
    fake_win32["kernel32"].GetLastError.return_value = 0  # success

    result = si_mod._ensure_windows_single_instance(silent=True)

    # The function returns the mutex handle so the caller can hold it
    assert result == 0xDEADBEEF

    set_handle_info = fake_win32["kernel32"].SetHandleInformation
    set_handle_info.assert_called_once_with(0xDEADBEEF, HANDLE_FLAG_INHERIT, 0)
    # The success path also writes our PID so the next launch can
    assert si_mod._write_backend_pid_file.called


def test_stale_pid_recovery_clears_pid_file(fake_win32, monkeypatch):
    """proceed (return the mutex) instead of exiting."""
    fake_win32["kernel32"].GetLastError.return_value = ERROR_ALREADY_EXISTS
    # Stale PID present, _read_stale_backend_pid returns a non-None PID.
    monkeypatch.setattr(si_mod, "_read_stale_backend_pid", lambda: 12345)
    # WaitForSingleObject returns WAIT_ABANDONED → previous owner died,
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
    assert len(clear_calls) == 1, "stale PID recovery must call _clear_backend_pid_file exactly once"
    # The recovered mutex path writes OUR PID so the next launch can
    assert len(write_calls) == 1, "stale PID recovery must write our own PID via _write_backend_pid_file"
    # The function proceeds (returns the mutex), does NOT sys.exit.
    assert result == 0xDEADBEEF


def test_mutex_name_is_exactly_local_lausu_single_instance(fake_win32):
    """
    ``CreateMutexW`` MUST be called with the exact name
    Pins SEC-001 / PLAT-RUN-FIXED: the mutex name is a fixed string so
    """
    fake_win32["kernel32"].GetLastError.return_value = 0  # success

    si_mod._ensure_windows_single_instance(silent=True)

    # CreateMutexW(lp_mutex_attributes, bInitialOwner, lpName).
    create_mutex = fake_win32["kernel32"].CreateMutexW
    create_mutex.assert_called_once()
    args, _ = create_mutex.call_args
    # ``lp_mutex_attributes`` may be None or a byref; ``bInitialOwner``
    assert args[2] == MUTEX_NAME, f"CreateMutexW must be called with mutex name {MUTEX_NAME!r}; got {args[2]!r}"
    assert args[1] is True, "CreateMutexW must be called with bInitialOwner=True"
