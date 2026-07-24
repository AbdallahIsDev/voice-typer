"""Windows autostart — Task Scheduler + HKCU Run key.

Phase 4.5 / ARCH-045 — extracted from the original
``voice_typer/server/server_platform.py`` god-module.  Implements two
parallel autostart mechanisms on Windows:

  - **HKCU Run key** (preferred — no admin elevation needed).
  - **Task Scheduler with a LogonTrigger** (fallback for the
    locked-task scenario where the Run key fails).

STARTUP-7: the Run key is tried FIRST because it requires no admin
elevation (HKCU is per-user, always writable).  Task Scheduler is tried
only as a fallback because registering a scheduled task may require UAC
elevation if a previous task was created by an admin install (locked
task).  The Run key fires ~33 s after logon, which is soon enough for
the autostart launcher (which has a ``--delay 15`` internal delay).

PLAT-RUN: append the install-path hash to the task name + Run-key name
so two installations in different directories register distinct entries
and don't conflict.  Pre-fix these were fixed strings — two installs
would overwrite each other's entries.  The hash matches the mutex name
hash in ``app.py`` (SHA-256 of ``sys.executable``, first 8 hex chars).

Patch-path compatibility
------------------------
Tests patch several names that this module's functions call at runtime:

  - ``_register_app_autostart_runkey`` / ``_register_app_autostart_task``
    — patched via ``monkeypatch.setattr(server_platform, "X", lambda: ...)``.
    ``_enable_autostart_windows`` looks them up via ``_pkg.X()``.
  - ``_unregister_app_autostart_task`` / ``_unregister_app_autostart_runkey``
    — patched similarly.  ``_disable_autostart_windows`` looks them up via
    ``_pkg.X()``.
  - ``_is_app_autostart_task_registered`` / ``_is_app_autostart_runkey_registered``
    — patched similarly.  ``_is_autostart_windows`` looks them up via
    ``_pkg.X()``.
  - ``_build_app_autostart_task_xml`` — patched via
    ``monkeypatch.setattr(server_platform, "_build_app_autostart_task_xml", lambda: ...)``.
    ``_register_app_autostart_task`` looks it up via ``_pkg.X()``.
  - ``_app_autostart_command_and_args`` — not patched by any test, but
    ``_build_app_autostart_task_xml`` looks it up via ``_pkg.X()`` for
    consistency with the rest of the bridge.
  - ``_autostart_command`` — patched via
    ``monkeypatch.setattr(platform_mod, "_autostart_command", lambda: ...)``.
    ``_register_app_autostart_runkey`` looks it up via ``_pkg.X()``.
  - ``_APP_AUTOSTART_TASK_NAME`` — module-level constant computed in
    ``__init__.py``.  Read via ``_pkg._APP_AUTOSTART_TASK_NAME`` at call
    time so the constant is available by the time any of these functions
    actually executes (it is NOT available at this module's import time
    because ``__init__.py`` is still loading when this file is imported).

``inspect.getsource`` compatibility
-----------------------------------
All twelve functions are genuinely defined here, so
``inspect.getsource(_register_app_autostart_task)`` etc. continue to
read from this file.  Source-string checks for "no redundant
``sys.platform != 'win32'`` check before ``task_scheduler.is_supported()``"
(in :mod:`tests.test_platform_and_config`) read this file's source.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
import sys
from pathlib import Path

# Patch-path bridge: route lookups of the patched names listed in the
# module docstring through the package namespace so test patches of the
# form ``monkeypatch.setattr("voice_typer.server.server_platform.X", ...)``
# keep affecting production code defined here.
from voice_typer.server import server_platform as _pkg
from voice_typer.server.branding import APP_NAME

log = logging.getLogger(__name__)

# STARTUP-7: Task Scheduler logon trigger fires earlier and more
# predictably than HKCU Run keys (which are gated by Windows Explorer's
# startup sequencing). We prefer the Task Scheduler path; HKCU Run key
# remains as a fallback for the locked-task scenario.
#
# PLAT-RUN: append the install-path hash to the task name so two
# installations in different directories register distinct schtasks
# entries and don't conflict. Pre-fix this was a fixed string
# "VoiceTyperAutostart" — two installs would overwrite each other's
# task. The hash matches the mutex name hash in app.py (SHA-256 of
# sys.executable, first 8 hex chars).
#
# NOTE: ``_APP_AUTOSTART_TASK_NAME`` is defined in the package
# ``__init__.py`` (not here) because (a) it depends on
# ``_install_hash_suffix`` (defined in :mod:`.autostart`) which is also
# re-exported by ``__init__.py``, and (b) tests in
# ``tests/regressions/platform_win32_test.py`` do
# ``inspect.getsource(server_platform)`` (which returns the
# ``__init__.py`` source) and assert the literal f-string
# ``f"VoiceTyperAutostart{_install_hash_suffix()}"`` is present.  The
# constant is read at call time via ``_pkg._APP_AUTOSTART_TASK_NAME`` so
# it is available by the time any function below actually executes
# (``__init__.py`` is still loading when this file is first imported).


def _enable_autostart_windows() -> bool:
    """STARTUP-7: register app autostart via HKCU Run key (preferred)
    or Task Scheduler (fallback).

    AUTOSTART-UAC-FIX: The Run key is tried FIRST because it requires
    NO admin elevation (HKCU is per-user, always writable). Task
    Scheduler is tried only as a fallback because registering a
    scheduled task may require UAC elevation if a previous task was
    created by an admin install (locked task). The Run key fires
    ~33 s after logon, which is soon enough for the autostart
    launcher (which has a --delay 15 internal delay).
    """
    # Try HKCU Run key first (no admin elevation needed).
    if _pkg._register_app_autostart_runkey():
        # Clean up any stale Task Scheduler task from a previous install.
        with contextlib.suppress(Exception):
            _pkg._unregister_app_autostart_task()
        return True
    # Fall back to Task Scheduler if the Run key fails.
    log.warning("[CONFIG] HKCU Run key autostart failed; trying Task Scheduler")
    if _pkg._register_app_autostart_task():
        return True
    log.warning("[CONFIG] Both autostart mechanisms failed")
    return False


def _disable_autostart_windows() -> bool:
    """STARTUP-7: remove app autostart from BOTH Task Scheduler and Run key."""
    removed_task = _pkg._unregister_app_autostart_task()
    removed_reg = _pkg._unregister_app_autostart_runkey()
    return removed_task or removed_reg


def _is_autostart_windows() -> bool:
    """STARTUP-7: True if autostart is registered via EITHER mechanism."""
    return _pkg._is_app_autostart_task_registered() or _pkg._is_app_autostart_runkey_registered()


# ── Task Scheduler autostart (preferred) ──────────────────────────────


def _app_autostart_command_and_args() -> tuple[str, str]:
    """Return (pythonw_path, arguments) for the app autostart task.

    STARTUP-7: same launcher + --hidden + --delay <N> as the Run-key path,
    but split into Command + Arguments for the Task Scheduler XML so we
    avoid the cmd.exe wrapper (mirrors the prewarm task fix).

    ADR-0009 Issue 4: the delay was reduced from 30s to 15s (see
    _autostart_command() for the full rationale).

    PLAT-VENV: Uses system Python if running inside a virtualenv.

    PVT-010/WINDOWS-VENV-AUTOSTART: previously the code swapped to the
    system Python unconditionally when running in a venv, WITHOUT
    checking whether the system Python can actually import
    ``voice_typer.server.autostart_launcher``. If the user installed
    Voice Typer only inside the venv, the autostart task would use the
    system Python — which would fail at login with
    ``ModuleNotFoundError`` and silently never start the app. We now
    probe the system Python before swapping; if the probe fails, we
    keep the venv Python (and log a warning) so the autostart entry
    works for the current user.
    """
    from voice_typer.server.task_scheduler import _APP_AUTOSTART_DELAY_SECONDS

    delay_str = str(_APP_AUTOSTART_DELAY_SECONDS)

    launcher = Path(__file__).resolve().parent.parent / "autostart_launcher.py"
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    python_bin = str(pythonw) if pythonw.exists() else sys.executable

    # PLAT-VENV: detect virtualenv and use system Python instead.
    # PVT-010: probe whether the system Python can import
    # voice_typer.server.autostart_launcher BEFORE swapping — if the
    # venv is the only place voice_typer is installed, the system
    # Python would fail at login.
    if sys.prefix != sys.base_prefix:
        import shutil

        system_python = shutil.which("python.exe")
        if system_python:
            from voice_typer.server.server_platform.autostart import (
                _system_python_can_import_launcher,
            )

            if _system_python_can_import_launcher(system_python):
                python_bin = system_python
            else:
                log.warning(
                    "[AUTOSTART] Running inside venv (%s) but system Python "
                    "cannot import voice_typer.server.autostart_launcher "
                    "(probe failed). Keeping venv Python for the Windows "
                    "Task Scheduler entry — autostart will break if the "
                    "venv is deleted, but works for the current user.",
                    sys.executable,
                )
    args = f'"{launcher}" --hidden --delay {delay_str}'
    return python_bin, args


def _build_app_autostart_task_xml() -> str:
    """Build the Task Scheduler XML for the app autostart task.

    STARTUP-7: fires at logon with PT0S delay (Run keys fire ~33 s
    after logon; Task Scheduler logon triggers fire immediately).
    The launcher's ``--delay <N>`` flag gives prewarm a head start on
    warming the OS file cache.

    ADR-0009 Issue 4: the delay was reduced from 30s to 15s (see
    _autostart_command() for the full rationale).
    """
    import xml.etree.ElementTree as ET

    python_exe, arguments = _pkg._app_autostart_command_and_args()

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
    uri.text = f"{_pkg._APP_AUTOSTART_TASK_NAME}"

    triggers = ET.SubElement(root, "Triggers")
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "Enabled").text = "true"
    # PT0S: fire immediately at logon (launcher's --delay <N> handles
    # the prewarm head-start; no Task Scheduler delay needed).
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
    """Register the app autostart Task Scheduler task. Returns True on success.

    Bug fix: removed redundant sys.platform != 'win32' check —
    task_scheduler.is_supported() already handles platform detection
    and is the single source of truth. The redundant check made the
    function untestable on non-Windows without monkeypatching sys.platform.
    """
    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        xml_def = _pkg._build_app_autostart_task_xml()
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False, encoding="utf-8") as tf:
            tf.write(xml_def)
            temp_xml = tf.name
        try:
            with contextlib.suppress(Exception):
                task_scheduler._schtasks(["/Delete", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/F"], capture=True)
            rc, output = task_scheduler._schtasks(
                ["/Create", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
                capture=True,
            )
            if rc != 0 and "access is denied" in (output or "").lower():
                log.info("[CONFIG] Non-elevated schtasks failed — retrying with UAC elevation prompt")
                rc, output = task_scheduler._schtasks_elevated(
                    ["/Create", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/XML", temp_xml, "/F"],
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
        rc, output = task_scheduler._schtasks(["/Delete", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/F"], capture=True)
        if rc == 0:
            log.info("[CONFIG] App autostart Task Scheduler task removed")
            return True
        # already absent
        return "cannot find" in output.lower() or "does not exist" in output.lower()
    except Exception as e:
        log.warning("[CONFIG] Task Scheduler autostart removal raised: %s", e)
        return False


def _is_app_autostart_task_registered() -> bool:
    """True if the app autostart Task Scheduler task exists.

    Bug fix: removed redundant sys.platform != 'win32' check.
    """
    # RW-6 (pyrefly): import subprocess BEFORE the try block so the
    # ``except subprocess.CalledProcessError`` clause has a guaranteed-bound
    # name (matches the pattern at line ~826).
    import subprocess

    try:
        from voice_typer.server import task_scheduler

        if not task_scheduler.is_supported():
            return False
        rc, _ = task_scheduler._schtasks(["/Query", "/TN", _pkg._APP_AUTOSTART_TASK_NAME, "/XML"])
        return rc == 0
    except (OSError, subprocess.CalledProcessError, FileNotFoundError):
        log.debug("[PLATFORM] _is_app_autostart_task_registered failed", exc_info=True)
        return False


# ── HKCU Run-key autostart (fallback) ─────────────────────────────────


def _run_key_name() -> str:
    """PLAT-RUN: Return a deterministic registry key name based on install path.

    Uses a hash of sys.executable so different installs (e.g. stable vs
    dev) don't conflict, and stale entries from removed installs can be
    cleaned up.
    """
    import hashlib

    install_hash = hashlib.sha256(sys.executable.encode()).hexdigest()[:8]
    return f"VoiceTyper_{install_hash}"


def _register_app_autostart_runkey() -> bool:
    """Register app autostart via HKCU Run key (admin-free fallback)."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: Use deterministic key name based on install path
    # to prevent conflicting entries from different installs
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            cmd = _pkg._autostart_command()
            winreg.SetValueEx(key, reg_key_name, 0, winreg.REG_SZ, cmd)
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart enabled via HKCU Run key (fallback): %s", cmd)

        # PLAT-RUN: Clean stale entries whose path no longer exists.
        #
        # DE-67: parse the Run-key command line with a Windows-aware
        # splitter before extracting the exe path. Pre-fix, the code did
        # ``value.strip('"').split('"')[0] if '"' in value else value.split()[0]``
        # which misparses UNQUOTED spaced paths (e.g.
        # ``C:\Program Files\VoiceTyper\app.exe --delay 15``) — the
        # ``value.split()[0]`` branch returns ``C:\Program`` (NOT a real
        # path), ``Path('C:\\Program').exists()`` is False, and the
        # cleanup silently DELETES the other install's Run-key entry.
        # This breaks multi-install autostart (a PLAT-RUN supported
        # scenario) when any install lives in a spaced path (common:
        # ``C:\Program Files\...``).
        #
        # ``shlex.split(value, posix=False)`` parses a Windows-style
        # command line: it preserves backslashes, treats double quotes
        # as argument delimiters (the quoted token is returned as a
        # single element WITH the surrounding quotes preserved), and
        # splits on whitespace outside quotes. The first token is the
        # exe path (quoted or not); we strip the surrounding quotes to
        # get the actual filesystem path.
        #
        # CONSERVATIVE-DELETE policy: an UNQUOTED value with multiple
        # tokens (spaces in the command line) is ambiguous — the actual
        # exe path might be a longer space-separated prefix that we
        # can't recover without quotes. For such entries, we DO NOT
        # delete even if the first token doesn't exist as a file,
        # because deleting a legitimate entry is worse than leaving a
        # stale one in the registry. We only delete when we're CERTAIN
        # the entry is stale:
        #   - quoted path that doesn't exist (unambiguous), OR
        #   - unquoted single-token path that doesn't exist (unambiguous).
        #
        # Note: ``shlex.split(value, posix=False)`` is the documented
        # cross-platform-safe Windows-command-line splitter that does
        # NOT require the Windows-only ``shell32.CommandLineToArgvW``
        # — which keeps this code testable on non-Windows CI.
        try:
            run_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS
            )
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(run_key, i)
                    if name.startswith("VoiceTyper") and name != reg_key_name and isinstance(value, str):
                        # DE-67: use shlex.split(posix=False) so quoted
                        # spaced paths are parsed correctly (the quoted
                        # token is a single element). For unquoted
                        # spaced paths, the parse is inherently ambiguous
                        # — see the CONSERVATIVE-DELETE policy above.
                        tokens = shlex.split(value, posix=False)
                        if not tokens:
                            # Malformed / empty value — skip cleanup.
                            i += 1
                            continue
                        exe_token = tokens[0]
                        # shlex.split(posix=False) preserves the
                        # surrounding quotes in the token; strip them so
                        # we get the actual filesystem path.
                        exe_path = exe_token.strip('"')
                        if not exe_path:
                            # Malformed entry (e.g. just quotes) — skip.
                            i += 1
                            continue
                        was_quoted = exe_token.startswith('"')
                        has_multiple_tokens = len(tokens) > 1
                        path_exists = Path(exe_path).exists()
                        if not path_exists:
                            # Only delete if we're CERTAIN the entry is
                            # stale (see CONSERVATIVE-DELETE policy).
                            # Ambiguous unquoted spaced paths are
                            # preserved (never deleted) to avoid
                            # breaking legitimate multi-install autostart.
                            if was_quoted or not has_multiple_tokens:
                                winreg.DeleteValue(run_key, name)
                                log.info("[AUTOSTART] Removed stale entry: %s", name)
                                continue
                            # else: ambiguous unquoted spaced path —
                            # be conservative, skip deletion.
                            log.debug(
                                "[AUTOSTART] Skipping ambiguous unquoted "
                                "spaced-path entry (cannot determine if "
                                "stale): %s",
                                name,
                            )
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(run_key)
        except Exception:
            log.debug("[AUTOSTART] registry Run-key cleanup failed", exc_info=True)

        return True
    except OSError as e:
        log.warning("[CONFIG] HKCU Run key autostart failed: %s", e)
        return False


def _unregister_app_autostart_runkey() -> bool:
    """Remove app autostart from HKCU Run key."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, reg_key_name)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart removed from HKCU Run key")
        return True
    except OSError:
        return False


def _is_app_autostart_runkey_registered() -> bool:
    """True if the HKCU Run key has the VoiceTyper entry."""
    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, reg_key_name)
            return bool(val)
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError:
        return False
