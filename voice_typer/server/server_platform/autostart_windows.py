"""Windows autostart. Task Scheduler + Startup .bat + HKCU Run key."""

from __future__ import annotations

import contextlib
import logging
import shlex
import sys
from pathlib import Path

# Patch-path bindings. Names defined in THIS module are resolved as
from voice_typer.server.branding import APP_NAME
from voice_typer.server.server_platform import autostart as _autostart_mod
from voice_typer.server.server_platform.platform_flags import is_windows

log = logging.getLogger(__name__)

# STARTUP-7: Task Scheduler logon trigger fires earlier and more


def _enable_autostart_windows() -> bool:
    """STARTUP-7: register app autostart via Task Scheduler (preferred),"""
    if _register_app_autostart_task():
        with contextlib.suppress(Exception):
            _unregister_app_autostart_runkey()
        with contextlib.suppress(Exception):
            _unregister_app_autostart_startup()
        return True
    log.warning("[CONFIG] Task Scheduler autostart failed; trying Startup-folder .bat")
    if _register_app_autostart_startup():
        with contextlib.suppress(Exception):
            _unregister_app_autostart_runkey()
        return True
    log.warning("[CONFIG] Startup-folder .bat autostart failed; trying HKCU Run key")
    if _register_app_autostart_runkey():
        # Clean up a stale Startup .bat from a previous session (same
        with contextlib.suppress(Exception):
            _unregister_app_autostart_startup()
        return True
    log.warning("[CONFIG] All three autostart mechanisms failed")
    return False


def _disable_autostart_windows() -> bool:
    """STARTUP-7: remove app autostart from ALL mechanisms."""
    removed_task = _unregister_app_autostart_task()
    removed_reg = _unregister_app_autostart_runkey()
    removed_startup = _unregister_app_autostart_startup()
    return removed_task or removed_reg or removed_startup


def _is_autostart_windows() -> bool:
    """STARTUP-7: True if autostart is registered via ANY of the three mechanisms."""
    return (
        _is_app_autostart_task_registered()
        or _is_app_autostart_runkey_registered()
        or _is_app_autostart_startup_registered()
    )


def _app_autostart_command_and_args() -> tuple[str, str]:
    """Return (pythonw_path, arguments) for the app autostart task."""
    from voice_typer.server.task_scheduler import _APP_AUTOSTART_DELAY_SECONDS

    delay_str = str(_APP_AUTOSTART_DELAY_SECONDS)

    # Packaged (frozen, no-Python) installs: Task Scheduler splits
    try:
        from voice_typer.server.server_platform.autostart import _packaged_tauri_target

        packaged = _packaged_tauri_target()
    except Exception:
        packaged = None
    if packaged is not None:
        tauri_bin, tauri_args = packaged
        args = " ".join(tauri_args)
        log.info("[AUTOSTART] Resolved packaged Task Scheduler command: %s %s", tauri_bin, args)
        return tauri_bin, args

    launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"
    from voice_typer.server.server_platform.autostart import _prefer_pythonw

    python_bin = _prefer_pythonw(sys.executable)

    # voice_typer.server.autostart_launcher BEFORE swapping, if the
    if sys.prefix != sys.base_prefix:
        from voice_typer.server.server_platform.autostart import (
            _probe_system_python,
        )

        system_python = _probe_system_python("python.exe")
        if system_python:
            python_bin = system_python
        else:
            log.warning(
                "[AUTOSTART] Running inside venv (%s) but system Python "
                "cannot import voice_typer.server.autostart_launcher "
                "(probe failed). Keeping venv Python for the Windows "
                "Task Scheduler entry, autostart will break if the "
                "venv is deleted, but works for the current user.",
                sys.executable,
            )
    # PLAT-VENV/SILENT-LOGON: the probe above may have swapped the
    python_bin = _prefer_pythonw(python_bin)

    # AUTOSTART-CMD-VALIDATE: verify the resolved Python interpreter
    if not Path(python_bin).exists():
        log.warning(
            "[AUTOSTART] Resolved Python interpreter does not exist: %s "
            "— attempting Tauri binary fallback for Task Scheduler entry",
            python_bin,
        )
        try:
            from voice_typer.server.autostart_launcher import _tauri_binary

            tauri_bin = _tauri_binary()
        except Exception:
            tauri_bin = None
        if tauri_bin:
            log.info(
                "[AUTOSTART] Using Tauri binary for Task Scheduler entry (no Python interpreter available): %s",
                tauri_bin,
            )
            return tauri_bin, ""
        log.error(
            "[AUTOSTART] No Python interpreter AND no Tauri binary "
            "available, Task Scheduler entry will be non-functional"
        )
    args = f'"{launcher}" --hidden --delay {delay_str}'
    log.info("[AUTOSTART] Resolved Task Scheduler command: %s %s", python_bin, args)
    return python_bin, args


