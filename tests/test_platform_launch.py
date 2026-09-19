"""
Unit tests for ``voice_typer.server.platform_launch``.
The module under test (REF-3 / XPLAT-01 / SEC-audit-011) contains
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.platform_launch import (
    _systemroot_notepad_path,
    _windows_close_process_handle,
    _windows_open_with_default_app,
    _windows_wait_for_process_exit,
)

_SEE_MASK_NOCLOSEPROCESS = 0x40
_SW_SHOWNORMAL = 1
_INFINITE = 0xFFFFFFFF

# DE-68: the production code uses a FINITE 30-minute timeout (NOT
_WAIT_TIMEOUT_MS = 30 * 60 * 1000  # 30 minutes


def _install_fake_windll(monkeypatch, shell32=None, kernel32=None):
    """Install a fake ``ctypes.windll`` exposing ``.shell32`` / ``.kernel32``."""
    mock_windll = MagicMock()
    if shell32 is not None:
        mock_windll.shell32 = shell32
    if kernel32 is not None:
        mock_windll.kernel32 = kernel32
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
    return mock_windll


def _strip_windll(monkeypatch):
    """Ensure ``ctypes.windll`` is absent (mimics Linux runtime)."""
    if hasattr(ctypes, "windll"):
        monkeypatch.delattr(ctypes, "windll")


class TestSystemRootNotepadPath:
    """
    Pin SYSTEMROOT-validated Notepad resolution (SEC-audit-011).
    * Never resolve a bare PATH-resolved ``notepad`` (SEC-audit-011).
    """

    def test_prefers_hardcoded_default_when_both_exist(self, monkeypatch):
        """the SYSTEMROOT-derived path exist, the HARDCODED path wins."""
        custom_root = r"D:\Attacker"
        monkeypatch.setenv("SYSTEMROOT", custom_root)
        default_path = Path(r"C:\Windows") / "System32" / "notepad.exe"
        attacker_path = Path(custom_root) / "System32" / "notepad.exe"

        monkeypatch.setattr(Path, "exists", lambda self: True)

        result = _systemroot_notepad_path()
        assert result == default_path, (
            f"XZ-R6-AS-07: hardcoded default must be preferred over "
            f"SYSTEMROOT-derived path; got {result!r}, expected {default_path!r}"
        )
        assert result != attacker_path, (
            "XZ-R6-AS-07: SYSTEMROOT-derived path must NOT be returned when the hardcoded default also exists"
        )

    def test_returns_systemroot_path_when_it_exists(self, monkeypatch):
        """When ONLY the SYSTEMROOT-derived path exists (hardcoded"""
        custom_root = r"D:\CustomWin"
        # Build the expected path with the same construction the code
        expected = Path(custom_root) / "System32" / "notepad.exe"
        monkeypatch.setenv("SYSTEMROOT", custom_root)

        def fake_exists(self):
            # ONLY the SYSTEMROOT-derived path exists; the hardcoded
            return self == expected

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = _systemroot_notepad_path()
        assert result is not None
        assert result == expected

    def test_falls_back_to_default_when_systemroot_missing(self, monkeypatch):
        """If SYSTEMROOT-based path is missing, fall back to ``C:\\Windows``."""
        monkeypatch.setenv("SYSTEMROOT", r"D:\Missing")
        default_path = Path(r"C:\Windows") / "System32" / "notepad.exe"

        def fake_exists(self):
            return self == default_path

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = _systemroot_notepad_path()
        assert result is not None
        assert result == default_path

    def test_returns_none_when_neither_candidate_exists(self, monkeypatch):
        """When both candidates are missing, returns ``None``."""
        monkeypatch.setenv("SYSTEMROOT", r"D:\Missing1")
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert _systemroot_notepad_path() is None

    def test_defaults_to_c_windows_when_systemroot_unset(self, monkeypatch):
        """When SYSTEMROOT env var is unset, the first candidate uses"""
        monkeypatch.delenv("SYSTEMROOT", raising=False)
        first_candidate = []
        default_path = Path(r"C:\Windows") / "System32" / "notepad.exe"

        def fake_exists(self):
            first_candidate.append(self)
            return self == default_path

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = _systemroot_notepad_path()
        assert result is not None
        assert result == default_path
        # The very first candidate checked must NOT be a relative path
        first = first_candidate[0]
        # Path is absolute if it has a drive (Windows) or starts with /
        assert "Windows" in str(first), f"Empty/missing SYSTEMROOT must not produce a relative path; got {first!r}"

    def test_continues_on_oserror_during_exists(self, monkeypatch):
        """If the first candidate raises ``OSError`` (e.g. permission"""
        custom_root = r"D:\CustomWin"
        monkeypatch.setenv("SYSTEMROOT", custom_root)
        Path(r"C:\Windows") / "System32" / "notepad.exe"
        systemroot_path = Path(custom_root) / "System32" / "notepad.exe"
        call_count = {"n": 0}

        def fake_exists(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First candidate (default_path per  order)
                raise OSError("permission denied")
            return self == systemroot_path

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = _systemroot_notepad_path()
        assert result is not None
        assert result == systemroot_path
        assert call_count["n"] >= 2  # second candidate was tried

    def test_returns_path_object_not_string(self, monkeypatch):
        """Result must be a ``pathlib.Path`` instance (not a string)."""
        monkeypatch.setattr(Path, "exists", lambda self: True)
        result = _systemroot_notepad_path()
        assert isinstance(result, Path)


class TestWindowsOpenWithDefaultApp:
    """
    Pin ``ShellExecuteEx``-based default-app launch.
    Contract: returns a truthy process handle on success and ``None``
    """

    def test_returns_none_on_non_windows(self, monkeypatch):
        """On Linux (no ``ctypes.windll``), function returns ``None``."""
        _strip_windll(monkeypatch)
        assert _windows_open_with_default_app("foo.json") is None

    def test_returns_handle_when_shellexecute_succeeds(self, monkeypatch):
        """When ``ShellExecuteExW`` succeeds and writes a non-zero"""
        mock_shell32 = MagicMock()
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        def _side_effect(byref_obj):
            byref_obj._obj.hProcess = 0xDEADBEEF
            return 1  # BOOL TRUE

        mock_shell32.ShellExecuteExW.side_effect = _side_effect

        result = _windows_open_with_default_app(r"C:\config.json")
        assert result == 0xDEADBEEF
        mock_shell32.ShellExecuteExW.assert_called_once()

    def test_returns_none_when_shellexecute_fails(self, monkeypatch):
        """When ``ShellExecuteExW`` returns 0 (failure), function"""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 0  # BOOL FALSE
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        assert _windows_open_with_default_app("foo.json") is None

    def test_returns_none_when_hprocess_is_null(self, monkeypatch):
        """Even when ``ShellExecuteExW`` reports success, a null"""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 1  # success
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        assert _windows_open_with_default_app("foo.json") is None

    def test_sets_argtypes_and_restype_on_shellexecute(self, monkeypatch):
        """``ShellExecuteExW`` before the call (Win32 ABI safety: without"""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 0
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        _windows_open_with_default_app("foo.json")
        # MagicMock records attribute assignments; if the function
        shell_ex = mock_shell32.ShellExecuteExW
        # The function assigns these explicitly, so they must be set
        assert shell_ex.argtypes is not None
        assert shell_ex.restype is not None

    def test_invokes_shellexecute_with_path_in_sei(self, monkeypatch):
        """SHELLEXECUTEINFO struct's ``lpFile`` field, with the correct"""
        mock_shell32 = MagicMock()
        captured = {}

        def _side_effect(byref_obj):
            sei = byref_obj._obj
            captured["lpFile"] = sei.lpFile
            captured["lpVerb"] = sei.lpVerb
            captured["fMask"] = sei.fMask
            captured["nShow"] = sei.nShow
            captured["cbSize"] = sei.cbSize
            return 0  # failure → function returns None immediately

        mock_shell32.ShellExecuteExW.side_effect = _side_effect
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        _windows_open_with_default_app(r"C:\Users\me\config.json")
        assert captured["lpFile"] == r"C:\Users\me\config.json"
        assert captured["lpVerb"] == "open"
        assert captured["fMask"] == _SEE_MASK_NOCLOSEPROCESS
        assert captured["nShow"] == _SW_SHOWNORMAL
        assert captured["cbSize"] > 0


class TestWindowsWaitForProcessExit:
    """Pin ``WaitForSingleObject(handle, finite-timeout)`` dispatch."""

    def test_calls_wait_for_single_object_with_infinite(self, monkeypatch):
        """finite 30-minute timeout (NOT ``INFINITE``, a hung editor must"""
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.return_value = 0  # WAIT_OBJECT_0
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        _windows_wait_for_process_exit(0x1234)
        mock_kernel32.WaitForSingleObject.assert_called_once_with(0x1234, _WAIT_TIMEOUT_MS)

    def test_assigns_argtypes_and_restype(self, monkeypatch):
        """``argtypes``/``restype`` must be set on"""
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.return_value = 0
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        _windows_wait_for_process_exit(0x1234)
        assert mock_kernel32.WaitForSingleObject.argtypes is not None
        assert mock_kernel32.WaitForSingleObject.restype is not None

    def test_swallows_exception_on_non_windows(self, monkeypatch):
        """On Linux (no ``ctypes.windll``), function returns ``None``"""
        _strip_windll(monkeypatch)
        assert _windows_wait_for_process_exit(0x1234) is None

    def test_swallows_kernel32_runtime_error(self, monkeypatch):
        """If ``WaitForSingleObject`` raises at runtime, the function"""
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.side_effect = OSError("boom")
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        # Must not raise.
        assert _windows_wait_for_process_exit(0x1234) is None


class TestWindowsCloseProcessHandle:
    """Pin ``CloseHandle`` dispatch."""

    def test_calls_close_handle_with_handle(self, monkeypatch):
        """``CloseHandle`` must be invoked with the handle argument."""
        mock_kernel32 = MagicMock()
        mock_kernel32.CloseHandle.return_value = 1  # BOOL TRUE
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        _windows_close_process_handle(0x1234)
        mock_kernel32.CloseHandle.assert_called_once_with(0x1234)

    def test_assigns_argtypes_and_restype(self, monkeypatch):
        """``argtypes``/``restype`` must be set on ``CloseHandle``."""
        mock_kernel32 = MagicMock()
        mock_kernel32.CloseHandle.return_value = 1
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        _windows_close_process_handle(0x1234)
        assert mock_kernel32.CloseHandle.argtypes is not None
        assert mock_kernel32.CloseHandle.restype is not None

    def test_swallows_exception_on_non_windows(self, monkeypatch):
        """On Linux (no ``ctypes.windll``), function returns ``None``"""
        _strip_windll(monkeypatch)
        assert _windows_close_process_handle(0x1234) is None

    def test_swallows_closehandle_runtime_error(self, monkeypatch):
        """If ``CloseHandle`` raises at runtime, the function must"""
        mock_kernel32 = MagicMock()
        mock_kernel32.CloseHandle.side_effect = OSError("boom")
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        assert _windows_close_process_handle(0x1234) is None


class TestOpenWaitCloseLifecycle:
    """``VoiceTyperApp._open_config_file``::"""

    def test_full_lifecycle_dispatches_all_three_win32_calls(self, monkeypatch):
        """open() → wait() → close() calls ShellExecuteExW,"""
        mock_shell32 = MagicMock()
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.return_value = 0
        mock_kernel32.CloseHandle.return_value = 1
        _install_fake_windll(monkeypatch, shell32=mock_shell32, kernel32=mock_kernel32)

        def _side_effect(byref_obj):
            byref_obj._obj.hProcess = 0xCAFE
            return 1

        mock_shell32.ShellExecuteExW.side_effect = _side_effect

        # Mirror VoiceTyperApp._open_config_file's call sequence.
        handle = _windows_open_with_default_app(r"C:\config.json")
        assert handle == 0xCAFE
        try:
            _windows_wait_for_process_exit(handle)
        finally:
            _windows_close_process_handle(handle)

        mock_shell32.ShellExecuteExW.assert_called_once()
        mock_kernel32.WaitForSingleObject.assert_called_once_with(0xCAFE, _WAIT_TIMEOUT_MS)
        mock_kernel32.CloseHandle.assert_called_once_with(0xCAFE)

    def test_null_handle_skips_wait_and_close_per_contract(self, monkeypatch):
        """When ``open()`` returns ``None`` (no .json association),"""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 0  # failure
        mock_kernel32 = MagicMock()
        _install_fake_windll(monkeypatch, shell32=mock_shell32, kernel32=mock_kernel32)

        handle = _windows_open_with_default_app("foo.json")
        assert handle is None
        # Respect the None contract: don't call wait/close.
        mock_kernel32.WaitForSingleObject.assert_not_called()
        mock_kernel32.CloseHandle.assert_not_called()

    def test_close_is_called_even_if_wait_raises(self, monkeypatch):
        """If ``WaitForSingleObject`` raises, ``CloseHandle`` must"""
        mock_shell32 = MagicMock()
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.side_effect = OSError("wait failed")
        mock_kernel32.CloseHandle.return_value = 1
        _install_fake_windll(monkeypatch, shell32=mock_shell32, kernel32=mock_kernel32)

        def _side_effect(byref_obj):
            byref_obj._obj.hProcess = 0xBEEF
            return 1

        mock_shell32.ShellExecuteExW.side_effect = _side_effect

        handle = _windows_open_with_default_app(r"C:\config.json")
        assert handle == 0xBEEF
        try:
            _windows_wait_for_process_exit(handle)
        finally:
            _windows_close_process_handle(handle)

        mock_kernel32.CloseHandle.assert_called_once_with(0xBEEF)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
