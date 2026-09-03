"""Windows Task Scheduler helpers shared with the autostart path.

Prewarm became a worker startup phase (master plan §6.2 P-1): the
OS-level scheduled-task registration for the prewarm binary (Windows
LogonTrigger / macOS LaunchAgent / Linux systemd user timer) was
deleted along with the prewarm binary it launched. The prewarm-
specific functions that used to live here (``register_prewarm_task``,
``unregister_prewarm_task``, ``is_prewarm_registered``,
``_build_task_xml``, the HKCU Run-key fallback helpers, the
``_prewarm_command`` interpreter resolver, and the
``prewarm_resolver`` / ``prewarm_scheduler_posix`` delegation paths)
were removed at the same time.

What REMAINS in this module is the small set of schtasks wrappers
that the autostart path (``server_platform/autostart.py`` /
``autostart_windows.py``) reuses:

- :data:`_APP_AUTOSTART_DELAY_SECONDS` — delay the autostart launcher
  waits before spawning Electron, so the just-launched app doesn't
  contend with the still-warming worker.
- :func:`is_supported` — True on Windows when ``schtasks.exe`` is
  present (gates the autostart_windows code paths).
- :func:`_schtasks` / :func:`_schtasks_elevated` — run
  ``schtasks`` non-elevated / via UAC elevation prompt (used by the
  autostart register / unregister / query / delete calls).
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys  # noqa: F401  — re-exported for tests that monkeypatch task_scheduler.sys.platform
import tempfile
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

# STARTUP-2: delay the app's autostart_launcher waits before spawning
# Electron, giving the worker a head start on warming the OS file
# cache (the worker calls ``warm_imports_for_worker`` once before
# accepting the first transcription request). Coded as a CLI flag so
# platform.py can pass it without depending on this module's
# internals.
_APP_AUTOSTART_DELAY_SECONDS = 15


# ─── schtasks wrappers ──────────────────────────────────────────────────


def _schtasks(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Run ``schtasks`` with *args*. Returns (returncode, combined output).

    ``schtasks /Create`` can block for up to 30s if the
    Windows Task Scheduler service is hung. This function is now called
    from a background thread (via ``_startup_parallel_work`` in app.py)
    so it doesn't block the main startup sequence.
    """
    cmd = ["schtasks"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=30,
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
    """Run ``schtasks`` with *args* via UAC elevation prompt.

    Used when a non-elevated schtasks call fails with "Access is denied"
    (e.g. overwriting a task created by an admin install).  Shows the
    standard Windows UAC consent dialog and waits for the user to accept
    or reject.

    Returns (returncode, combined_output).  If the user cancels UAC,
    the ShellExecuteExW fails and we return (1223, "user cancelled").
    """
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

    # build the arg string for schtasks using
    # ``subprocess.list2cmdline`` (the same helper ``subprocess.Popen``
    # uses on Windows internally). The previous hand-rolled join —
    # ``" ".join(f'"{a}"' if " " in a or "&" in a else a for a in args)``
    # — only quoted args containing a space or ``&`` and NEVER escaped
    # embedded ``"`` characters. A malicious or misconfigured arg
    # containing ``"`` could break out of the quoting and inject
    # arbitrary cmd.exe metacharacters into the ``cmd_line`` below
    # (which is then wrapped in another layer of ``cmd.exe /c "..."``
    # quoting via ``sei.lpParameters``). ``list2cmdline`` handles the
    # full Windows command-line quoting rules: it double-quotes any
    # arg containing whitespace, ``"``, or other special chars, and
    # escapes embedded ``"`` as ``\\"`` so the resulting string parses
    # back to the original argv on the cmd.exe side. ``schtasks`` args
    # today are all safe (task name, /Query, /TN, etc.), but the
    # function is a generic helper — hardening it removes a latent
    # injection vector if a future caller passes a user-supplied arg
    # (e.g. a custom ``--trigger`` value).
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
        # (which logs WARNING on ``FileNotFoundError`` and ERROR on
        # ``TimeoutExpired``). The elevated path previously had ZERO
        # log lines — a UAC-cancel or stale-temp-file failure was
        # silently swallowed, leaving the caller (e.g. an autostart
        # register / unregister flow) to retry blind or give up with
        # no diagnostic trail. Each failure mode now logs at the same
        # severity as the sibling helper per
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.WinError()
            log.warning(
                "[TASK] _schtasks_elevated: ShellExecuteExW failed (UAC declined or shell error) for args=%r: %s",
                args,
                err,
            )
            return 1223, f"UAC elevation failed: {err}"

        # Wait for the process to finish. ``WaitForSingleObject`` returns
        # WAIT_TIMEOUT (258) if the process didn't exit within
        # ``timeout_ms`` — surface that as a warning so a hung schtasks
        # doesn't look like a silent success.
        wait_result = ctypes.windll.kernel32.WaitForSingleObject(
            sei.hProcess,
            timeout_ms,
        )
        # check both documented non-success return values.
        # ``WAIT_TIMEOUT`` (258) means the process is still running
        # after ``timeout_ms`` — log.error so a hung schtasks is
        # visible. ``WAIT_FAILED`` (0xFFFFFFFF) means the wait itself
        # failed (e.g. ``sei.hProcess`` is invalid) — log.warning so
        # the failure is diagnosable before ``GetExitCodeProcess``
        # reads garbage. The finding's suggested ``WAIT_TIMEOUT=124``
        # is incorrect (124 is ETIMEDOUT, not a Win32 wait code); the
        # correct value is 258 (``STATUS_TIMEOUT`` = ``0x102``).
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
                "(handle invalid?) for args=%r — GetExitCodeProcess may return stale value",
                args,
            )

        exit_code = ctypes.wintypes.DWORD()
        # ``GetExitCodeProcess`` returns a BOOL (nonzero on
        # success, zero on failure). The previous call discarded the
        # return value, so a failure (e.g. invalid handle) silently
        # left ``exit_code`` at its zero-initialized value — the caller
        # saw ``rc=0`` (success) and treated a failed read as a
        # successful schtasks run. Now log the failure and fall
        # through with ``STILL_ACTIVE`` (259) sentinel so the caller's
        # ``rc != 0`` branch (which logs warning + retries) fires.
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
        # schtasks exited 0 but produced no stdout) is logged at debug
        # so a silent-success is distinguishable from a failed read.
        # An ``OSError`` here (temp file deleted by AV, permissions,
        # etc.) is logged at warning — same severity as the sibling
        # ``_schtasks`` uses for ``FileNotFoundError``.
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


# ─── Public API ──────────────────────────────────────────────────────────


def is_supported() -> bool:
    """Return True if Windows Task Scheduler (``schtasks.exe``) is available.

    Prewarm became a worker startup phase (master plan §6.2 P-1): the
    POSIX prewarm scheduling path (macOS LaunchAgent / Linux systemd
    user timer via ``prewarm_scheduler_posix``) was deleted along
    with the prewarm binary it launched. ``is_supported`` now only
    reports whether the Windows schtasks.exe binary exists — the
    POSIX path no longer needs a Task-Scheduler-style gate because
    the autostart code paths on POSIX use LaunchAgent / systemd
    directly (see ``server_platform/autostart_macos.py`` /
    ``autostart_linux.py``), not this helper.

    Returns:
        True on Windows when ``schtasks.exe`` is present, False
        otherwise.
    """
    if not is_windows():
        return False
    return Path(os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32\schtasks.exe").exists()
