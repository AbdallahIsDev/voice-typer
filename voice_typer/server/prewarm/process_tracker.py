# ARCH-045 / SPLIT-4: extracted from the original ``prewarm.py`` god-module.
"""PID-file + process-liveness + status-query helpers.

Phase 4.5 / ARCH-045 — this module holds the helpers that track whether
a prewarm process is currently running, wait for it to finish, spawn a
detached background prewarm, and report cache status to the UI:

- :func:`_write_pid_file` / :func:`_remove_pid_file` — write/remove the
  prewarm PID file (read by :func:`is_prewarm_running`).
- :func:`_process_alive` — cross-platform process-liveness check.
- :func:`_read_process_cmdline_windows` /
  :func:`_read_process_cmdline_windows_wmi` — read another Windows
  process's command line via the PEB walk + WMI fallback.
- :func:`_process_is_prewarm` — best-effort check that a PID is actually
  prewarm (PID-recycling guard).
- :func:`is_prewarm_running` — public API: is prewarm running right now?
- :func:`wait_for_prewarm` — block until prewarm finishes (event-based
  on Windows/Linux, poll fallback elsewhere).
- :func:`spawn_background_prewarm` — launch a detached prewarm subprocess.
- :func:`get_prewarm_status` — return a snapshot of the prewarm cache
  state for the About-page UI.
- :func:`_read_prewarm_pid` — read the live prewarm PID from the PID file.

Patch-path compatibility
------------------------
Tests patch ``_pid_file_path``, ``_process_is_prewarm``,
``is_prewarm_running``, ``_wait_for_completion_event``,
``_sentinel_path``, ``_active_model_cache_dirs``, ``_cache_ratio``,
and ``is_windows`` on the package namespace.  Callers in this module
look those up via ``_pkg.X()`` at call time so the patches take effect.

``inspect.getsource`` compatibility
-----------------------------------
Every function here is genuinely defined in this file, so
``inspect.getsource(prewarm._read_process_cmdline_windows)`` etc. keep
working.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

# Patch-path bridge: route lookups of cross-submodule helpers through
# the package namespace so test patches of the form
# ``monkeypatch.setattr(prewarm, "_pid_file_path", ...)`` /
# ``monkeypatch.setattr(prewarm, "_process_is_prewarm", ...)`` /
# ``monkeypatch.setattr(prewarm, "is_prewarm_running", ...)`` /
# ``monkeypatch.setattr(prewarm, "_wait_for_completion_event", ...)`` /
# ``monkeypatch.setattr(prewarm, "_sentinel_path", ...)`` /
# ``monkeypatch.setattr(prewarm, "_active_model_cache_dirs", ...)`` /
# ``monkeypatch.setattr(prewarm, "_cache_ratio", ...)`` /
# ``monkeypatch.setattr(prewarm, "is_windows", ...)``
# keep affecting production code defined here.
from voice_typer.server import prewarm as _pkg
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.prewarm")


# ─── PID file + process-liveness helpers (ADR-0009 Issue 4) ───────────────


def _write_pid_file() -> None:
    """Write the current PID to the prewarm PID file.

    Called at the start of the warming phase (after all early-exit
    guards). The app's ``is_prewarm_running()`` polls this file to
    decide whether to wait for prewarm to finish before loading the
    model. ``_remove_pid_file()`` removes it in a finally block.
    """
    try:
        from voice_typer.server.config import _secure_atomic_write

        pid_file = _pkg._pid_file_path()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        _secure_atomic_write(pid_file, str(os.getpid()))
    except OSError as exc:
        log.debug("[PREWARM] could not write PID file: %s", exc)


def _remove_pid_file() -> None:
    """Remove the prewarm PID file on exit.

    Idempotent: ``missing_ok=True`` so this is safe to call even if
    ``_write_pid_file()`` never ran (e.g. run() bailed out before the
    warming phase).
    """
    try:
        _pkg._pid_file_path().unlink(missing_ok=True)
    except OSError as exc:
        log.debug("[PREWARM] could not remove PID file: %s", exc)


def _process_alive(pid: int) -> bool:
    """Return True if the process with ``pid`` is currently running.

    Cross-platform:
      - Windows: ``OpenProcess`` + ``GetExitCodeProcess`` (STILL_ACTIVE=259)
      - POSIX: ``os.kill(pid, 0)`` (raises OSError if the process is dead)

    ADR-0009 Issue 4: used by ``is_prewarm_running()`` to check the PID
    file. Treating "process does not exist" as "not running" (rather
    than raising) is intentional — a stale PID file pointing at a
    recycled PID is a known failure mode, and the worst case is that
    the app skips the wait and loads the model from a possibly-cold
    cache, which is exactly the pre-ADR-0009 behavior.
    """
    if pid <= 0:
        return False
    if is_windows():
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            process_query_limited_information = 0x1000
            STILL_ACTIVE = 259  # noqa: N806
            handle = kernel32.OpenProcess(
                process_query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except OSError:
            return False
        except Exception:
            log.debug("[PREWARM] Windows process liveness check failed", exc_info=True)
            return False
    else:
        import errno

        try:
            os.kill(pid, 0)
            return True
        except OSError as exc:
            # ESRCH = no such process → not alive.
            # EPERM = process exists but not ours → treat as alive
            # (the prewarm process is owned by the same user, so EPERM
            # shouldn't happen in practice; treating it as alive is
            # the safe default — the worst case is we wait for a
            # prewarm that already finished, which is bounded by the
            # timeout in wait_for_prewarm()).
            if exc.errno == errno.ESRCH:
                return False
            if exc.errno == errno.EPERM:
                # XPLAT-05: log a warning so the user can diagnose if
                # an unexpected EPERM is causing wait_for_prewarm() to
                # block for the full 60s timeout. Best-effort check if
                # the PID is owned by the same user (Linux only, via
                # /proc).
                _uid = os.getuid()
                try:
                    _st = os.stat(f"/proc/{pid}")
                    if _st.st_uid == _uid:
                        log.debug(
                            "[PREWARM] PID %d is owned by us but EPERM on kill(0) — treating as alive",
                            pid,
                        )
                    else:
                        log.warning(
                            "[PREWARM] PID %d exists but is owned by "
                            "UID %d (not us, UID=%d) — treating as "
                            "alive for 60s timeout",
                            pid,
                            _st.st_uid,
                            _uid,
                        )
                except OSError:
                    log.warning(
                        "[PREWARM] could not verify PID %d ownership "
                        "(EPERM on kill) — treating as alive for 60s "
                        "timeout",
                        pid,
                    )
                return True
            return False


def _read_process_cmdline_windows(pid: int) -> str | None:
    """Read the full command line of another Windows process, no elevation.

    Tasks 1+2: the previous Windows check used QueryFullProcessImageNameW
    and only verified the image was python.exe — which means ANY Python
    process (including pytest) passed the "is prewarm" check, causing
    the PID recycling guard to fail on Windows.

    This implementation walks the target process's PEB
    (Process Environment Block) to read
    RTL_USER_PROCESS_PARAMETERS.CommandLine, the same technique Task
    Manager and Process Explorer use. It works without elevation for
    processes owned by the same user (which is always the case for
    prewarm — it's launched by the user's scheduled task).

    Architecture:
      1. OpenProcess(process_query_limited_information | process_vm_read).
         Both flags are granted to same-user processes without elevation.
      2. NtQueryInformationProcess(ProcessBasicInformation) → PROCESS_BASIC_INFORMATION.
         This gives us the PEB address inside the target's address space.
      3. ReadProcessMemory(PEB) → read the ProcessParameters pointer.
      4. ReadProcessMemory(RTL_USER_PROCESS_PARAMETERS) → read the
         CommandLine UNICODE_STRING (Length, Buffer pointer).
      5. ReadProcessMemory(Buffer) → read the UTF-16 command line.

    Falls back to WMI (powershell Get-CimInstance Win32_Process) if the
    PEB walk fails (protected process, PEB paged out, 32-bit/64-bit
    mismatch, etc.). WMI is slower (~200ms) but works in all cases
    where the caller has at least PROCESS_QUERY_LIMITED_INFORMATION.

    Returns the command line as a UTF-8 string, or None if it can't be
    read (process dead, access denied, all methods failed).
    """
    import ctypes
    from ctypes import wintypes

    # Task 1: ULONG_PTR is not in Python's ctypes.wintypes module. The
    # Windows SDK defines ULONG_PTR as UINT_PTR (pointer-sized unsigned
    # int). In ctypes, wintypes.WPARAM IS defined as UINT_PTR, so it has
    # the correct size (8 bytes on 64-bit, 4 bytes on 32-bit). Using
    # c_size_t would also work but WPARAM is the semantically correct
    # match for the SDK's ULONG_PTR type. (The previous code used
    # wintypes.ULONG_PTR which doesn't exist, crashing on Windows.)
    ulong_ptr = wintypes.WPARAM

    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll

    # ── Struct definitions ──────────────────────────────────────────
    # N801: class names follow CapWords per ruff; the Win32 SDK names
    # (_UNICODE_STRING, _PROCESS_BASIC_INFORMATION) are preserved in
    # comments for anyone cross-referencing the Microsoft docs.
    class UnicodeString(ctypes.Structure):
        """NT UNICODE_STRING — length-prefixed UTF-16 string pointer."""

        _fields_ = [
            ("Length", wintypes.USHORT),  # bytes, excluding NUL
            ("MaximumLength", wintypes.USHORT),  # bytes, including NUL
            ("Buffer", wintypes.LPWSTR),  # pointer into target's memory
        ]

    class ProcessBasicInformation(ctypes.Structure):
        """NtQueryInformationProcess output for ProcessBasicInformation.

        Field types match the Windows SDK PROCESS_BASIC_INFORMATION:
          ExitStatus              NTSTATUS (LONG)
          PebBaseAddress          PPEB (pointer)
          AffinityMask            ULONG_PTR
          BasePriority            KPRIORITY (LONG)
          UniqueProcessId         HANDLE_PTR (ULONG_PTR)
          InheritedFromUniqueProcessId ULONG_PTR
        """

        _fields_ = [
            ("ExitStatus", wintypes.LONG),  # NTSTATUS
            ("PebBaseAddress", wintypes.LPVOID),  # PEB* inside target's memory
            ("AffinityMask", ulong_ptr),
            ("BasePriority", wintypes.LONG),
            ("UniqueProcessId", ulong_ptr),
            ("InheritedFromUniqueProcessId", ulong_ptr),
        ]

    # ── Function signatures (best practice: set argtypes/restype) ───
    process_query_limited_information = 0x1000
    process_vm_read = 0x0010

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]

    # NtQueryInformationProcess is in ntdll, not kernel32.
    # PROCESSINFOCLASS.ProcessBasicInformation = 0 (Win32 SDK enum value).
    process_info_class_basic = 0
    ntdll.NtQueryInformationProcess.restype = wintypes.LONG  # NTSTATUS
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]

    handle = kernel32.OpenProcess(
        process_query_limited_information | process_vm_read,
        False,
        pid,
    )
    if not handle:
        return None  # access denied or process dead
    try:
        # ── Step 1: NtQueryInformationProcess → PEB address ────────
        pbi = ProcessBasicInformation()
        returned = wintypes.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            handle,
            process_info_class_basic,
            ctypes.byref(pbi),
            ctypes.sizeof(pbi),
            ctypes.byref(returned),
        )
        # NTSTATUS >= 0 means success (0 = STATUS_SUCCESS, >0 = informational)
        if status < 0 or not pbi.PebBaseAddress:
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        peb_addr = pbi.PebBaseAddress

        # ── Step 2: Read PEB → ProcessParameters pointer ───────────
        # On 64-bit Windows, ProcessParameters is at PEB offset 0x20.
        # On 32-bit Windows, it's at PEB offset 0x10. We detect the
        # pointer size via sizeof(ulong_ptr) (8 on 64-bit, 4 on 32-bit).
        # These offsets are stable across Win10/Win11 — the PEB layout
        # hasn't changed since Windows 7.
        is_64bit = ctypes.sizeof(ulong_ptr) == 8
        params_offset = 0x20 if is_64bit else 0x10

        params_ptr = wintypes.LPVOID()
        bytes_read = ctypes.c_size_t(0)
        # PYREFLY-FIX: ctypes.cast(...).value returns Optional[int]; add an
        # explicit None guard so the arithmetic is type-safe. If the cast
        # somehow returns None (shouldn't happen for a valid PEB address,
        # but pyrefly can't prove it), fall back to WMI.
        peb_val = ctypes.cast(peb_addr, ctypes.c_void_p).value
        if peb_val is None:
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        if not kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(peb_val + params_offset),
            ctypes.byref(params_ptr),
            ctypes.sizeof(params_ptr),
            ctypes.byref(bytes_read),
        ):
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        if not params_ptr.value:
            return _read_process_cmdline_windows_wmi(pid)  # fallback

        # ── Step 3: Read RTL_USER_PROCESS_PARAMETERS → CommandLine ─
        # CommandLine is a UNICODE_STRING. Its offset within
        # RTL_USER_PROCESS_PARAMETERS is 0x70 on 64-bit, 0x40 on 32-bit.
        cmd_offset = 0x70 if is_64bit else 0x40
        cmd_unicode = UnicodeString()
        params_val = ctypes.cast(params_ptr, ctypes.c_void_p).value
        if params_val is None:
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        if not kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(params_val + cmd_offset),
            ctypes.byref(cmd_unicode),
            ctypes.sizeof(cmd_unicode),
            ctypes.byref(bytes_read),
        ):
            return _read_process_cmdline_windows_wmi(pid)  # fallback

        # ── Step 4: Read the actual command-line string ────────────
        if cmd_unicode.Length == 0 or not cmd_unicode.Buffer:
            return ""  # process has no command line (rare)
        # Length is in bytes; UTF-16 = 2 bytes per char. Read into a
        # wchar array sized to the char count + 1 (NUL terminator).
        char_count = cmd_unicode.Length // 2
        buf = ctypes.create_unicode_buffer(char_count + 1)
        if not kernel32.ReadProcessMemory(
            handle,
            cmd_unicode.Buffer,
            buf,
            cmd_unicode.Length,
            ctypes.byref(bytes_read),
        ):
            return _read_process_cmdline_windows_wmi(pid)  # fallback
        return buf.value
    except OSError:
        return None
    except Exception:
        log.debug("[PREWARM] Windows PEB walk failed", exc_info=True)
        return _read_process_cmdline_windows_wmi(pid)  # fallback
    finally:
        kernel32.CloseHandle(handle)


def _read_process_cmdline_windows_wmi(pid: int) -> str | None:
    """WMI fallback for reading a Windows process's command line.

    Used when the PEB walk (_read_process_cmdline_windows) fails (e.g.
    protected process, PEB paged out, 32/64-bit mismatch). Spawns a
    powershell subprocess (~200ms) to query Get-CimInstance Win32_Process.

    Returns the command line string, or None if WMI fails.
    """
    try:
        # Using Get-CimInstance instead of the deprecated wmic CLI.
        # -Filter avoids fetching all processes (faster, less memory).
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            cmdline = result.stdout.strip()
            return cmdline if cmdline else None
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    except Exception:
        log.debug("[PREWARM] Windows WMI command-line read failed", exc_info=True)
        return None


def _process_is_prewarm(pid: int) -> bool:
    """Best-effort check that ``pid`` is actually a prewarm process.

    ADR-0009 Issue 4 (review fix H4) + Tasks 1+2: after prewarm exits
    normally (PID file removed by finally) or is killed
    (SIGKILL/TerminateProcess — finally doesn't run), the OS may recycle
    the PID for an unrelated process. Without this check,
    ``is_prewarm_running()`` returns True for the unrelated process, and
    ``wait_for_prewarm()`` blocks the model load for the full 60s
    timeout on every app launch until the unrelated process exits.

    Detection is best-effort but cross-platform consistent:
      - Linux: read /proc/{pid}/cmdline and check for "prewarm" +
        "voice_typer".
      - macOS: use ``ps -o command= -p {pid}`` (no /proc on macOS).
      - Windows (Tasks 1+2): walk the target process's PEB via
        NtQueryInformationProcess + ReadProcessMemory to read the actual
        command line, then check for "prewarm" + "voice_typer". Falls
        back to WMI (powershell Get-CimInstance) if the PEB walk fails.
        This is the same technique Task Manager/Process Explorer use and
        works without elevation for same-user processes. The previous
        coarse image-name check (only verified "python" was in the image
        path) could not distinguish prewarm from pytest or any other
        Python process, causing test_is_prewarm_running_pid_recycled to
        fail on Windows.

    Returns True if the process looks like prewarm, False if it doesn't
    (or if the check fails — fail-safe toward "not prewarm" so the stale
    PID file gets cleaned up).
    """
    if pid <= 0:
        return False
    # ── Linux: /proc/{pid}/cmdline ─────────────────────────────────
    if is_linux():
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            cmdline_str = cmdline.replace(b"\x00", b" ").decode("utf-8", "ignore")
            return "prewarm" in cmdline_str and "voice_typer" in cmdline_str
        except OSError:
            return False
    # ── macOS: ps ──────────────────────────────────────────────────
    if is_macos():
        try:
            result = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            cmdline = result.stdout
            return "prewarm" in cmdline and "voice_typer" in cmdline
        except (OSError, subprocess.TimeoutExpired):
            return False
    # ── Windows: PEB walk + WMI fallback (Tasks 1+2) ───────────────
    if is_windows():
        cmdline = _read_process_cmdline_windows(pid)
        if cmdline is None:
            # Couldn't read the command line — fail safe (treat as not
            # prewarm so the stale PID file gets cleaned up). This is
            # the correct default: if we can't verify the process IS
            # prewarm, we shouldn't block the app for 60s.
            return False
        return "prewarm" in cmdline and "voice_typer" in cmdline
    return False


def is_prewarm_running() -> bool:
    """Return True if a prewarm process is currently running.

    ADR-0009 Issue 4: checks for the prewarm PID file written by the
    prewarm process at startup. If the PID file exists and the process
    is alive AND looks like prewarm (review fix H4: PID recycling
    guard), prewarm is running. If the PID file is missing, points at a
    dead process, or points at a recycled unrelated process, prewarm is
    not running and the stale PID file is cleaned up.

    Safe to call from any thread (the app's model_manager.try_load()
    calls this from a daemon thread).

    CR-12 (Low, informational): there is an inherent TOCTOU race window
    between this check and any subsequent action that depends on the
    result. Between the moment ``is_prewarm_running()`` returns True
    and the caller acts on that, the prewarm process may exit (so
    ``wait_for_prewarm()`` then sees the PID file gone and returns
    True immediately — harmless), or a new prewarm process may start
    and overwrite the PID file (so the caller's snapshot is stale —
    also harmless, since ``wait_for_prewarm()`` re-reads the PID file
    before waiting). The residual race that is NOT closeable without a
    PID-file lock is: prewarm exits AND the OS recycles its PID for an
    unrelated process between our ``_process_alive(pid)`` check and
    our ``_process_is_prewarm(pid)`` check. In that case we may
    falsely return True (the recycled process briefly looks like
    prewarm) — but ``_process_is_prewarm``'s "voice_typer" +
    "prewarm" cmdline match makes this practically impossible. No
    code change is needed; the race is theoretical and the worst case
    is a spurious 60s wait that the caller can interrupt.
    """
    pid_file = _pkg._pid_file_path()
    if not pid_file.exists():
        return False
    try:
        pid_text = pid_file.read_text().strip()
        pid = int(pid_text)
    except (ValueError, OSError):
        return False
    if not _process_alive(pid):
        return False
    # H4: the PID is alive, but is it actually prewarm? If the OS
    # recycled the PID for an unrelated process, treat the PID file as
    # stale and clean it up so the next wait_for_prewarm() doesn't block.
    if not _pkg._process_is_prewarm(pid):
        log.info(
            "[PREWARM] PID file points at pid %d which is not prewarm (PID recycled) — removing stale PID file",
            pid,
        )
        # Routed via _pkg so tests that patch ``prewarm._remove_pid_file``
        # (e.g. test_is_prewarm_running_pid_recycled) see the patched
        # value.
        _pkg._remove_pid_file()
        return False
    return True


def wait_for_prewarm(timeout_s: float = 60.0) -> bool:
    """Wait for prewarm to finish if it's running.

    CPU-04: Uses event-based notification instead of polling:
      - Windows: ``WaitForSingleObject`` on a named event (zero-CPU kernel wait).
      - Linux: ``pidfd_open`` + ``select.poll()`` (fd-based wait).
      - Fallback (macOS / old kernel): degraded 1s polling (60 polls max).

    Returns True if prewarm completed (or wasn't running), False if the
    timeout was reached.

    Called by ``model_manager.try_load()`` before loading the model so
    the app doesn't fight prewarm for disk I/O when the user logs in
    faster than prewarm can warm the cache.

    Task 5: when this returns False (timeout), the caller should call
    ``spawn_background_prewarm()`` to ensure prewarm restarts for the
    next app launch (the current boot's prewarm was preempted by the
    app's model load and may not have finished warming).
    """
    if not _pkg.is_prewarm_running():
        return True  # nothing to wait for

    log.info(
        "[PREWARM] waiting for prewarm to finish (timeout=%.0fs)",
        timeout_s,
    )

    # CPU-04: try event-based wait first (zero-CPU on Windows, fd-based on Linux).
    # If it returns True, prewarm signaled completion within the timeout.
    # If it returns False, either the platform doesn't support event-based
    # waiting or the wait timed out — fall back to the degraded 1s poll loop,
    # but consume only the REMAINING budget so the total wait never exceeds
    # timeout_s (the event wait already spent up to the full timeout).
    wait_start = time.perf_counter()
    if _pkg._wait_for_completion_event(timeout_s):
        return True

    deadline = wait_start + timeout_s
    while time.perf_counter() < deadline:
        time.sleep(1.0)  # CPU-04: reduced from 500ms to 1s (60 polls max)
        if not _pkg.is_prewarm_running():
            log.info("[PREWARM] prewarm finished -- proceeding")
            return True

    log.warning(
        "[PREWARM] prewarm still running after %.0fs -- proceeding anyway",
        timeout_s,
    )
    return False


def spawn_background_prewarm(force: bool = True, trigger: str = "manual") -> int | None:
    """Spawn a detached prewarm subprocess for the next app launch.

    Task 5: when ``wait_for_prewarm()`` times out (prewarm is still
    running after 60s), the app loads the model from a cold cache
    (~50s). But prewarm was preempted — the app's disk I/O starved it.
    This function ensures prewarm restarts (or continues) so the cache
    is warm for the NEXT time the app starts.

    Launches ``pythonw.exe -m voice_typer.server.prewarm [--force]`` as
    a detached subprocess. The subprocess survives the app's lifetime
    (detached process group on POSIX, CREATE_NO_WINDOW on Windows).

    Parameters
    ----------
    force : bool
        If True (default), pass ``--force`` to bypass the boot-sentinel
        dedup. This is correct for the timeout case: if we're calling
        this, the current boot's prewarm hasn't finished, so we want to
        re-run it unconditionally.

    Returns the subprocess PID on success, or None if the spawn failed.
    """
    # If a prewarm subprocess is already running (e.g. the
    # previous boot's prewarm is still alive after ``wait_for_prewarm``
    # timed out at 60s), do NOT spawn a second one — that would race
    # with the existing prewarm for disk I/O and double-write the PID
    # file. Return the existing PID so the caller's accounting stays
    # correct. The TOCTOU window between this check and the subsequent
    # ``Popen`` is acceptable: if prewarm exits in that window, the
    # spawn we fall through to is the correct behavior (prewarm needs
    # to restart for the next boot).
    if _pkg.is_prewarm_running():
        existing_pid = _pkg._read_prewarm_pid()
        if existing_pid is not None:
            log.info(
                "[PREWARM] spawn skipped — prewarm already running with PID %d",
                existing_pid,
            )
            return existing_pid
        # Defensive: ``is_prewarm_running`` returned True but the PID
        # file is gone (TOCTOU). Fall through to spawn so the next
        # boot still gets a prewarm — but log the anomaly so it's
        # visible in support traces.
        log.warning("[PREWARM] is_prewarm_running=True but PID file unreadable — spawning a new prewarm anyway")

    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    # AB-18: use frozen exe via resolver when available (Tauri production
    # mode). Falls back to the legacy python -m path when the resolver
    # can't find a frozen exe (dev mode) or itself errors out.
    try:
        from voice_typer.server.prewarm_resolver import resolve_prewarm_exe

        resolved = resolve_prewarm_exe()
    except Exception:
        resolved = None
    if resolved:
        if " -m " in resolved:
            # dev fallback: multi-token command line
            import shlex

            cmd = shlex.split(resolved)
        else:
            # frozen exe path
            cmd = [resolved]
    else:
        # ultimate fallback (existing behavior)
        python_bin = _sys.executable
        if _pkg.is_windows():
            pythonw = _Path(_sys.executable).parent / "pythonw.exe"
            if pythonw.exists():
                python_bin = str(pythonw)
        cmd = [python_bin, "-m", "voice_typer.server.prewarm"]

    if force:
        cmd.append("--force")
    cmd.extend(["--trigger", trigger])

    log.info("[PREWARM] spawning background prewarm: %s", " ".join(cmd))

    kwargs: dict = {}
    if _pkg.is_windows():
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    kwargs["stdout"] = subprocess.DEVNULL
    kwargs["stderr"] = subprocess.DEVNULL
    kwargs["stdin"] = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(cmd, **kwargs)
        log.info(
            "[PREWARM] background prewarm spawned (pid=%d, force=%s)",
            proc.pid,
            force,
        )
        return proc.pid
    except FileNotFoundError as exc:
        log.warning("[PREWARM] could not spawn background prewarm: %s", exc)
        return None
    except OSError as exc:
        log.warning("[PREWARM] could not spawn background prewarm: %s", exc)
        return None


# ─── Status query (ADR-0009 Issue 3) ──────────────────────────────────────

_cache_probe_cache: dict = {}
_CACHE_PROBE_TTL_S: float = 30.0


def _probe_cache_status(active_dirs: list[Path]) -> tuple[float, int, int]:
    """Return ``(cache_ratio, cached_bytes, total_bytes)`` for *active_dirs*.

    XV-18: results are memoized for ``_CACHE_PROBE_TTL_S`` seconds,
    keyed on a fingerprint of each active dir's ``(path, mtime_ns,
    size)``. The fingerprint detects new snapshot downloads (HF hub
    bumps the model dir's mtime when it writes a new symlink) so a
    freshly-downloaded model invalidates the cache immediately. Empty
    ``active_dirs`` returns ``(0.0, 0, 0)`` without polluting the
    cache (so a transient "no model" state doesn't shadow a
    subsequent "model present" probe).
    """
    if not active_dirs:
        return (0.0, 0, 0)

    fingerprint_parts: list[tuple[str, int, int]] = []
    for d in active_dirs:
        try:
            st = d.stat()
            fingerprint_parts.append((str(d), st.st_mtime_ns, st.st_size))
        except OSError:
            fingerprint_parts.append((str(d), 0, 0))
    fingerprint = tuple(fingerprint_parts)

    now = time.monotonic()
    cached = _cache_probe_cache.get(fingerprint)
    if cached is not None:
        ts, result = cached
        if now - ts < _CACHE_PROBE_TTL_S:
            return result

    sizes: list[int] = []
    ratios: list[float] = []
    total_bytes = 0
    for d in active_dirs:
        snapshots_dir = d / "snapshots"
        if not snapshots_dir.is_dir():
            continue
        try:
            entries = list(snapshots_dir.iterdir())
        except OSError:
            continue
        for snapshot in entries:
            if not snapshot.is_dir():
                continue
            weights = snapshot / "model.safetensors"
            if weights.exists():
                try:
                    size = weights.stat().st_size
                except OSError:
                    continue
                sizes.append(size)
                ratios.append(_pkg._cache_ratio(weights))
                total_bytes += size
    if sizes and total_bytes > 0:
        cached_bytes = sum(int(s * r) for s, r in zip(sizes, ratios, strict=True))
        cache_ratio = cached_bytes / total_bytes
    else:
        cached_bytes = 0
        cache_ratio = 0.0

    result = (cache_ratio, cached_bytes, total_bytes)
    _cache_probe_cache[fingerprint] = (now, result)
    return result


def _invalidate_cache_probe_cache() -> None:
    """Clear the ``_probe_cache_status`` TTL cache (XV-18).

    Tests call this between assertions to force a re-probe. Production
    code (``get_prewarm_status``) does NOT need to call this — the TTL
    + mtime fingerprint handles invalidation automatically.
    """
    _cache_probe_cache.clear()


def get_prewarm_status() -> dict:
    """Return a snapshot of the prewarm cache state for the UI.

    ADR-0009 Issue 3: called by the ``get_prewarm_status`` IPC handler
    to populate the "Cache Status" card in the About page.

    XV-18: the cache-ratio probe is memoized via
    ``_probe_cache_status`` (30 s TTL keyed on directory mtime) so
    frequent IPC polls don't re-walk the HF cache and re-probe every
    weights file each call.
    """
    # ── Sentinel: last_run + elapsed_s ──────────────────────────────
    last_run: str | None = None
    elapsed_s: float | None = None
    sentinel = _pkg._sentinel_path()
    sentinel_exists = sentinel.exists()
    try:
        if sentinel_exists:
            content = sentinel.read_text()
            lines = content.split("\n")
            boot_ts: int | None = None
            if lines and lines[0].strip():
                try:
                    boot_ts = int(lines[0].strip())
                except ValueError:
                    boot_ts = None
            if len(lines) > 1 and lines[1].strip():
                try:
                    elapsed_s = float(lines[1].strip())
                except ValueError:
                    elapsed_s = None
            if len(lines) > 2 and lines[2].strip():
                last_run = lines[2].strip()
            elif boot_ts is not None:
                from datetime import datetime

                approx_ts = boot_ts + (elapsed_s if elapsed_s is not None else 0)
                last_run = datetime.fromtimestamp(approx_ts).isoformat()
    except (ValueError, OSError):
        pass
    except Exception:
        log.debug("[PREWARM] get_prewarm_status sentinel read failed", exc_info=True)

    # ── Cache ratio probe (XV-18: TTL-memoized via _probe_cache_status) ──
    active_dirs: list[Path] = []
    try:
        active_dirs = _pkg._active_model_cache_dirs()
    except Exception:
        log.debug("[PREWARM] get_prewarm_status active_dirs lookup failed", exc_info=True)
    try:
        cache_ratio, cached_bytes, total_bytes = _probe_cache_status(active_dirs)
    except Exception:
        log.debug("[PREWARM] get_prewarm_status cache probe failed", exc_info=True)
        cache_ratio, cached_bytes, total_bytes = 0.0, 0, 0

    # ── Label: hot / partial / cold / unknown ───────────────────────
    active_dirs_any = bool(active_dirs)
    if not sentinel_exists and not active_dirs_any:
        label = "unknown"
    elif cache_ratio >= 0.9:
        label = "hot"
    elif cache_ratio >= 0.1:
        label = "partial"
    else:
        label = "cold"

    return {
        "last_run": last_run,
        "elapsed_s": elapsed_s,
        "cache_ratio": round(cache_ratio, 2),
        "cache_label": label,
        "cached_bytes": cached_bytes,
        "total_bytes": total_bytes,
        "prewarm_running": _pkg.is_prewarm_running(),
    }


def _read_prewarm_pid() -> int | None:
    """Return the live prewarm PID from the PID file, or None if absent/invalid."""
    pid_file = _pkg._pid_file_path()
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None