def _build_app_autostart_task_xml() -> str:
    """Build the Task Scheduler XML for the app autostart task."""
    import xml.etree.ElementTree as ET

    python_exe, arguments = _app_autostart_command_and_args()

    root = ET.Element(
        "Task",
        {
            "version": "1.4",
            "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task",
        },
    )
    reg = ET.SubElement(root, "RegistrationInfo")
    desc = ET.SubElement(reg, "Description")
    desc.text = (
        f"Launches {APP_NAME} at user logon. Safe to disable or delete; "
        "the app can still be started manually from the Start Menu."
    )
    uri = ET.SubElement(reg, "URI")
    uri.text = f"{_autostart_mod._APP_AUTOSTART_TASK_NAME}"

    triggers = ET.SubElement(root, "Triggers")
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    # PT0S: fire immediately at logon (launcher's --delay <N> handles
    ET.SubElement(logon, "Delay").text = "PT0S"

    principal = ET.SubElement(root, "Principals")
    princ = ET.SubElement(principal, "Principal", {"id": "Author"})
    ET.SubElement(princ, "LogonType").text = "InteractiveToken"
    ET.SubElement(princ, "RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(root, "Settings")
    ET.SubElement(settings, "ExecutionTimeLimit").text = "PT0S"  # no limit
    ET.SubElement(settings, "Hidden").text = "true"
    ET.SubElement(settings, "RunOnlyIfNetworkAvailable").text = "false"
    ET.SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
    ET.SubElement(settings, "StopIfGoingOnBatteries").text = "false"
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"

    actions = ET.SubElement(root, "Actions", {"Context": "Author"})
    exec_el = ET.SubElement(actions, "Exec")
    # STARTUP-1 lesson: use pythonw.exe directly, no cmd.exe wrapper.
    ET.SubElement(exec_el, "Command").text = python_exe
    ET.SubElement(exec_el, "Arguments").text = arguments

    return ET.tostring(root, encoding="unicode")


def _register_app_autostart_task() -> bool:
    """Register the app autostart Task Scheduler task. Returns True on success."""
    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        xml_def = _build_app_autostart_task_xml()
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as tf:
            tf.write(xml_def)
            temp_xml = tf.name
        try:
            with contextlib.suppress(Exception):
                task_scheduler._schtasks(
                    ["/Delete", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/F"], capture=True
                )
            rc, output = task_scheduler._schtasks(
                ["/Create", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                capture=True,
            )
            if rc != 0 and "access is denied" in (output or "").lower():
                log.info("[CONFIG] Non-elevated schtasks failed, retrying with UAC elevation prompt")
                rc, output = task_scheduler._schtasks_elevated(
                    ["/Create", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                )
            if rc == 0:
                log.info("[CONFIG] App autostart registered via Task Scheduler (logon trigger)")
                return True
            log.warning("[CONFIG] Task Scheduler autostart registration failed: %s", output.strip())
            return False
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp_xml)
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart registration raised: %s", e)
        return False


def _unregister_app_autostart_task() -> bool:
    """Remove the app autostart Task Scheduler task. Returns True on success.

    Bug fix: removed redundant sys.platform != 'win32' check.
    """
    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        rc, output = task_scheduler._schtasks(
            ["/Delete", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/F"],
            capture=True,
        )
        if rc == 0:
            log.info("[CONFIG] App autostart Task Scheduler task removed")
            return True
        # already absent
        return "cannot find" in output.lower() or "does not exist" in output.lower()
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart removal raised: %s", e)
        return False


def _is_app_autostart_task_registered() -> bool:
    """True if the app autostart Task Scheduler task exists AND its"""
    # (pyrefly): import subprocess BEFORE the try block so the
    import subprocess

    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        rc, output = task_scheduler._schtasks(["/Query", "/TN", _autostart_mod._APP_AUTOSTART_TASK_NAME, "/XML"])
        if rc != 0:
            return False
        # AUTOSTART-CMD-VALIDATE: parse the task XML and verify the
        command_path = _extract_command_from_task_xml(output)
        arguments_text = _extract_arguments_from_task_xml(output) or ""
        try:
            combined = f"{command_path or ''} {arguments_text}"
            if _autostart_mod._is_legacy_stale_autostart_reference(combined):
                log.warning(
                    "[AUTOSTART] Task Scheduler task references legacy runtime, "
                    "reporting as NOT registered (stale task)",
                )
                return False
            if _autostart_mod._references_missing_launcher_script(combined):
                log.warning(
                    "[AUTOSTART] Task Scheduler task references missing launcher, "
                    "reporting as NOT registered (stale task)",
                )
                return False
            if _autostart_mod._launcher_entry_superseded(combined):
                log.warning(
                    "[AUTOSTART] Task Scheduler task targets the Python launcher "
                    "while a packaged Tauri binary is installed, reporting as "
                    "NOT registered (migrates to direct-binary on next sync)",
                )
                return False
        except Exception:
            pass
        if command_path is None:
            # Could not parse the XML, conservatively report True
            log.debug(
                "[AUTOSTART] Could not parse <Command> from task XML, "
                "reporting task as registered (cannot validate path)"
            )
            return True
        if not Path(command_path).exists():
            log.warning(
                "[AUTOSTART] Task Scheduler task exists but its command "
                "path does not exist: %s, reporting as NOT registered "
                "(stale task)",
                command_path,
            )
            return False
        log.debug(
            "[AUTOSTART] Task Scheduler task check: present with valid command: %s (read-only, no write)",
            command_path,
        )
        return True
    except (OSError, subprocess.CalledProcessError, FileNotFoundError):
        log.debug("[PLATFORM] _is_app_autostart_task_registered failed", exc_info=True)
        return False


# The register / unregister / is-registered trio lives in


def _run_key_name() -> str:
    """PLAT-RUN: Return a deterministic registry value name based on install"""
    return f"com.voicetyper.autostart_{_autostart_mod._install_hash()}"


def _validate_runkey_command(value: str) -> bool:
    """Validate that a Run-key command line's exe path exists on disk."""
    if not value or not isinstance(value, str):
        return True  # empty/None, don't claim stale (caller checks truthy)
    # Stale-migration: Electron-era and pip-era shapes can never work
    try:
        if _autostart_mod._is_legacy_stale_autostart_reference(value):
            return False
        if _autostart_mod._references_missing_launcher_script(value):
            return False
        if _autostart_mod._launcher_entry_superseded(value):
            return False
    except Exception:
        pass
    tokens = shlex.split(value, posix=False)
    if not tokens:
        return True  # malformed, don't claim stale
    exe_token = tokens[0]
    exe_path = exe_token.strip('"')
    if not exe_path:
        return True  # malformed, don't claim stale
    # AUTOSTART-QUOTING-FIX: a doubled backslash inside a Windows path
    if is_windows() and "\\\\" in exe_path and not exe_path.startswith("\\\\"):
        return False
    was_quoted = exe_token.startswith('"')
    has_multiple_tokens = len(tokens) > 1
    # CONSERVATIVE-DELETE: only claim stale when we're CERTAIN —
    if not was_quoted and has_multiple_tokens:
        return True  # ambiguous, preserve (can't validate confidently)
    return Path(exe_path).exists()


def _cleanup_stale_runkey_entry(reg_key_name: str) -> None:
    """Best-effort cleanup of a stale HKCU Run-key entry."""
    try:
        import winreg
    except ImportError:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, reg_key_name)
            log.info("[AUTOSTART] Cleaned up stale Run-key entry: %s", reg_key_name)
        except FileNotFoundError:
            pass  # already gone
        finally:
            winreg.CloseKey(key)
    except OSError as exc:
        log.debug(
            "[AUTOSTART] Could not clean up stale Run-key entry %s: %s",
            reg_key_name,
            exc,
        )


# ── Windows Startup-folder .bat naming (mechanism trio in


def _startup_bat_name() -> str:
    """Return the Startup-folder .bat file name (hash-suffixed)."""
    return f"com.voicetyper.autostart{_autostart_mod._install_hash_suffix()}.bat"


def _startup_bat_path() -> Path:
    """Return the full path to the Startup-folder .bat file."""
    return _autostart_mod.get_autostart_dir() / _startup_bat_name()


# These names are genuinely defined in the ``_autostart_windows_*``
from ._autostart_windows_runkey import (  # noqa: E402, F401
    _is_app_autostart_runkey_registered,
    _register_app_autostart_runkey,
    _unregister_app_autostart_runkey,
)
from ._autostart_windows_startup_bat import (  # noqa: E402, F401
    _is_app_autostart_startup_registered,
    _register_app_autostart_startup,
    _unregister_app_autostart_startup,
)
from ._autostart_windows_sweep import (  # noqa: E402, F401
    _entry_targets_this_install,
    _legacy_sweep_marker_path,
    _sweep_legacy_runkeys,
    _sweep_legacy_startup_bats,
    _sweep_legacy_tasks,
    _sweep_v1_marker_files,
    sweep_legacy_autostart_entries,
)
from ._autostart_windows_task import (  # noqa: E402, F401
    _extract_arguments_from_task_xml,
    _extract_command_from_task_xml,
)
from ._autostart_windows_uninstall import (  # noqa: E402, F401
    _unregister_all_voicetyper_runkeys,
    _unregister_all_voicetyper_tasks,
)
