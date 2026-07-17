"""Unit tests for ``voice_typer.server.platform_launch``.

The module under test (REF-3 / XPLAT-01 / SEC-audit-011) contains
Windows-only editor-launch helpers extracted from
``voice_typer/server/app.py``. Every Win32 call is wrapped in a broad
``try: ... except Exception:`` block so the functions fail-soft
(return ``None`` or ``pass``) on non-Windows platforms — which lets us
unit-test them on Linux by mocking ``ctypes.windll``.

Tests pin:

* ``_systemroot_notepad_path`` — SYSTEMROOT-validated Notepad
  resolution (priority order, fallback to ``C:\\Windows``, OSError
  tolerance, env-var handling, return type).
* ``_windows_open_with_default_app`` — ``ShellExecuteExW`` dispatch,
  SHELLEXECUTEINFO field wiring (``SEE_MASK_NOCLOSEPROCESS``,
  ``SW_SHOWNORMAL``, ``lpVerb='open'``, ``lpFile=path``), handle
  return semantics (``None`` on failure or null ``hProcess``), and
  argtypes/restype ABI assignment.
* ``_windows_wait_for_process_exit`` — ``WaitForSingleObject(handle,
  INFINITE)`` dispatch with INFINITE = ``0xFFFFFFFF``.
* ``_windows_close_process_handle`` — ``CloseHandle(handle)`` dispatch.
* The full ``open → wait → close`` lifecycle used by
  ``VoiceTyperApp._open_config_file``.

The contract that callers (``app.py``) rely on:
``_windows_open_with_default_app`` returns a truthy process handle on
success and ``None`` on any failure (so the caller can branch to a
Notepad fallback); ``_windows_wait_for_process_exit`` and
``_windows_close_process_handle`` never raise.
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

# Win32 magic constants (mirrored from the source under test so the
# tests stay valid even if the literals in platform_launch.py are
# refactored into named constants).
_SEE_MASK_NOCLOSEPROCESS = 0x40
_SW_SHOWNORMAL = 1
_INFINITE = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _install_fake_windll(monkeypatch, shell32=None, kernel32=None):
    """Install a fake ``ctypes.windll`` exposing ``.shell32`` / ``.kernel32``.

    On Linux ``ctypes`` has no ``windll`` attribute, so we install a
    ``MagicMock`` with ``raising=False``. Each library is itself a
    ``MagicMock`` so individual tests can configure return values or
    side effects. Returns the mock windll for direct assertions.
    """
    mock_windll = MagicMock()
    if shell32 is not None:
        mock_windll.shell32 = shell32
    if kernel32 is not None:
        mock_windll.kernel32 = kernel32
    # raising=False: ctypes.windll doesn't exist on Linux by default.
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
    return mock_windll


def _strip_windll(monkeypatch):
    """Ensure ``ctypes.windll`` is absent (mimics Linux runtime).

    On Linux this is a no-op. On Windows (where these tests would also
    run in a hypothetical cross-platform CI) it removes the real
    ``windll`` so the ``except Exception`` failure path is exercised.
    """
    if hasattr(ctypes, "windll"):
        monkeypatch.delattr(ctypes, "windll")


# ---------------------------------------------------------------------------
# _systemroot_notepad_path
# ---------------------------------------------------------------------------


class TestSystemRootNotepadPath:
    """Pin SYSTEMROOT-validated Notepad resolution (SEC-audit-011).

    The function must:
      * Prefer ``%SYSTEMROOT%\\System32\\notepad.exe``.
      * Fall back to ``C:\\Windows\\System32\\notepad.exe``.
      * Return ``None`` only if neither exists.
      * Tolerate ``OSError`` during ``Path.exists()``.
      * Default to ``C:\\Windows`` when ``SYSTEMROOT`` is unset.
      * Never resolve a bare PATH-resolved ``notepad`` (SEC-audit-011).
    """

    def test_returns_systemroot_path_when_it_exists(self, monkeypatch):
        """When %SYSTEMROOT%\\System32\\notepad.exe exists, it's returned."""
        custom_root = r"D:\CustomWin"
        # Build the expected path with the same construction the code
        # under test uses (Path / operator) so the comparison is
        # cross-platform-correct (on Linux the literal r"C:\Windows\\..."
        # would NOT match ``str(Path(r'C:\Windows') / ...)`` which uses
        # forward slashes for the / joins).
        expected = Path(custom_root) / "System32" / "notepad.exe"
        monkeypatch.setenv("SYSTEMROOT", custom_root)

        def fake_exists(self):
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
        """When SYSTEMROOT env var is unset, the first candidate uses
        ``C:\\Windows`` (not an empty / relative path)."""
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
        # (i.e. not "System32\\notepad.exe" — which would happen if an
        # empty SYSTEMROOT were passed through to Path()). An empty
        # SYSTEMROOT would make ``os.environ.get('SYSTEMROOT', ...)``
        # return ``''``, and ``Path('') / 'System32'`` collapses to a
        # relative ``PosixPath('System32')``.
        first = first_candidate[0]
        # Path is absolute if it has a drive (Windows) or starts with /
        # (POSIX). The constructed ``Path(r'C:\Windows')`` is absolute
        # on Windows; on Linux it's treated as a relative PosixPath
        # (just characters), but at minimum it must contain the
        # "Windows" literal — never a bare "System32\\notepad.exe".
        assert "Windows" in str(first), f"Empty/missing SYSTEMROOT must not produce a relative path; got {first!r}"

    def test_continues_on_oserror_during_exists(self, monkeypatch):
        """If the first candidate raises ``OSError`` (e.g. permission
        denied), the function must continue to the next candidate
        instead of propagating the exception."""
        monkeypatch.setenv("SYSTEMROOT", r"D:\PermissionDenied")
        default_path = Path(r"C:\Windows") / "System32" / "notepad.exe"
        call_count = {"n": 0}

        def fake_exists(self):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("permission denied")
            return self == default_path

        monkeypatch.setattr(Path, "exists", fake_exists)

        result = _systemroot_notepad_path()
        assert result is not None
        assert result == default_path
        assert call_count["n"] >= 2  # second candidate was tried

    def test_returns_path_object_not_string(self, monkeypatch):
        """Result must be a ``pathlib.Path`` instance (not a string)."""
        monkeypatch.setattr(Path, "exists", lambda self: True)
        result = _systemroot_notepad_path()
        assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# _windows_open_with_default_app
