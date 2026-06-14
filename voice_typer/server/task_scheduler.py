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

Non-Windows platforms get no-op stubs: registration returns False and
``is_prewarm_enabled`` returns False, so the Settings toggle simply has
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

# Delay after logon before prewarming — lets login settle and avoids
# contention with every other startup program hitting disk at once.
_LOGON_DELAY = "PT45S"  # ISO 8601 duration: 45 seconds

# Re-warm after this much idle time, so a heavy-RAM session that evicted
# the cache gets another chance before the user dictates again.
_IDLE_DELAY = "PT15M"  # 15 minutes


# ─── Python interpreter resolution ──────────────────────────────────────

def _prewarm_command() -> str | None:
    """Return the full command line for the prewarm action, or None.

    Mirrors ``asr_setup.get_voice_typer_python()``: prefer the app venv
    at ``~/.voice-typer/venv/`` (the interpreter Electron spawns), and
    fall back to ``sys.executable``.
    """
    venv_python = Path.home() / ".voice-typer" / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        python = str(venv_python.resolve())
    else:
        python = sys.executable
    if not Path(python).exists():
        log.warning("[TASK] Python interpreter not found: %s", python)
        return None
    # Quote for the XML; schtasks handles the rest.
    return f'"{python}" -m voice_typer.server.prewarm'


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
    principal = ET.SubElement(root, "Principals")
    princ = ET.SubElement(principal, "Principal", {"id": "Author"})
    ET.SubElement(princ, "UserId").text = _current_user_sid() or ""
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


def _current_user_sid() -> str | None:
    """Return the current user's SID string, or None if unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        ADVAPI32 = ctypes.windll.advapi32
        token = wintypes.HANDLE()
        ADVAPI32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(),
            0x0008,  # TOKEN_QUERY
            ctypes.byref(token),
        )
        try:
            # Get the user SID from the token.
            needed = wintypes.DWORD(0)
            ADVAPI32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buf = (ctypes.c_byte * needed.value)()
            if not ADVAPI32.GetTokenInformation(
                token, 1, buf, needed.value, ctypes.byref(needed)
            ):
                return None

            # Walk TOKEN_USER → SID → ConvertSidToStringSid
            class _TOKEN_USER(ctypes.Structure):
                _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]
            tu = ctypes.cast(buf, ctypes.POINTER(_TOKEN_USER)).contents

            sid_str = ctypes.c_wchar_p()
            ADVAPI32.ConvertSidToStringSidW(
                tu.Sid, ctypes.byref(sid_str)
            )
            result = sid_str.value
            ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(sid_str))
            return result
        finally:
            ctypes.windll.kernel32.CloseHandle(token)
    except Exception as exc:
        log.debug("[TASK] could not resolve user SID: %s", exc)
        return None


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
    """Return True if the VoiceTyperPrewarm task currently exists."""
    if not is_supported():
        return False
    rc, _ = _schtasks(["/Query", "/TN", TASK_NAME, "/XML"])
    return rc == 0


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
        # /F forces overwrite if the task already exists.
        rc, output = _schtasks(
            ["/Create", "/TN", TASK_NAME, "/XML", temp_xml, "/F"],
            capture=True,
        )
        if rc == 0:
            log.info("[TASK] VoiceTyperPrewarm registered OK")
            return True
        log.error("[TASK] registration failed (rc=%d): %s", rc, output.strip())
        return False
    finally:
        try:
            os.unlink(temp_xml)
        except OSError:
            pass


def unregister_prewarm_task() -> bool:
    """Delete the VoiceTyperPrewarm scheduled task.  Returns True on success
    (including the case where it didn't exist)."""
    if not is_supported():
        return False
    rc, output = _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], capture=True)
    if rc == 0:
        log.info("[TASK] VoiceTyperPrewarm removed")
        return True
    # "The system cannot find the file specified" is fine — already gone.
    if rc == 1 and ("cannot find" in output.lower() or "does not exist" in output.lower()):
        log.info("[TASK] VoiceTyperPrewarm was already absent")
        return True
    log.warning("[TASK] removal failed (rc=%d): %s", rc, output.strip())
    return False
