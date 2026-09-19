"""Windows Task Scheduler helpers (autostart order C-CROSS-2)."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys  # noqa: F401, re-exported for tests that monkeypatch task_scheduler.sys.platform
import tempfile
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

# STARTUP-2: delay the app's autostart_launcher waits before spawning
_APP_AUTOSTART_DELAY_SECONDS = 3


def _schtasks(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Run ``schtasks`` with *args*. Returns (returncode, combined output)."""
    cmd = ["schtasks"] + args
    try:
        # Hidden spawn on Windows: schtasks.exe is a console-subsystem
        run_kwargs: dict = {
            "capture_output": capture,
            "text": True,
            "timeout": 30,
        }
        if is_windows():
            from voice_typer.server.server_platform.autostart import (
                _windows_create_no_window_flags,
            )

            run_kwargs["creationflags"] = _windows_create_no_window_flags()
        result = subprocess.run(
            cmd,
            **run_kwargs,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, output
    except FileNotFoundError:
        log.warning("[TASK] schtasks.exe not found (not Windows?)")
        return 127, "schtasks not found"
    except subprocess.TimeoutExpired:
        log.exception("[TASK] schtasks timed out: %s", cmd)
        return 124, "schtasks timed out"


def _schtasks_elevated(args: list[str], *, timeout_ms: int = 60000) -> tuple[int, str]:
    """Run ``schtasks`` with *args* via UAC elevation prompt."""
    import ctypes
    import ctypes.wintypes

    class SHELLEXECUTEINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.wintypes.DWORD),
            ("fMask", ctypes.wintypes.ULONG),
            ("hwnd", ctypes.wintypes.HWND),
            ("lpVerb", ctypes.wintypes.LPCWSTR),
            ("lpFile", ctypes.wintypes.LPCWSTR),
            ("lpParameters", ctypes.wintypes.LPCWSTR),
            ("lpDirectory", ctypes.wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.wintypes.HINSTANCE),
            ("lpIDList", ctypes.wintypes.LPVOID),
            ("lpClass", ctypes.wintypes.LPCWSTR),
            ("hKeyClass", ctypes.wintypes.HKEY),
            ("dwHotKey", ctypes.wintypes.DWORD),
            ("hMonitor", ctypes.wintypes.HANDLE),
            ("hProcess", ctypes.wintypes.HANDLE),
        ]

    see_mask_noclose = 0x00000040
    sw_hide = 0

    # ``subprocess.list2cmdline`` (the same helper ``subprocess.Popen``
    # quoting via ``sei.lpParameters``). ``list2cmdline`` handles the
    arg_str = subprocess.list2cmdline(args)

    # Redirect output to a temp file so we can read it back
    with tempfile.NamedTemporaryFile(mode="w+t", suffix=".txt", delete=False, encoding="utf-8") as out_file:
        out_path = out_file.name

    try:
        # Launch via cmd.exe /c with redirection so we capture output
        cmd_line = f'schtasks {arg_str} > "{out_path}" 2>&1'
        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = see_mask_noclose
        sei.lpVerb = "runas"
        sei.lpFile = "cmd.exe"
        sei.lpParameters = f'/c "{cmd_line}"'
        sei.nShow = sw_hide

        # parity with the non-elevated ``_schtasks`` helper
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.WinError()
            log.warning(
                "[TASK] _schtasks_elevated: ShellExecuteExW failed (UAC declined or shell error) for args=%r: %s",
                args,
                err,
            )
            return 1223, f"UAC elevation failed: {err}"

        # Wait for the process to finish. ``WaitForSingleObject`` returns
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(
            sei.hProcess,
            timeout_ms,
        )
        # check both documented non-success return values.
        if wait_result == 258:  # WAIT_TIMEOUT
            log.error(
                "[TASK] _schtasks_elevated: WaitForSingleObject timed out after "
                "%dms (schtasks may still be running) for args=%r",
                timeout_ms,
                args,
            )
        elif wait_result == 0xFFFFFFFF:  # WAIT_FAILED
            log.warning(
                "[TASK] _schtasks_elevated: WaitForSingleObject returned WAIT_FAILED "
                "(handle invalid?) for args=%r. GetExitCodeProcess may return stale value",
                args,
            )

        exit_code = ctypes.wintypes.DWORD()
        # ``GetExitCodeProcess`` returns a BOOL (nonzero on
        get_exit_ok = ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
        if not get_exit_ok:
            log.warning(
                "[TASK] _schtasks_elevated: GetExitCodeProcess failed for args=%r "
                "(handle may be invalid); treating as STILL_ACTIVE (259)",
                args,
            )
            exit_code.value = 259  # STILL_ACTIVE
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

        # Read output from the temp file. The empty-output case (e.g.
        output = ""
        try:
            with open(out_path, encoding="utf-8") as f:
                output = f.read()
        except OSError as e:
            log.warning(
                "[TASK] _schtasks_elevated: could not read schtasks output file %s: %s",
                out_path,
                e,
            )
        else:
            if not output:
                log.debug(
                    "[TASK] _schtasks_elevated: schtasks produced empty output for args=%r (exit_code=%d)",
                    args,
                    exit_code.value,
                )

        if exit_code.value != 0:
            log.warning(
                "[TASK] _schtasks_elevated: schtasks exited %d for args=%r (output: %r)",
                exit_code.value,
                args,
                output[:200],
            )
        else:
            log.debug(
                "[TASK] _schtasks_elevated: schtasks exited 0 for args=%r",
                args,
            )

        return exit_code.value, output

    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)


def is_supported() -> bool:
    """Return True if Windows Task Scheduler (``schtasks.exe``) is available."""
    if not is_windows():
        return False
    return Path(os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32\schtasks.exe").exists()
