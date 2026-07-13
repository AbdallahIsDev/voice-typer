"""Windows Scheduled Task registration for the prewarm step.

The prewarm script (``voice_typer.server.prewarm``) only helps if it runs
*before* the user launches Voice Typer after a reboot.  This module
registers a Windows Scheduled Task that fires it:

- **At logon**, after a 45 s delay (lets login settle, avoids contention
  with all the other startup programs fighting for disk).

The task runs with **below-normal priority**, hidden, no network, and
only when on AC power (laptops).  It calls::

    <venv-python> -m voice_typer.server.prewarm

so the prewarm runs in exactly the Python environment that Voice Typer
uses at runtime — warming the same files that will be imported later.

**Admin-free fallback.**  If ``schtasks /Create`` fails (most often because
a previously-registered task got *locked* such that a standard user can't
overwrite or delete it), we fall back to the per-user ``HKCU\\...\\Run``
registry key — the same mechanism the app's autostart uses.  It runs
``pythonw.exe -m voice_typer.server.prewarm --delay 45`` at every logon:
no console window, no admin rights, and the in-process delay replaces the
Task Scheduler logon-trigger delay.  The registry path keeps logon
warmup (the part that matters) working.

Non-Windows platforms get no-op stubs: registration returns False and
``is_prewarm_registered`` returns False, so the Settings toggle simply has
no effect there.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from voice_typer.server import _paths
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger(__name__)

TASK_NAME = "VoiceTyperPrewarm"

# Per-user Run registry key — the same mechanism the app's autostart uses.
# HKCU is user-writable, so this needs NO admin privileges.  We fall back to
# it when the Task Scheduler task can't be created (e.g. a previous task got
# locked such that a standard user cannot overwrite it with schtasks).
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# STARTUP-2: previously the Run-key fallback delayed 45 s and the Task
# Scheduler logon trigger also delayed 45 s. The app's HKCU Run key has
# NO delay, so the app always started ~45 s before prewarm, defeating
# the cache-warming benefit (app cold-imports torch/transformers while
# prewarm is still waiting to fire).
#
# Fix: prewarm fires at logon+0 s (low I/O priority prevents user
# disturbance); the app's autostart_launcher gets a --delay 30 flag so
# it waits 30 s after launch before spawning Electron. Prewarm has a
# 30 s head start on its import stage before the app starts contending.
_RUN_KEY_DELAY_SECONDS = 0

# Delay after logon before prewarming. STARTUP-2: was PT45S, now PT0S
# (low I/O priority handles contention; the previous delay guaranteed
# prewarm would lose the race against the app's cold imports).
_LOGON_DELAY = "PT0S"  # ISO 8601 duration: 0 seconds (fire at logon)

# STARTUP-2: delay the app's autostart_launcher waits before spawning
# Electron, giving prewarm a head start on warming the OS file cache.
# Coded as a CLI flag so platform.py can pass it without depending on
# this module's internals.
#
# ADR-0009 Issue 4: reduced from 30s to 15s. Combined with the prewarm
# PID-file handshake in model_manager.try_load() (wait_for_prewarm()),
# this gives prewarm a head start without wasting 15s when prewarm
# finishes early. If the user logs in faster than prewarm can finish,
# the app's model loader waits for prewarm to complete (up to 60s)
# rather than fighting it for disk I/O.
_APP_AUTOSTART_DELAY_SECONDS = 15


# ─── Python interpreter resolution ──────────────────────────────────────

def _prewarm_command() -> str | None:
    """Return the pythonw.exe path for the prewarm action, or None.

    STARTUP-1: previously returned the *full* command line
    ``"pythonw.exe" -m voice_typer.server.prewarm`` and the XML builder
    wrapped it in ``cmd.exe /c``, which spawned a visible console window
    that stayed alive ~10 min while prewarm ran.

    Now returns just the interpreter path; the module name is appended
    by ``_build_task_xml`` as a separate ``<Arguments>`` element so the
    Task Scheduler action runs ``pythonw.exe`` directly with no cmd host
    (and thus no console window — pythonw.exe has no console by design).

    Mirrors the venv-resolution logic that the Electron main process
    uses to spawn the IPC server: prefer the app venv at
    ``~/.voice-typer/venv/`` (the interpreter Electron spawns), and
    fall back to ``sys.executable``.

    NEW-DUP-001: previously this comment referenced
    ``asr_setup.get_voice_typer_python()``, a function that no longer
    exists (it was removed in an earlier refactor).  The comment is
    now updated to describe the actual logic without referencing dead
    code.
    """
    # Try pythonw.exe first (no console window).
    # RW-7: use _paths.venv_pythonw() so the venv path respects the
    # platform-aware _config_dir() logic instead of the previous
    # hardcoded Path.home() / ".voice-typer".
    venv_pythonw = _paths.venv_pythonw()
    if venv_pythonw.exists():
        return str(venv_pythonw.resolve())
    # Fall back to pythonw.exe next to the current interpreter.
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw.resolve())
    # Final fallback: sys.executable itself.  On Windows this is python.exe
    # (which has a console), but we still set Hidden=true and the task runs
    # in a non-interactive session so the console is normally not visible.
    # NOTE: this fallback only triggers when pythonw.exe is unavailable,
    # which is unusual on a normal Python install.
    python = sys.executable
    if not Path(python).exists():
        log.warning("[TASK] Python interpreter not found: %s", python)
        return None
    log.debug("[TASK] pythonw.exe not found — falling back to %s", python)
    return python


# Arguments passed to the pythonw interpreter for the prewarm action.
# Kept as a module constant so tests can verify the XML uses it.
# Arguments passed to the pythonw interpreter for the prewarm action.
# PW-2: includes --trigger boot so the log records whether the task
# fired via BootTrigger / EventTrigger (both are system-start triggers).
_PREWARM_ARGS = "-m voice_typer.server.prewarm --trigger boot"


def _prewarm_pythonw() -> str | None:
    """Resolve the pythonw.exe to run prewarm hidden (no console window).

    The Run registry key launches the program at logon in the user's
    session.  Using ``pythonw.exe`` (rather than ``python.exe`` wrapped in
    ``cmd.exe /c timeout ... start``) is the documented way to run a Python
    script with no console, so nothing flashes on screen at login.  The
    delay is handled in-process by prewarm's own ``--delay`` flag, avoiding
    a ``timeout.exe``/``cmd.exe`` dependency that would itself open a console.
    """
    # RW-7: use _paths.venv_pythonw() for the same reasons as
    # _prewarm_command() — see the docstring there for details.
    venv_pythonw = _paths.venv_pythonw()
    if venv_pythonw.exists():
        return str(venv_pythonw.resolve())
    # Fall back to pythonw.exe next to the current interpreter.
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw.resolve())
    log.warning("[TASK] pythonw.exe not found — prewarm would show a console")
    return None


def _registry_command() -> str | None:
    """Build the command line stored in the HKCU Run key.

    Runs ``pythonw.exe -m voice_typer.server.prewarm --delay <n>``.  The
    ``--delay`` lets login settle (replacing the Task Scheduler logon
    trigger's delay); it runs in-process so no console window is needed.
    """
    pythonw = _prewarm_pythonw()
    if pythonw is None:
        return None
    return (
        f'"{pythonw}" -m voice_typer.server.prewarm '
        f'--delay {_RUN_KEY_DELAY_SECONDS} '
        '--trigger logon'
    )


# ─── HKCU Run-key fallback (no admin needed) ──────────────────────────────

def _register_prewarm_registry(command: str) -> bool:
    """Register prewarm via the HKCU Run registry key (no admin needed).

    The Run key makes the program launch at every user logon.  We only use
    it as a fallback when the Task Scheduler task can't be registered
    (e.g. a locked task left behind by an earlier install).  Returns True
    on success, False otherwise.
    """
    if not is_windows():
        return False
    try:
        import winreg
    except ImportError:
        return False  # not Windows (e.g. test host with patched sys.platform)
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.SetValueEx(key, TASK_NAME, 0, winreg.REG_SZ, command)
        finally:
            winreg.CloseKey(key)
        log.info("[TASK] Prewarm registered via HKCU Run key (no admin)")
        return True
    except OSError as exc:
        log.warning("[TASK] Could not register prewarm via registry: %s", exc)
        return False


def _unregister_prewarm_registry() -> bool:
    """Remove prewarm from the HKCU Run registry key.

    Returns True on success (including when the value was already absent).
    """
    if not is_windows():
        return False
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, TASK_NAME)
        finally:
            winreg.CloseKey(key)
        log.info("[TASK] Prewarm removed from HKCU Run key")
        return True
    except FileNotFoundError:
        return True  # already absent
    except OSError as exc:
        log.warning("[TASK] Could not remove prewarm from registry: %s", exc)
        return False


def _is_prewarm_registered_registry() -> bool:
    """Return True if the HKCU Run key has the prewarm value."""
    if not is_windows():
        return False
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, TASK_NAME)
            return bool(val)
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError:
        return False


# ─── Task XML definition ──────────────────────────────────────────────────

def _build_task_xml(python_exe: str, arguments: str | None = None) -> str:
    """Build the Task Scheduler XML definition.

    Hand-rolled rather than via ``win32com.client`` (which pywin32 may not
    ship in the runtime venv).  ``schtasks /Create /XML`` accepts this
    directly.

    STARTUP-1: previously this wrapped the prewarm command in
    ``cmd.exe /c``, which spawned a visible console window that stayed
    alive ~10 min while prewarm ran. Now ``<Command>`` is the pythonw.exe
    path directly (no cmd host) and ``<Arguments>`` carries the
    ``-m voice_typer.server.prewarm`` flag. pythonw.exe has no console
    by design, so no window ever appears.

    Parameters
    ----------
    python_exe : str
        Absolute path to the Python interpreter (pythonw.exe preferred).
    arguments : str, optional
        Command-line arguments for the interpreter. Defaults to
        ``-m voice_typer.server.prewarm``.
    """
    if arguments is None:
        arguments = _PREWARM_ARGS
    root = ET.Element("Task", {
        "version": "1.4",
        "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task",
    })

    # ── RegistrationInfo ──────────────────────────────────────────────
    reg = ET.SubElement(root, "RegistrationInfo")
    desc = ET.SubElement(reg, "Description")
    desc.text = (
        "Warms the OS file cache for Voice Typer (torch + model weights) "
        "so the app starts fast after a reboot. Safe to disable or delete."
    )
    uri = ET.SubElement(reg, "URI")
    uri.text = f"{TASK_NAME}"

    # ── Triggers ──────────────────────────────────────────────────────
    #
    # ADR-0009 Issue 1 + Issue 2 (combined): emit a BootTrigger AND an
    # EventTrigger (Event ID 12, "OS started"). This replaces the
    # previous single LogonTrigger, which had two problems:
    #
    #   1. LogonTrigger fires only at user logon, not at system boot —
    #      so prewarm couldn't start until the user typed their password.
    #      BootTrigger fixes this by firing at system startup.
    #
    #   2. LogonTrigger with Hidden=true RE-FIRES on Windows session
    #      unlock (known quirk), causing prewarm to spawn a new Python
    #      process every time the user unlocked their screen. The
    #      sentinel caught it, but the log showed a confusing "free RAM
    #      < budget" message instead of "already ran". EventTrigger
    #      (Event ID 12) fires ONLY on the actual OS-started event,
    #      never on screen unlock.
    #
    # Event ID 12 fires on both cold boot AND Fast Startup (which skips
    # BootTrigger), so the EventTrigger alone covers both cases. We keep
    # BootTrigger as defense-in-depth: if Event ID 12 ever fails to
    # fire on a specific Windows build (some kernel-event-channel
    # configurations filter it), BootTrigger still covers cold boot.
    # The sentinel in prewarm.run() prevents the rare double-fire if
    # both triggers fire in the same boot session.
    triggers = ET.SubElement(root, "Triggers")

    # Trigger 1: Boot — fires at system boot (cold boot + restart).
    # Note: BootTrigger does NOT fire on Windows Fast Startup (which
    # hibernates the kernel session rather than fully restarting). The
    # EventTrigger below covers that case.
    boot = ET.SubElement(triggers, "BootTrigger")
    ET.SubElement(boot, "Enabled").text = "true"

    # Trigger 2: Event — fires on Event ID 12 (Kernel-General, "OS
    # started"). This fires once per boot, INCLUDING Fast Startup, and
    # NEVER on screen unlock or session reconnect. The subscription
    # XML is escaped because it lives inside the <Subscription> text
    # node of the EventTrigger element.
    event = ET.SubElement(triggers, "EventTrigger")
    ET.SubElement(event, "Enabled").text = "true"
    subscription = ET.SubElement(event, "Subscription")
    subscription.text = (
        "<QueryList><Query Id=\"0\" Path=\"System\">"
        "<Select Path=\"System\">"
        "*[System[Provider[@Name='Microsoft-Windows-Kernel-General'] "
        "and (EventID=12)]]"
        "</Select>"
        "</Query></QueryList>"
    )

    # PREWARM-001: The IdleTrigger that previously lived here fired every
    # time the system went idle for 15+ minutes, causing prewarm to run
    # 5+ times per session. After the first run the OS file cache is
    # already warm; subsequent runs are pure wasted I/O and — under memory
    # pressure — actively harmful because they re-read ~6 GB of files
    # that the OS had just evicted to free RAM. Prewarm is a cold-boot
    # optimization; running it once per login is sufficient.

    # ── Principal — run as the current user, no elevation, hidden ────
    # Per the Task Scheduler schema, omitting <UserId> in a Principal
    # with id="Author" defaults to the registering user (the current
    # user). This avoids fragile SID extraction via ctypes that was
    # returning None (missing argtypes declarations) and producing an
    # empty <UserId></UserId> that Task Scheduler rejects with
    # "(1,485):UserId: incorrectly formatted or out of range".
    principal = ET.SubElement(root, "Principals")
    princ = ET.SubElement(principal, "Principal", {"id": "Author"})
    ET.SubElement(princ, "LogonType").text = "InteractiveToken"
    ET.SubElement(princ, "RunLevel").text = "LeastPrivilege"

    # ── Settings ─────────────────────────────────────────────────────
    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "ExecutionTimeLimit").text = "PT10M"  # 10 min max
    ET.SubElement(settings, "Hidden").text = "true"
    ET.SubElement(settings, "RunOnlyIfNetworkAvailable").text = "false"
    ET.SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    ET.SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"

    # PREWARM-001: the <IdleSettings> block that previously lived here was
    # vestigial — once the <IdleTrigger> was removed (it caused prewarm to
    # fire 5+ times per session), these settings had no effect on
    # scheduling.  They were left behind in the earlier cleanup, which was
    # misleading (the XML still advertised an idle behavior the task no
    # longer had).  Prewarm is logon/boot-only on all platforms now.

    # ── Actions ──────────────────────────────────────────────────────
    # STARTUP-1: <Command> is pythonw.exe directly (NO cmd.exe wrapper).
    # pythonw.exe has no console; the previous cmd.exe /c wrapper kept
    # the cmd host alive for the duration of the prewarm run (~10 min),
    # showing a ghost window.
    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    exec_el = ET.SubElement(actions, "Exec")
    ET.SubElement(exec_el, "Command").text = python_exe
    ET.SubElement(exec_el, "Arguments").text = arguments

    # ET.tostring adds the encoding declaration schtasks expects.
    return ET.tostring(root, encoding="unicode")




# ─── schtasks wrappers ──────────────────────────────────────────────────

def _schtasks(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Run ``schtasks`` with *args*. Returns (returncode, combined output).

    PERF-NEW-026: ``schtasks /Create`` can block for up to 30s if the
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
        log.error("[TASK] schtasks timed out: %s", cmd)
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

    # Build the arg string for schtasks
    arg_str = " ".join(
        f'"{a}"' if " " in a or "&" in a else a for a in args
    )

    # Redirect output to a temp file so we can read it back
    with tempfile.NamedTemporaryFile(
        mode="w+t", suffix=".txt", delete=False, encoding="utf-8"
    ) as out_file:
        out_path = out_file.name

    try:
        # Launch via cmd.exe /c with redirection so we capture output
        cmd_line = f'schtasks {arg_str} > "{out_path}" 2>&1'
        sei = SHELLEXECUTEINFO()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = see_mask_noclose
        sei.lpVerb = "runas"
        sei.lpFile = "cmd.exe"
        sei.lpParameters = f"/c \"{cmd_line}\""
        sei.nShow = sw_hide

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.WinError()
            return 1223, f"UAC elevation failed: {err}"

        # Wait for the process to finish
        ctypes.windll.kernel32.WaitForSingleObject(
            sei.hProcess, timeout_ms
        )

        exit_code = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(
            sei.hProcess, ctypes.byref(exit_code)
        )
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)

        # Read output from the temp file
        output = ""
        try:
            with open(out_path, encoding="utf-8") as f:
                output = f.read()
        except OSError:
            pass

        return exit_code.value, output

    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)


# ─── Public API ──────────────────────────────────────────────────────────

def is_supported() -> bool:
    """Return True if prewarm scheduling is supported on this platform.

    STARTUP-5: previously Windows-only. Now also returns True on macOS
    and Linux (via prewarm_scheduler_posix). The actual scheduling layer
    differs by platform (Task Scheduler / LaunchAgent / systemd user timer).

    Returns:
        True if the platform supports scheduled prewarm tasks.
    """
    if is_windows():
        return Path(
            os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32\schtasks.exe"
        ).exists()
    # STARTUP-5: POSIX platforms use prewarm_scheduler_posix.
    # Bug fix: use exact match instead of startswith("linux") for
    # consistency with prewarm_scheduler_posix.is_supported().
    return is_macos() or is_linux()


def is_prewarm_registered() -> bool:
    """Return True if prewarm is registered via EITHER mechanism.

    The Task Scheduler task is preferred; the HKCU Run key is the fallback.
    On POSIX, delegates to prewarm_scheduler_posix.

    Returns:
        True if a prewarm task is currently registered.
    """
    # STARTUP-5: delegate to POSIX scheduler on macOS/Linux.
    if not is_windows():
        try:
            from voice_typer.server.prewarm_scheduler_posix import is_prewarm_registered as _posix_is
            return _posix_is()
        except Exception:
            return False
    if not is_supported():
        return False
    rc, _ = _schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    return rc == 0 or _is_prewarm_registered_registry()


def register_prewarm_task() -> bool:
    """Register (or update) the prewarm task.

    Returns True on success, False on failure.  Safe to call repeatedly —
    existing tasks are overwritten with ``/F``.

    STARTUP-5: on macOS/Linux, delegates to prewarm_scheduler_posix which
    registers a LaunchAgent (macOS) or systemd user timer (Linux).

    Returns:
        True if the task was registered successfully, False otherwise.
    """
    # STARTUP-5: delegate to POSIX scheduler on macOS/Linux.
    if not is_windows():
        try:
            from voice_typer.server.prewarm_scheduler_posix import register_prewarm_task as _posix_reg
            return _posix_reg()
        except Exception as e:
            log.warning("[TASK] POSIX prewarm registration raised: %s", e)
            return False
    if not is_supported():
        log.info("[TASK] Scheduled Tasks not supported on this platform — skipping")
        return False

    command = _prewarm_command()
    if command is None:
        log.warning("[TASK] cannot resolve prewarm command — skipping registration")
        return False

    xml_def = _build_task_xml(command, _PREWARM_ARGS)

    # Write XML to a temp file — schtasks /Create /XML needs a path, and
    # passing huge inline args via cmd.exe is fragile.
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(xml_def)
            temp_xml = tf.name
    except OSError as exc:
        log.error("[TASK] could not write task XML: %s", exc)
        return False

    try:
        # Delete any existing (possibly locked) task BEFORE creating.
        # A task created with the old, broken UserId XML can become
        # locked so that `schtasks /Create /F` fails with "Access is
        # denied".  An explicit /Delete /F first clears it cleanly;
        # if it doesn't exist, the error is harmless.
        with contextlib.suppress(Exception):
            _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], capture=True)

        # /F forces overwrite if the task already exists.
        rc, output = _schtasks(
            ["/Create", "/TN", TASK_NAME, "/XML", temp_xml, "/F"],
            capture=True,
        )
        # If non-elevated attempt fails with Access denied, try via UAC
        if rc != 0 and "access is denied" in (output or "").lower():
            log.info("[TASK] Non-elevated schtasks failed — retrying with UAC elevation prompt")
            rc, output = _schtasks_elevated(
                ["/Create", "/TN", TASK_NAME, "/XML", temp_xml, "/F"],
            )
        if rc == 0:
            log.info("[TASK] VoiceTyperPrewarm registered OK")
            # We switched to the Task Scheduler path — remove any stale
            # Run-key fallback left by a previous (admin-less) run so we
            # don't prewarm twice at the next logon.
            if _is_prewarm_registered_registry():
                _unregister_prewarm_registry()
            return True
        # Fall back to the HKCU Run key (no admin needed).  The most common
        # cause of the /Create failure is a task left locked by a previous
        # admin-created install that a standard user cannot overwrite; the
        # Run key gives us a working, admin-free logon trigger regardless.
        log.warning(
            "[TASK] schtasks registration failed (%s) — "
            "falling back to HKCU Run key",
            output.strip(),
        )
        reg_cmd = _registry_command()
        if reg_cmd is None:
            log.warning("[TASK] Cannot build Run-key command — aborting")
            return False
        return _register_prewarm_registry(reg_cmd)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp_xml)


def unregister_prewarm_task() -> bool:
    """Delete the VoiceTyperPrewarm scheduled task AND Run-key fallback.

    Returns True if prewarm is fully removed (or was never present).  We
    always clean up both mechanisms: a standard user may not be able to
    delete a locked Task Scheduler task, but the Run key is always
    user-writable, so the prewarm is reliably managed.

    STARTUP-5: on macOS/Linux, delegates to prewarm_scheduler_posix.

    Returns:
        True if the prewarm task was fully removed or was not present.
    """
    # STARTUP-5: delegate to POSIX scheduler on macOS/Linux.
    if not is_windows():
        try:
            from voice_typer.server.prewarm_scheduler_posix import unregister_prewarm_task as _posix_unreg
            return _posix_unreg()
        except Exception as e:
            log.warning("[TASK] POSIX prewarm removal raised: %s", e)
            return False
    if not is_supported():
        return False

    removed_task = False
    rc, output = _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], capture=True)
    if rc == 0:
        log.info("[TASK] VoiceTyperPrewarm scheduled task removed")
        removed_task = True
    elif rc == 1 and (
        "cannot find" in output.lower() or "does not exist" in output.lower()
    ):
        log.info("[TASK] VoiceTyperPrewarm scheduled task was already absent")
        removed_task = True
    elif "access is denied" in output.lower():
        # A locked task the standard user can't delete.  The Run-key path
        # below still succeeds, and the next logon won't relaunch prewarm
        # from the registry.  The orphaned task is inert: it points at our
        # The prewarm module skips when free RAM is low (EXIT_LOW_RAM).
        # The scheduled task is harmless otherwise.
        log.warning(
            "[TASK] Cannot delete locked scheduled task without admin; "
            "the Run-key fallback is removed and prewarm will no-op via config."
        )
    else:
        log.warning("[TASK] task removal failed (rc=%d): %s", rc, output.strip())

    # Always remove the Run-key fallback (user-writable, never locked).
    removed_reg = _unregister_prewarm_registry()

    return removed_task or removed_reg or not is_prewarm_registered()
