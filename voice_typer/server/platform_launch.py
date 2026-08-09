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
#
# Pre-fix, the function called ``WaitForSingleObject(handle, 0xFFFFFFFF)``
# (``INFINITE``). If the launched editor (e.g. Notepad opened for the
# config file) hangs — or the user walks away with the editor open —
# the calling thread blocked forever. The caller (``_open_config_file``
# in ``app.py``) holds the server's IPC thread, so a hung editor wedges
# the entire server: no further IPC requests are processed, the tray
# icon becomes unresponsive, and the user must kill the process.
#
# 30 minutes is a generous upper bound for "the user is actively
# editing a config file": it's long enough that no realistic edit
# session will expire it, and short enough that a forgotten-open
# editor doesn't wedge the server forever. The function still returns
# control to the caller if the timeout expires (rather than blocking
# indefinitely), logging a warning so the operator can diagnose.
#
# 1800000 ms = 30 minutes. Encoded as a literal (NOT ``0xFFFFFFFF``)
# so the value is self-documenting at the call site.
_WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS = 30 * 60 * 1000  # 30 minutes

# Win32 ``WaitForSingleObject`` return codes (subset relevant to the
# finite-timeout fix). See:
# https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject
_WAIT_OBJECT_0 = 0  # The specified object is signaled.
_WAIT_TIMEOUT = 0x00000102  # The time-out interval elapsed, object not signaled.
_WAIT_FAILED = 0xFFFFFFFF  # The function failed (call GetLastError).


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
    """Block until the process behind *handle* exits, or the finite
        timeout expires.

    pre-fix this function called ``WaitForSingleObject(handle,
        0xFFFFFFFF)`` (``INFINITE``). If the launched editor (e.g. Notepad
        opened for the config file) hangs — or the user walks away with
        the editor open — the calling thread blocked forever. The caller
        (``_open_config_file`` in ``app.py``) holds the server's IPC
        thread, so a hung editor wedged the entire server.

        The function now waits with a finite timeout (30 minutes). If the
        timeout expires (``WAIT_TIMEOUT``), the function logs a warning
        and returns control to the caller rather than blocking forever.
        The function still never raises (the broad ``except Exception``
        is preserved so the editor flow doesn't crash on edge cases).
    """
    try:
        import ctypes
        from ctypes.wintypes import DWORD, HANDLE

        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
        kernel32.WaitForSingleObject.restype = DWORD
        # finite timeout — see ``_WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS``
        # comment for the rationale.
        result = kernel32.WaitForSingleObject(handle, _WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS)
        if result == _WAIT_TIMEOUT:
            _log.warning(
                "[WIN32] WaitForSingleObject timed out after %d ms while "
                "waiting for launched editor to exit; returning control to "
                "caller (the editor process is still running — the user may "
                "need to close it manually).",
                _WAIT_FOR_PROCESS_EXIT_TIMEOUT_MS,
            )
        elif result == _WAIT_FAILED:
            # WaitForSingleObject itself failed (e.g. invalid handle).
            # Don't raise — the caller's contract is "never raise" — but
            # log a warning so the failure is diagnosable.
            _log.warning(
                "[WIN32] WaitForSingleObject returned WAIT_FAILED; the "
                "process handle may be invalid. Caller will proceed to "
                "CloseHandle."
            )
        # ``_WAIT_OBJECT_0`` (0) is the normal "process exited" case —
        # no log needed. Other positive values (WAIT_ABANDONED etc.)
        # are not expected for process handles and are silently
        # ignored to preserve the "never raise" contract.
    except Exception:
        # Preserve the pre-fix contract: never raise (the broad
        # ``except Exception`` is intentional so the editor flow
        # doesn't crash on edge cases — Win32 process-handle ops can
        # raise ``OSError``, ``AttributeError`` (ctypes config drift),
        # or Windows-specific exception types). Log at debug so the
        # failure is diagnosable without surfacing to the user.
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
        # Never raise — the caller is best-effort cleaning up a
        # process handle. Win32 ``CloseHandle`` can raise ``OSError``
        # on an invalid handle or ``AttributeError`` on ctypes config
        # drift. Log at debug so the failure is diagnosable.
        _log.debug("[PLATFORM] CloseHandle failed", exc_info=True)


def _systemroot_notepad_path():
    """Return the SystemRoot-validated Notepad path, or ``None``.

        SEC-audit-011: prefer the canonical ``C:\\Windows\\System32\\notepad.exe``
        (existence checked), falling back to ``%SYSTEMROOT%\\System32\\notepad.exe``
        for non-standard Windows installs. Never resolves a bare ``notepad``
        from PATH/cwd (which would be tamperable). Returns ``None`` only if
        neither location exists.

    the candidate order is REVERSED from the historical
        ``%SYSTEMROOT%``-first order. The previous order trusted
        ``os.environ.get("SYSTEMROOT")`` first, then fell back to the
        hardcoded ``C:\\Windows``. An attacker (or a misconfigured parent
        process) setting ``SYSTEMROOT=C:\\Users\\attacker`` could trick this
        helper into returning ``C:\\Users\\attacker\\System32\\notepad.exe``
        (an attacker-controlled binary) AS LONG AS that file existed on
        disk — which is trivial for an attacker who already controls
        ``SYSTEMROOT``. The hardcoded ``C:\\Windows\\System32\\notepad.exe``
        is the OS-installed Notepad (shipped with every Windows install
        since Windows NT); preferring it FIRST closes the trust gap. The
        ``%SYSTEMROOT%`` candidate is kept as a fallback for non-standard
        Windows installs where the system root is on a different drive
        (e.g. ``D:\\Windows``) — in that scenario, the attacker would
        ALSO need write access to the SYSTEMROOT path, which is a strictly
        higher privilege bar than setting an env var.
    """
    candidates = [
        # hardcoded OS path FIRST — never trust env-controlled
        # SYSTEMROOT ahead of the canonical install location.
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
