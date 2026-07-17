"""Windows-specific platform launch helpers extracted from
``voice_typer/server/app.py`` (REF-3).

Re-exported from ``app.py`` so existing callers
(``VoiceTyperApp._open_config_file``) and tests that monkeypatch
``voice_typer.server.app._windows_open_with_default_app`` /
``_windows_wait_for_process_exit`` / ``_windows_close_process_handle`` /
``_systemroot_notepad_path`` keep working unchanged.

XPLAT-01 + SEC-audit-011: ``_windows_open_with_default_app`` uses
``ShellExecuteEx`` (not ``os.startfile``) so the caller can obtain a
process handle and block until the editor exits. The fallback path uses
``_systemroot_notepad_path`` to resolve a SystemRoot-validated Notepad
binary (never a bare PATH-resolved ``notepad``).
"""

import os
from pathlib import Path


def _windows_open_with_default_app(path: str):
    """Open *path* with the user's default app (association-respecting) and
    return a Win32 process HANDLE, or ``None`` if no association.

    Uses ``ShellExecuteEx`` with ``SEE_MASK_NOCLOSEPROCESS`` so we get a
    handle to wait on — unlike ``os.startfile`` which returns immediately
    with no handle (the cause of the old reload/lock regression). The
    caller must close the returned handle via
    :func:`_windows_close_process_handle`. Returns ``None`` on any failure
    (e.g. no ``.json`` association, or a non-Windows platform) so callers
    can fall back to a validated Notepad path.
    """
    try:
        import ctypes
        from ctypes.wintypes import (
            BOOL,
            DWORD,
            HANDLE,
            HINSTANCE,
            HKEY,
            HWND,
            LPCWSTR,
            ULONG,
        )

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ULONG),
                ("fMask", ULONG),
                ("hwnd", HWND),
                ("lpVerb", LPCWSTR),
                ("lpFile", LPCWSTR),
                ("lpParameters", LPCWSTR),
                ("lpDirectory", LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", LPCWSTR),
                ("hKeyClass", HKEY),
                ("dwHotKey", DWORD),
                ("hIconOrMonitor", HANDLE),
                ("hProcess", HANDLE),
            ]

        see_mask_nocloseprocess = 0x40
        sw_shownormal = 1
        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = see_mask_nocloseprocess
        sei.lpVerb = "open"
        sei.lpFile = path
        sei.nShow = sw_shownormal
        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
        shell32.ShellExecuteExW.restype = BOOL
        if not shell32.ShellExecuteExW(ctypes.byref(sei)):
            return None
        return sei.hProcess or None
    except Exception:
        return None


def _windows_wait_for_process_exit(handle) -> None:
    """Block until the process behind *handle* exits."""
    try:
        import ctypes
        from ctypes.wintypes import DWORD, HANDLE

        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        kernel32.WaitForSingleObject.restype = DWORD
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
    except Exception:
        pass


def _windows_close_process_handle(handle) -> None:
    """Close a process handle returned by ``_windows_open_with_default_app``."""
    try:
        import ctypes
        from ctypes.wintypes import BOOL, HANDLE

        kernel32 = ctypes.windll.kernel32
        kernel32.CloseHandle.argtypes = [HANDLE]
        kernel32.CloseHandle.restype = BOOL
        kernel32.CloseHandle(handle)
    except Exception:
        pass


def _systemroot_notepad_path():
    """Return the SystemRoot-validated Notepad path, or ``None``.

    SEC-audit-011: prefer ``%SYSTEMROOT%\\System32\\notepad.exe`` (existence
    checked), falling back to the canonical ``C:\\Windows\\System32\\notepad.exe``.
    Never resolves a bare ``notepad`` from PATH/cwd (which would be
    tamperable). Returns ``None`` only if neither location exists.
    """
    candidates = [
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "notepad.exe",
        Path(r"C:\Windows") / "System32" / "notepad.exe",
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None