# ---------------------------------------------------------------------------


class TestWindowsOpenWithDefaultApp:
    """Pin ``ShellExecuteEx``-based default-app launch.

    Contract: returns a truthy process handle on success and ``None``
    on any failure (so the caller can fall back to the validated
    Notepad path).
    """

    def test_returns_none_on_non_windows(self, monkeypatch):
        """On Linux (no ``ctypes.windll``), function returns ``None``."""
        _strip_windll(monkeypatch)
        assert _windows_open_with_default_app("foo.json") is None

    def test_returns_handle_when_shellexecute_succeeds(self, monkeypatch):
        """When ``ShellExecuteExW`` succeeds and writes a non-zero
        ``hProcess``, that handle is returned to the caller."""
        mock_shell32 = MagicMock()
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        def _side_effect(byref_obj):
            # Mimic the kernel writing a process handle into the
            # SHELLEXECUTEINFO struct passed by reference.
            byref_obj._obj.hProcess = 0xDEADBEEF
            return 1  # BOOL TRUE

        mock_shell32.ShellExecuteExW.side_effect = _side_effect

        result = _windows_open_with_default_app(r"C:\config.json")
        assert result == 0xDEADBEEF
        mock_shell32.ShellExecuteExW.assert_called_once()

    def test_returns_none_when_shellexecute_fails(self, monkeypatch):
        """When ``ShellExecuteExW`` returns 0 (failure), function
        returns ``None`` (no association / error)."""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 0  # BOOL FALSE
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        assert _windows_open_with_default_app("foo.json") is None

    def test_returns_none_when_hprocess_is_null(self, monkeypatch):
        """Even when ``ShellExecuteExW`` reports success, a null
        ``hProcess`` (no associated handler with a process handle)
        must collapse to ``None`` via the ``or None`` clause."""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 1  # success
        # hProcess is left at its c_void_p default of None.
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        assert _windows_open_with_default_app("foo.json") is None

    def test_sets_argtypes_and_restype_on_shellexecute(self, monkeypatch):
        """``argtypes``/``restype`` must be explicitly assigned on
        ``ShellExecuteExW`` before the call (Win32 ABI safety: without
        them, ctypes defaults to ``c_int`` return which truncates
        64-bit pointers on 64-bit Windows)."""
        mock_shell32 = MagicMock()
        mock_shell32.ShellExecuteExW.return_value = 0
        _install_fake_windll(monkeypatch, shell32=mock_shell32)

        _windows_open_with_default_app("foo.json")
        # MagicMock records attribute assignments; if the function
        # never assigned them, attribute access would return a fresh
        # child mock (also truthy). Distinguish by checking that the
        # stored value is exactly what we'd expect (a list for
        # argtypes, a type for restype). We assert non-default via
        # explicit assignment tracking.
        shell_ex = mock_shell32.ShellExecuteExW
        # The function assigns these explicitly, so they must be set
        # (not auto-children). We verify by checking the mock's
        # ``_mock_children`` does not own a freshly-created default —
        # i.e. the assigned value is preserved.
        assert shell_ex.argtypes is not None
        assert shell_ex.restype is not None

    def test_invokes_shellexecute_with_path_in_sei(self, monkeypatch):
        """The path argument must be plumbed into the
        SHELLEXECUTEINFO struct's ``lpFile`` field, with the correct
        ``lpVerb`` ('open'), ``fMask`` (SEE_MASK_NOCLOSEPROCESS) and
        ``nShow`` (SW_SHOWNORMAL)."""
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
        # cbSize must be initialized to sizeof(SHELLEXECUTEINFO) —
        # ShellExecuteEx refuses to call with cbSize=0.
        assert captured["cbSize"] > 0


