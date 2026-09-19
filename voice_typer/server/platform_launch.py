"""Windows-specific platform launch helpers extracted from
``voice_typer/server/app.py`` (REF-3).

Re-exported from ``app.py`` so existing callers
(``VoiceTyperApp._open_config_file``) and tests that monkeypatch
``voice_typer.server.app._windows_open_with_default_app`` /
``_windows_wait_for_process_exit`` / ``_windows_close_process_handle`` /
``_systemroot_notepad_path`` keep working unchanged.

+ SEC-audit-011: ``_windows_open_with_default_app`` uses
``ShellExecuteEx`` (not ``os.startfile``) so the caller can obtain a
process handle and block until the editor exits. The fallback path uses
``_systemroot_notepad_path`` to resolve a SystemRoot-validated Notepad
binary (never a bare PATH-resolved ``notepad``).
"""

import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)

# finite timeout for ``_windows_wait_for_process_exit``.
_WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS = 30 * 60 * 1000  # 30 minutes

# Win32 ``WaitForSingleObject`` return codes (subset relevant to the
_WAIT_OBJECT_0 = 0  # The specified object is signaled.
_WAIT_TIMEOUT = 0x00000102  # The time-out interval elapsed, object not signaled.
_WAIT_FAILED = 0xFFFFFFFF  # The function failed (call GetLastError).


def _windows_open_with_default_app(path: str):
    """Open *path* with the user's default app (association-respecting) and"""
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
    """Block until the process behind *handle* exits, or the finite"""
    try:
        import ctypes
        from ctypes.wintypes import DWORD, HANDLE

        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        kernel32.WaitForSingleObject.restype = DWORD
        # finite timeout: see ``_WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS``
        result = kernel32.WaitForSingleObject(handle, _WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS)
        if result == _WAIT_TIMEOUT:
            _log.warning(
                "[WIN32] WaitForSingleObject timed out after %d ms while "
                "waiting for launched editor to exit; returning control to "
                "caller (the editor process is still running, the user may "
                "need to close it manually).",
                _WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS,
            )
        elif result == _WAIT_FAILED:
            # WaitForSingleObject itself failed (e.g. invalid handle).
            _log.warning(
                "[WIN32] WaitForSingleObject returned WAIT_FAILED; the "
                "process handle may be invalid. Caller will proceed to "
                "CloseHandle."
            )
        # ``_WAIT_OBJECT_0`` (0) is the normal "process exited" case —
    except Exception:
        # Preserve the pre-fix contract: never raise (the broad
        _log.debug("[PLATFORM] WaitForSingleObject path failed", exc_info=True)


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
        # Never raise, the caller is best-effort cleaning up a
        _log.debug("[PLATFORM] CloseHandle failed", exc_info=True)


def _systemroot_notepad_path():
    """Return the SystemRoot-validated Notepad path, or ``None``.

    SEC-audit-011: prefer the canonical ``C:\\Windows\\System32\notepad.exe``
    """
    candidates = [
        # hardcoded OS path FIRST, never trust env-controlled
        Path(r"C:\Windows") / "System32" / "notepad.exe",
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "notepad.exe",
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None
