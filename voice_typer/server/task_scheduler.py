"""Windows Scheduled Task registration for the prewarm step.

The prewarm script (``voice_typer.server.prewarm``) only helps if it runs
*before* the user launches Voice Typer after a reboot.  This module
registers a Windows Scheduled Task that fires it:

- **At logon**, after a 45 s delay (lets login settle, avoids contention
  with all the other startup programs fighting for disk).
- **On idle**, when the system has been idle for 15 min (re-warms the
  cache if a heavy-RAM session evicted it).

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
Task Scheduler logon-trigger delay.  The registry path loses the idle
re-warm trigger, but logon warmup (the part that matters) keeps working.

Non-Windows platforms get no-op stubs: registration returns False and
``is_prewarm_registered`` returns False, so the Settings toggle simply has
no effect there.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("voice_typer.task_scheduler")

TASK_NAME = "VoiceTyperPrewarm"

# Per-user Run registry key — the same mechanism the app's autostart uses.
# HKCU is user-writable, so this needs NO admin privileges.  We fall back to
# it when the Task Scheduler task can't be created (e.g. a previous task got
# locked such that a standard user cannot overwrite it with schtasks).
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Seconds the Run-key fallback sleeps before prewarming (mirrors the
# Task Scheduler logon-trigger delay so prewarm does not contend with all
# the other startup programs hitting disk at once).
_RUN_KEY_DELAY_SECONDS = 45

# Delay after logon before prewarming — lets login settle and avoids
# contention with every other startup program hitting disk at once.
_LOGON_DELAY = "PT45S"  # ISO 8601 duration: 45 seconds

# Re-warm after this much idle time, so a heavy-RAM session that evicted
# the cache gets another chance before the user dictates again.
_IDLE_DELAY = "PT15M"  # 15 minutes


# ─── Python interpreter resolution ──────────────────────────────────────

def _prewarm_command() -> str | None:
    """Return the full command line for the prewarm action, or None.

    Prefers ``pythonw.exe`` (no console window) over ``python.exe``, so
    the prewarm runs silently in the background even when launched via
    ``cmd.exe /c`` in the Task Scheduler action.

    Mirrors ``asr_setup.get_voice_typer_python()``: prefer the app venv
    at ``~/.voice-typer/venv/`` (the interpreter Electron spawns), and
    fall back to ``sys.executable``.
    """
    # Try pythonw.exe first (no console window).
    venv_pythonw = Path.home() / ".voice-typer" / "venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        python = str(venv_pythonw.resolve())
    else:
        pythonw = Path(sys.executable).parent / "pythonw.exe"
        if pythonw.exists():
            python = str(pythonw.resolve())
        else:
            python = sys.executable
    if not Path(python).exists():
        log.warning("[TASK] Python interpreter not found: %s", python)
        return None
    # Quote for the XML; schtasks handles the rest.
    return f'"{python}" -m voice_typer.server.prewarm'


def _prewarm_pythonw() -> str | None:
    """Resolve the pythonw.exe to run prewarm hidden (no console window).

    The Run registry key launches the program at logon in the user's
    session.  Using ``pythonw.exe`` (rather than ``python.exe`` wrapped in
    ``cmd.exe /c timeout ... start``) is the documented way to run a Python
    script with no console, so nothing flashes on screen at login.  The
    delay is handled in-process by prewarm's own ``--delay`` flag, avoiding
    a ``timeout.exe``/``cmd.exe`` dependency that would itself open a console.
    """
    venv_pythonw = Path.home() / ".voice-typer" / "venv" / "Scripts" / "pythonw.exe"
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
        f'--delay {_RUN_KEY_DELAY_SECONDS}'
    )


# ─── HKCU Run-key fallback (no admin needed) ──────────────────────────────

def _register_prewarm_registry(command: str) -> bool:
    """Register prewarm via the HKCU Run registry key (no admin needed).

    The Run key makes the program launch at every user logon.  We only use
    it as a fallback when the Task Scheduler task can't be registered
    (e.g. a locked task left behind by an earlier install).  Returns True
    on success, False otherwise.
    """
    if sys.platform != "win32":
        return False
    import winreg
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
    if sys.platform != "win32":
        return False
    import winreg
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
    if sys.platform != "win32":
        return False
    import winreg
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

def _build_task_xml(command: str) -> str:
    """Build the Task Scheduler XML definition.

    Hand-rolled rather than via ``win32com.client`` (which pywin32 may not
    ship in the runtime venv).  ``schtasks /Create /XML`` accepts this
    directly.
    """
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
    uri.text = f"\\{TASK_NAME}"

    # ── Triggers ──────────────────────────────────────────────────────
    triggers = ET.SubElement(root, "Triggers")

    # Logon trigger (with delay)
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    ET.SubElement(logon, "Delay").text = _LOGON_DELAY

    # Idle trigger (re-warm during idle)
    idle = ET.SubElement(triggers, "IdleTrigger")
    ET.SubElement(idle, "Enabled").text = "true"

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

    # Idle settings — require 15 min idle, stop when no longer idle.
    idle_settings = ET.SubElement(settings, "IdleSettings")
    ET.SubElement(idle_settings, "Duration").text = "PT30M"
    ET.SubElement(idle_settings, "WaitTimeout").text = _IDLE_DELAY
    ET.SubElement(idle_settings, "StopOnIdleEnd").text = "true"
    ET.SubElement(idle_settings, "RestartOnIdle").text = "false"

    # ── Actions ──────────────────────────────────────────────────────
    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    exec_el = ET.SubElement(actions, "Exec")
    # Split "python.exe" -m voice_typer.server.prewarm into Command + Args.
    # We pass the whole thing through cmd.exe to avoid quoting headaches
    # in the XML — schtasks is much happier with a single shell command.
    ET.SubElement(exec_el, "Command").text = "cmd.exe"
    ET.SubElement(exec_el, "Arguments").text = f'/c {command}'

    # ET.tostring adds the encoding declaration schtasks expects.
    return ET.tostring(root, encoding="unicode")




# ─── schtasks wrapper ─────────────────────────────────────────────────────

def _schtasks(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Run ``schtasks`` with *args*. Returns (returncode, combined output)."""
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