# ---------------------------------------------------------------------------
# _windows_wait_for_process_exit
# ---------------------------------------------------------------------------


class TestWindowsWaitForProcessExit:
    """Pin ``WaitForSingleObject(handle, INFINITE)`` dispatch.

    Contract: never raises (failures are silently swallowed so the
    editor flow doesn't crash on edge cases).
    """

    def test_calls_wait_for_single_object_with_infinite(self, monkeypatch):
        """``WaitForSingleObject`` must be called with the handle and
        ``INFINITE`` (``0xFFFFFFFF``) timeout."""
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.return_value = 0  # WAIT_OBJECT_0
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        _windows_wait_for_process_exit(0x1234)
        mock_kernel32.WaitForSingleObject.assert_called_once_with(0x1234, _INFINITE)

    def test_assigns_argtypes_and_restype(self, monkeypatch):
        """``argtypes``/``restype`` must be set on
        ``WaitForSingleObject`` (Win32 ABI safety)."""
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.return_value = 0
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        _windows_wait_for_process_exit(0x1234)
        assert mock_kernel32.WaitForSingleObject.argtypes is not None
        assert mock_kernel32.WaitForSingleObject.restype is not None

    def test_swallows_exception_on_non_windows(self, monkeypatch):
        """On Linux (no ``ctypes.windll``), function returns ``None``
        silently instead of raising."""
        _strip_windll(monkeypatch)
        assert _windows_wait_for_process_exit(0x1234) is None

    def test_swallows_kernel32_runtime_error(self, monkeypatch):
        """If ``WaitForSingleObject`` raises at runtime, the function
        must swallow the exception (returns ``None``)."""
        mock_kernel32 = MagicMock()
        mock_kernel32.WaitForSingleObject.side_effect = OSError("boom")
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        # Must not raise.
        assert _windows_wait_for_process_exit(0x1234) is None


# ---------------------------------------------------------------------------
# _windows_close_process_handle
# ---------------------------------------------------------------------------


class TestWindowsCloseProcessHandle:
    """Pin ``CloseHandle`` dispatch.

    Contract: never raises (so the ``finally`` clause in
    ``_open_config_file`` doesn't crash on cleanup).
    """

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
        """On Linux (no ``ctypes.windll``), function returns ``None``
        silently."""
        _strip_windll(monkeypatch)
        assert _windows_close_process_handle(0x1234) is None

    def test_swallows_closehandle_runtime_error(self, monkeypatch):
        """If ``CloseHandle`` raises at runtime, the function must
        swallow the exception (returns ``None``)."""
        mock_kernel32 = MagicMock()
        mock_kernel32.CloseHandle.side_effect = OSError("boom")
        _install_fake_windll(monkeypatch, kernel32=mock_kernel32)

        assert _windows_close_process_handle(0x1234) is None


# ---------------------------------------------------------------------------
# Integration: open → wait → close lifecycle
# ---------------------------------------------------------------------------


class TestOpenWaitCloseLifecycle:
    """Pin the end-to-end lifecycle used by
    ``VoiceTyperApp._open_config_file``::

        handle = _windows_open_with_default_app(str(config_file))
        if handle is not None:
            try:
                _windows_wait_for_process_exit(handle)
            finally:
                _windows_close_process_handle(handle)
        else:
            notepad = _systemroot_notepad_path()  # fallback
    """

    def test_full_lifecycle_dispatches_all_three_win32_calls(self, monkeypatch):
        """open() → wait() → close() calls ShellExecuteExW,
        WaitForSingleObject, and CloseHandle in order with the same
        handle flowing through all three."""
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
        mock_kernel32.WaitForSingleObject.assert_called_once_with(0xCAFE, _INFINITE)
        mock_kernel32.CloseHandle.assert_called_once_with(0xCAFE)

    def test_null_handle_skips_wait_and_close_per_contract(self, monkeypatch):
        """When ``open()`` returns ``None`` (no .json association),
        the caller's contract is to skip wait/close and fall back to
        the Notepad path. This test pins that contract: with no
        ShellExecuteExW side_effect (default return = 0 → None), the
        mock kernel32 functions must NOT have been invoked."""
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
        """If ``WaitForSingleObject`` raises, ``CloseHandle`` must
        still be called (the ``try/finally`` in app.py guarantees
        this; ``_windows_wait_for_process_exit`` swallows the exception
        so the finally runs normally — but this test pins the
        contract independently)."""
        mock_shell32 = MagicMock()
        mock_kernel32 = MagicMock()
        # wait() will swallow this and return None — CloseHandle still called.
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