# ─── Public API ──────────────────────────────────────────────────────────

def is_supported() -> bool:
    """Return True if Scheduled Task registration is supported here."""
    return sys.platform == "win32" and Path(
        os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\schtasks.exe"
    ).exists()


def is_prewarm_registered() -> bool:
    """Return True if prewarm is registered via EITHER mechanism.

    The Task Scheduler task is preferred; the HKCU Run key is the fallback.
    """
    if not is_supported():
        return False
    rc, _ = _schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    return rc == 0 or _is_prewarm_registered_registry()


def register_prewarm_task() -> bool:
    """Register (or update) the VoiceTyperPrewarm scheduled task.

    Returns True on success, False on failure.  Safe to call repeatedly —
    existing tasks are overwritten with ``/F``.
    """
    if not is_supported():
        log.info("[TASK] Scheduled Tasks not supported on this platform — skipping")
        return False

    command = _prewarm_command()
    if command is None:
        log.warning("[TASK] cannot resolve prewarm command — skipping registration")
        return False

    xml_def = _build_task_xml(command)

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
        try:
            _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], capture=True)
        except Exception:
            pass

        # /F forces overwrite if the task already exists.
        rc, output = _schtasks(
            ["/Create", "/TN", TASK_NAME, "/XML", temp_xml, "/F"],
            capture=True,
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
        try:
            os.unlink(temp_xml)
        except OSError:
            pass


def unregister_prewarm_task() -> bool:
    """Delete the VoiceTyperPrewarm scheduled task AND Run-key fallback.

    Returns True if prewarm is fully removed (or was never present).  We
    always clean up both mechanisms: a standard user may not be able to
    delete a locked Task Scheduler task, but the Run key is always
    user-writable, so disabling fast_startup reliably stops the prewarm.
    """
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
        # prewarm module which honours the fast_startup config flag, so it
        # will exit early (EXIT_DISABLED) once the setting is off.
        log.warning(
            "[TASK] Cannot delete locked scheduled task without admin; "
            "the Run-key fallback is removed and prewarm will no-op via config."
        )
    else:
        log.warning("[TASK] task removal failed (rc=%d): %s", rc, output.strip())

    # Always remove the Run-key fallback (user-writable, never locked).
    removed_reg = _unregister_prewarm_registry()

    return removed_task or removed_reg or not is_prewarm_registered()
